"""
Shopify Admin API connectivity test using GraphQL (/graphql.json).
Prompts for the Admin API token and fetches shop info.
"""

import os
import sys
import requests


def read_token() -> str:
    token = os.getenv("SHOPIFY_ADMIN_TOKEN")
    if token:
        return token.strip()
    return input("Paste your Admin API Access Token: ").strip()


def main() -> int:
    shop = os.getenv("SHOPIFY_STORE_DOMAIN", "a908bf-3.myshopify.com")
    api_version = "2025-01"  # per Shopify's example

    token = read_token()
    if not token:
        print("No token provided.", file=sys.stderr)
        return 1

    url = f"https://{shop}/admin/api/{api_version}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    query = """
    {
      shop {
        name
        email
        primaryDomain {
          url
        }
      }
    }
    """

    print(f"Testing: {url}")
    try:
        resp = requests.post(url, headers=headers, json={"query": query}, timeout=20)
        if resp.status_code != 200:
            print(f"Request failed. Status: {resp.status_code}", file=sys.stderr)
            try:
                print(resp.json(), file=sys.stderr)
            except Exception:
                print(resp.text, file=sys.stderr)
            return 1

        data = resp.json()
        if "errors" in data:
            print("GraphQL errors:", data["errors"], file=sys.stderr)
            return 1

        shop_info = data.get("data", {}).get("shop", {})
        if not shop_info:
            print("No shop data returned.", file=sys.stderr)
            return 1

        print("Authentication successful!")
        print(f"Shop Name: {shop_info.get('name')}")
        print(f"Shop Email: {shop_info.get('email')}")
        primary_domain = shop_info.get("primaryDomain") or {}
        print(f"Primary Domain: {primary_domain.get('url')}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
