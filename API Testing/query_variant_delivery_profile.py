"""
Quick helper to check a product variant's delivery profile by handle.

Usage:
  # set your Admin token in env
  set SHOPIFY_ADMIN_TOKEN=...
  # run the script
  python "API Testing/query_variant_delivery_profile.py" --handle revivaltime-choir-the-weve-a-story-to-tell-1969-word
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import requests


def query_variant_profile(handle: str, token: str, store: str = "a908bf-3.myshopify.com") -> dict:
    graphql = """
    query VariantProfile($handle: String!) {
      productByHandle(handle: $handle) {
        id
        title
        productType
        category { id fullName }
        variants(first: 10) {
          nodes {
            id
            title
            sku
            deliveryProfile { id name }
          }
        }
      }
    }
    """
    payload = {"query": graphql, "variables": {"handle": handle}}
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    url = f"https://{store}/admin/api/2025-01/graphql.json"
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        raise SystemExit(f"HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("errors"):
        raise SystemExit(f"GraphQL errors: {data['errors']}")
    return data.get("data") or {}


def fetch_handle_from_rest(product_id: str, token: str, store: str) -> str:
    """
    Fetch handle via REST /products/{id}.json given numeric ID.
    """
    url = f"https://{store}/admin/api/2025-01/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": token}
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        raise SystemExit(f"REST HTTP {resp.status_code}: {resp.text}")
    try:
        data = resp.json().get("product") or {}
    except Exception as exc:
        raise SystemExit(f"REST parse error: {exc}")
    handle = data.get("handle")
    if not handle:
        raise SystemExit("No handle found on product response.")
    return handle


def query_variant_profile_by_gid(product_gid: str, token: str, store: str) -> dict:
    """
    Query product + variants by GraphQL product GID directly (no handle).
    """
    graphql = """
    query VariantProfileById($id: ID!) {
      product(id: $id) {
        id
        title
        productType
        handle
        category { id fullName }
        variants(first: 10) {
          nodes {
            id
            title
            sku
            deliveryProfile { id name }
          }
        }
      }
    }
    """
    payload = {"query": graphql, "variables": {"id": product_gid}}
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    url = f"https://{store}/admin/api/2025-01/graphql.json"
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        raise SystemExit(f"HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("errors"):
        raise SystemExit(f"GraphQL errors: {data['errors']}")
    return data.get("data") or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Shopify variant delivery profile by product handle."
    )
    parser.add_argument("--handle", help="Product handle to query.")
    parser.add_argument("--product-id", help="Numeric product id (REST) to resolve handle.")
    parser.add_argument("--product-gid", help="GraphQL product gid to query directly.")
    parser.add_argument(
        "--store",
        default="a908bf-3.myshopify.com",
        help="Shopify store domain (default: a908bf-3.myshopify.com)",
    )
    args = parser.parse_args(argv)

    token = os.getenv("SHOPIFY_ADMIN_TOKEN", "").strip()
    if not token:
        print("Missing SHOPIFY_ADMIN_TOKEN env var.", file=sys.stderr)
        return 1

    # Determine lookup mode
    if args.product_gid:
        data = query_variant_profile_by_gid(args.product_gid, token, store=args.store)
    else:
        handle = args.handle
        if not handle:
            if not args.product_id:
                print("Provide --handle, --product-id, or --product-gid.", file=sys.stderr)
                return 1
            handle = fetch_handle_from_rest(args.product_id, token, store=args.store)
            print(f"Resolved handle from product_id {args.product_id}: {handle}")
        data = query_variant_profile(handle, token, store=args.store)

    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
