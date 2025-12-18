"""
Assign Shopify product variants to a delivery (shipping) profile.

Defaults:
- Domain: a908bf-3.myshopify.com (hard-coded per shop)
- API version: 2025-01
- Record detection: product_type == "Vinyl Record"

Usage examples:
  # Dry run with defaults (product_type="Vinyl Record", profile name lookup)
  python scripts/data_repair/assign_shipping_profile.py --mode dry-run

  # Apply to a known profile id
  python scripts/data_repair/assign_shipping_profile.py --profile-id gid://shopify/DeliveryProfile/... --mode apply

Requirements:
- Env var SHOPIFY_ADMIN_TOKEN must be set (Admin API access token).
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, Iterable, List, Optional, Tuple

import requests


STORE_DOMAIN = "a908bf-3.myshopify.com"
API_VERSION = "2025-01"
DEFAULT_PROFILE_NAME = "Books and Media"
DEFAULT_FILTER_TYPE = "product_type"
DEFAULT_FILTER_VALUE = "Vinyl Record"
DEFAULT_LOCATION_GROUP_ID = None  # can override via CLI; falls back to first group on profile


def graphql_request(
    query: str, variables: Dict[str, object], token: str, timeout: int = 30
) -> Dict[str, object]:
    url = f"https://{STORE_DOMAIN}/admin/api/{API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    resp = requests.post(
        url, headers=headers, json={"query": query, "variables": variables}, timeout=timeout
    )
    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise RuntimeError(f"GraphQL HTTP {resp.status_code}: {detail}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"GraphQL response parse failed: {exc}") from exc
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload.get("data") or {}


def find_delivery_profile_id(profile_name: str, token: str) -> Optional[str]:
    """Return the first profile id whose name matches (case-insensitive)."""
    query = """
    query ($first: Int!, $after: String) {
      deliveryProfiles(first: $first, after: $after) {
        nodes {
          id
          name
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
    """
    after = None
    first = 50
    while True:
        data = graphql_request(query, {"first": first, "after": after}, token)
        profiles = data.get("deliveryProfiles", {})
        for node in profiles.get("nodes", []):
            if node.get("name", "").strip().lower() == profile_name.strip().lower():
                return node.get("id")
        page_info = profiles.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
    return None


def build_product_query(filter_type: str, filter_value: str) -> str:
    if filter_type == "tag":
        return f"tag:{filter_value}"
    if filter_type == "product_type":
        # product_type filter uses productType: term
        return f'product_type:"{filter_value}"'
    raise ValueError(f"Unsupported filter_type: {filter_type}")


def fetch_record_variants(
    token: str,
    filter_type: str,
    filter_value: str,
    max_products: Optional[int] = None,
) -> Tuple[List[str], List[str]]:
    """
    Return variant ids and sample product handles for logging.
    """
    query = """
    query ($first: Int!, $after: String, $query: String!) {
      products(first: $first, after: $after, query: $query) {
        edges {
          node {
            id
            handle
            title
            variants(first: 100) {
              edges { node { id title sku } }
            }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """
    search = build_product_query(filter_type, filter_value)
    after = None
    first = 50
    variants: List[str] = []
    sample_handles: List[str] = []
    total_products = 0

    while True:
        data = graphql_request(query, {"first": first, "after": after, "query": search}, token)
        products = data.get("products", {})
        edges = products.get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            total_products += 1
            handle = node.get("handle")
            if handle and len(sample_handles) < 10:
                sample_handles.append(handle)
            variant_edges = (node.get("variants") or {}).get("edges") or []
            for v_edge in variant_edges:
                v_node = v_edge.get("node") or {}
                vid = v_node.get("id")
                if vid:
                    variants.append(vid)
        if max_products and total_products >= max_products:
            break
        page_info = products.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")

    return variants, sample_handles


def chunked(items: Iterable[str], size: int) -> Iterable[List[str]]:
    batch: List[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def assign_variants_to_profile(
    profile_id: str, variant_ids: List[str], token: str
) -> List[Dict[str, object]]:
    """
    Use deliveryProfileUpdate (variantsToAssociate) to attach variants to the profile.
    Returns list of userErrors (empty on success).
    """
    mutation = """
    mutation deliveryProfileUpdate($id: ID!, $profile: DeliveryProfileInput!) {
      deliveryProfileUpdate(id: $id, profile: $profile) {
        profile { id name }
        userErrors { field message }
      }
    }
    """
    errors: List[Dict[str, object]] = []
    for batch in chunked(variant_ids, 80):  # keep well under typical GraphQL input limits
        profile_input = {"variantsToAssociate": batch}
        data = graphql_request(
            mutation,
            {"id": profile_id, "profile": profile_input},
            token,
            timeout=40,
        )
        payload = data.get("deliveryProfileUpdate") or {}
        batch_errors = payload.get("userErrors") or []
        errors.extend(batch_errors)
    return errors


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assign product variants to a Shopify delivery (shipping) profile."
    )
    parser.add_argument(
        "--profile-id",
        help="Delivery profile ID (gid://shopify/DeliveryProfile/...). If omitted, will look up by name.",
    )
    parser.add_argument(
        "--profile-name",
        default=DEFAULT_PROFILE_NAME,
        help=f"Profile name to look up when --profile-id is not provided. Default: {DEFAULT_PROFILE_NAME!r}",
    )
    parser.add_argument(
        "--filter-type",
        choices=["product_type", "tag"],
        default=DEFAULT_FILTER_TYPE,
        help=f"How to detect record products. Default: {DEFAULT_FILTER_TYPE}",
    )
    parser.add_argument(
        "--filter-value",
        default=DEFAULT_FILTER_VALUE,
        help=f"Value for the selected filter. Default: {DEFAULT_FILTER_VALUE!r}",
    )
    parser.add_argument(
        "--mode",
        choices=["dry-run", "apply"],
        default="dry-run",
        help="dry-run: report counts only. apply: perform assignment.",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        help="Optional limit on number of products to scan (for testing).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    token = os.getenv("SHOPIFY_ADMIN_TOKEN", "").strip()
    if not token:
        print("Missing SHOPIFY_ADMIN_TOKEN env var.", file=sys.stderr)
        return 1

    profile_id = args.profile_id
    if not profile_id:
        profile_id = find_delivery_profile_id(args.profile_name, token)
        if not profile_id:
            print(
                f"Profile named {args.profile_name!r} not found. "
                "Pass --profile-id or create the profile first.",
                file=sys.stderr,
            )
            return 1

    print(f"Store: {STORE_DOMAIN} (API {API_VERSION})")
    print(f"Target profile: {profile_id} (name={args.profile_name!r})")
    print(
        f"Filter: {args.filter_type} = {args.filter_value!r} | Mode: {args.mode} "
        f"| Max products: {args.max_products or 'all'}"
    )

    variants, sample_handles = fetch_record_variants(
        token=token,
        filter_type=args.filter_type,
        filter_value=args.filter_value,
        max_products=args.max_products,
    )
    print(f"Found variants: {len(variants)}")
    if sample_handles:
        print(f"Sample product handles (up to 10): {', '.join(sample_handles)}")

    if not variants:
        print("No variants matched filter; nothing to do.")
        return 0

    if args.mode == "dry-run":
        print("Dry run complete. No changes sent.")
        return 0

    errors = assign_variants_to_profile(profile_id, variants, token)
    if errors:
        print("Assignment completed with userErrors:", file=sys.stderr)
        for err in errors:
            print(err, file=sys.stderr)
        return 2

    print("Assignment applied successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
