"""
Assign a shipping package (from Shipping & Delivery > Packages) to product variants.

This sets inventoryItem.measurement.shippingPackageId, which controls the
"Package" dropdown shown in the Shopify product UI.

Usage examples (PowerShell):
  $env:SHOPIFY_ADMIN_TOKEN="..."
  python "API Testing/assign_shipping_package.py" ^
    --package-id gid://shopify/ShippingPackage/XXXXXXXXXX ^
    --handle some-product-handle

  # Using product id from admin URL
  python "API Testing/assign_shipping_package.py" ^
    --package-id gid://shopify/ShippingPackage/XXXXXXXXXX ^
    --product-id 9292885098804

  # Update weight too
  python "API Testing/assign_shipping_package.py" ^
    --package-id gid://shopify/ShippingPackage/XXXXXXXXXX ^
    --handle some-product-handle ^
    --weight-value 1.0 --weight-unit POUNDS
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

import requests

API_VERSION = "2025-10"  # use newer API version that exposes shippingPackageId

def graphql_request(store: str, token: str, query: str, variables: Dict[str, object]) -> Dict[str, object]:
    url = f"https://{store}/admin/api/{API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json={"query": query, "variables": variables}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"GraphQL HTTP {resp.status_code}: {resp.text}")
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"GraphQL parse error: {exc}")
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data") or {}


def fetch_handle_from_rest(store: str, token: str, product_id: str) -> str:
    url = f"https://{store}/admin/api/{API_VERSION}/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": token}
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"REST HTTP {resp.status_code}: {resp.text}")
    try:
        prod = resp.json().get("product") or {}
    except Exception as exc:
        raise RuntimeError(f"REST parse error: {exc}")
    handle = prod.get("handle")
    if not handle:
        raise RuntimeError("No handle found on product response.")
    return handle


def fetch_variants(
    store: str, token: str, handle: Optional[str] = None, product_gid: Optional[str] = None
) -> Tuple[str, List[Dict[str, str]]]:
    """
    Return product title and list of {variant_id, sku, inventory_item_id, current_package_id}
    """
    if not handle and not product_gid:
        raise ValueError("handle or product_gid required")

    query_by_handle = """
    query ($handle: String!, $first: Int!, $after: String) {
      productByHandle(handle: $handle) {
        id
        title
        variants(first: $first, after: $after) {
          edges {
            cursor
            node {
              id
              sku
              inventoryItem {
                id
                measurement { weight { value unit } }
              }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """
    query_by_id = """
    query ($id: ID!, $first: Int!, $after: String) {
      product(id: $id) {
        id
        title
        variants(first: $first, after: $after) {
          edges {
            cursor
            node {
              id
              sku
              inventoryItem {
                id
                measurement { weight { value unit } }
              }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """
    first = 100
    after = None
    variants: List[Dict[str, str]] = []
    title = ""
    while True:
        if handle:
            data = graphql_request(
                store,
                token,
                query_by_handle,
                {"handle": handle, "first": first, "after": after},
            )
            product = data.get("productByHandle") or {}
        else:
            data = graphql_request(
                store,
                token,
                query_by_id,
                {"id": product_gid, "first": first, "after": after},
            )
            product = data.get("product") or {}
        if not product:
            raise RuntimeError("Product not found.")
        title = product.get("title") or ""
        edges = (product.get("variants") or {}).get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            inv = (node.get("inventoryItem") or {})
            meas = inv.get("measurement") or {}
            variants.append(
                {
                    "variant_id": node.get("id"),
                    "sku": node.get("sku"),
                    "inventory_item_id": inv.get("id"),
                    "current_package_id": meas.get("shippingPackageId"),  # may be absent on this schema
                }
            )
        page_info = (product.get("variants") or {}).get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
    return title, variants


def update_inventory_item_package(
    store: str,
    token: str,
    inventory_item_id: str,
    package_id: str,
    weight_value: Optional[float] = None,
    weight_unit: Optional[str] = None,
) -> Dict[str, object]:
    mutation = """
    mutation UpdateInventoryItemPackaging($inventoryItemId: ID!, $shippingPackageId: ID!, $weight: WeightInput) {
      inventoryItemUpdate(
        id: $inventoryItemId
        input: {
          measurement: {
            shippingPackageId: $shippingPackageId
            weight: $weight
          }
        }
      ) {
        inventoryItem {
          id
          measurement {
            weight { value unit }
          }
        }
        userErrors { field message }
      }
    }
    """
    weight_input: Optional[Dict[str, object]] = None
    if weight_value is not None and weight_unit:
        weight_input = {"value": weight_value, "unit": weight_unit}
    variables: Dict[str, object] = {
        "inventoryItemId": inventory_item_id,
        "shippingPackageId": package_id,
        "weight": weight_input,
    }
    data = graphql_request(store, token, mutation, variables)
    return data.get("inventoryItemUpdate") or {}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assign a shipping package to product variants (sets inventoryItem.measurement.shippingPackageId)."
    )
    parser.add_argument("--handle", help="Product handle.")
    parser.add_argument("--product-id", help="Numeric product id (REST) to resolve handle.")
    parser.add_argument("--product-gid", help="Product gid://shopify/Product/... to query directly.")
    parser.add_argument("--package-id", required=True, help="Shipping package gid to assign.")
    parser.add_argument("--store", default="a908bf-3.myshopify.com", help="Store domain.")
    parser.add_argument("--weight-value", type=float, help="Optional packaged weight value.")
    parser.add_argument(
        "--weight-unit",
        choices=["GRAMS", "KILOGRAMS", "OUNCES", "POUNDS"],
        help="Optional packaged weight unit (required if weight-value set).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only; no writes.")
    args = parser.parse_args(argv)

    token = os.getenv("SHOPIFY_ADMIN_TOKEN", "").strip()
    if not token:
        print("Missing SHOPIFY_ADMIN_TOKEN env var.", file=sys.stderr)
        return 1

    handle = args.handle
    if not handle and args.product_id:
        handle = fetch_handle_from_rest(args.store, token, args.product_id)
        print(f"Resolved handle from product_id {args.product_id}: {handle}")

    if not handle and not args.product_gid:
        print("Provide --handle, --product-id, or --product-gid.", file=sys.stderr)
        return 1

    if args.weight_value is not None and not args.weight_unit:
        print("When providing --weight-value, also provide --weight-unit.", file=sys.stderr)
        return 1

    title, variants = fetch_variants(args.store, token, handle=handle, product_gid=args.product_gid)
    print(f"Product: {title} | variants: {len(variants)}")
    for v in variants:
        print(
            f"- Variant {v['variant_id']} sku={v['sku']} current_package={v['current_package_id']}"
        )

    if args.dry_run:
        print("Dry run: no updates sent.")
        return 0

    for v in variants:
        inv_id = v["inventory_item_id"]
        if not inv_id:
            print(f"Skipping variant {v['variant_id']} (no inventory item id).", file=sys.stderr)
            continue
        try:
            res = update_inventory_item_package(
                store=args.store,
                token=token,
                inventory_item_id=inv_id,
                package_id=args.package_id,
                weight_value=args.weight_value,
                weight_unit=args.weight_unit,
            )
            user_errors = res.get("userErrors") or []
            if user_errors:
                print(f"Variant {v['variant_id']} errors: {user_errors}", file=sys.stderr)
            else:
                meas = ((res.get("inventoryItem") or {}).get("measurement")) or {}
                print(
                    f"Updated variant {v['variant_id']} inventory_item {inv_id} -> package {meas.get('shippingPackageId')} weight={meas.get('weight')}"
                )
        except Exception as exc:
            print(f"Variant {v['variant_id']} update failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
