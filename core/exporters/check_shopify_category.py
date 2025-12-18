#!/usr/bin/env python3
import os
import sys
import requests

def main():
    if len(sys.argv) < 4:
        print("Usage: python check_shopify_category.py <store_domain> <access_token> <handle>")
        print("Example: python check_shopify_category.py a908bf-3.myshopify.com shpat_xxx mary-jayne-gaetke-my-wonderful-lord-1976")
        sys.exit(1)

    store_domain, access_token, handle = sys.argv[1], sys.argv[2], sys.argv[3]
    api_version = os.getenv("SHOPIFY_API_VERSION", "2025-01")
    url = f"https://{store_domain}/admin/api/{api_version}/graphql.json"

    query = """
    query ($handle: String!) {
      productByHandle(handle: $handle) {
        id
        title
        handle
        # Legacy taxonomy node path
        productCategory {
          productTaxonomyNode {
            id
            fullName
          }
        }
        # New category field (2024-07+)
        category {
          id
          fullName
        }
        # standardizedProductType field exists on Product; value/legacy fields do not.
        standardizedProductType {
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
    resp = requests.post(url, headers=headers, json={"query": query, "variables": variables}, timeout=20)
    print(f"Status: {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        print(resp.text)
        sys.exit(1)

    if "errors" in data:
        print("Errors:", data["errors"])
        # Keep going to show any partial data Shopify returned

    prod = data.get("data", {}).get("productByHandle")
    if not prod:
        # Try a fallback query search if handle lookup fails (drafts or deleted)
        search_query = """
        query ($query: String!, $first: Int!) {
          products(first: $first, query: $query) {
            edges {
              node {
                id
                title
                handle
                productCategory {
                  productTaxonomyNode {
                    id
                    fullName
                  }
                }
                standardizedProductType {
                  productTaxonomyNode {
                    id
                    fullName
                  }
                }
              }
            }
          }
        }
        """
        resp = requests.post(
            url,
            headers=headers,
            json={"query": search_query, "variables": {"query": f"handle:{handle}", "first": 1}},
            timeout=20,
        )
        try:
            search_data = resp.json()
        except Exception:
            search_data = {}
        edges = (
            search_data.get("data", {})
            .get("products", {})
            .get("edges", [])
        )
        if edges:
            prod = edges[0].get("node")
        if not prod:
            print("Product not found for handle (handle lookup and search failed):", handle)
            sys.exit(1)

    print("Title:", prod.get("title"))
    cat = prod.get("productCategory", {}) or {}
    cat_node = cat.get("productTaxonomyNode") or {}
    print("productCategory.productTaxonomyNode.id:", cat_node.get("id"))
    print("productCategory.productTaxonomyNode.fullName:", cat_node.get("fullName"))
    new_cat = prod.get("category") or {}
    print("category.id:", new_cat.get("id"))
    print("category.fullName:", new_cat.get("fullName"))

    spt = prod.get("standardizedProductType", {}) or {}
    spt_node = spt.get("productTaxonomyNode") or {}
    print("standardizedProductType.productTaxonomyNode.id:", spt_node.get("id"))
    print("standardizedProductType.productTaxonomyNode.fullName:", spt_node.get("fullName"))

if __name__ == "__main__":
    main()
