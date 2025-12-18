#!/usr/bin/env python3
"""
Quick Shopify product category inspector.
Hard-coded store domain: a908bf-3.myshopify.com
Usage:
  python check_shopify_category.py <access_token> <handle>
"""
import os
import sys
import requests


STORE_DOMAIN = "a908bf-3.myshopify.com"
API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2025-01")


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "Usage: python check_shopify_category.py <access_token> [handle]\n"
            "Example: python check_shopify_category.py shpat_xxx mary-jayne-gaetke-my-wonderful-lord-1976"
        )
        return 1

    access_token = sys.argv[1]
    handle = sys.argv[2] if len(sys.argv) >= 3 else input("Product handle: ").strip()
    if not handle:
        print("No handle provided.")
        return 1
    url = f"https://{STORE_DOMAIN}/admin/api/{API_VERSION}/graphql.json"
    query = """
    query ($handle: String!) {
      productByHandle(handle: $handle) {
        id
        title
        handle
        productCategory {
          productTaxonomyNode {
            id
            fullName
          }
        }
        standardProductType {
          value
          productTaxonomyNode {
            id
            fullName
          }
        }
      }
    }
    """
    variables = {"handle": handle}
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }
    resp = requests.post(
        url, headers=headers, json={"query": query, "variables": variables}, timeout=20
    )
    print(f"Status: {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        print(resp.text)
        return 1

    if "errors" in data:
        print("Errors:", data["errors"])
        return 1

    prod = data.get("data", {}).get("productByHandle")
    if not prod:
        print("Product not found for handle:", handle)
        return 1

    print("Title:", prod.get("title"))
    cat_node = (prod.get("productCategory") or {}).get("productTaxonomyNode") or {}
    print("productCategory.productTaxonomyNode.id:", cat_node.get("id"))
    print("productCategory.productTaxonomyNode.fullName:", cat_node.get("fullName"))

    spt = prod.get("standardProductType") or {}
    spt_node = spt.get("productTaxonomyNode") or {}
    print("standardProductType.value:", spt.get("value"))
    print("standardProductType.productTaxonomyNode.id:", spt_node.get("id"))
    print("standardProductType.productTaxonomyNode.fullName:", spt_node.get("fullName"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
