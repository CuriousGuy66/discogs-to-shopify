"""
List Shopify shipping packages (Shipping & Delivery > Packages) via Admin GraphQL.

Attempts 2025-10 first, then unstable if needed.

Usage (PowerShell):
  $env:SHOPIFY_ADMIN_TOKEN="..."
  python "API Testing/get_shipping_packages.py"

Options:
  --store <domain>         Default: a908bf-3.myshopify.com
  --api-version <version>  Override version (e.g., 2025-10 or unstable). If omitted,
                           the script tries 2025-10, then unstable on failure.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

import requests


def graphql_request(store: str, token: str, query: str, variables: Dict[str, object], api_version: str) -> Dict[str, object]:
    url = f"https://{store}/admin/api/{api_version}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json={"query": query, "variables": variables}, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"Response parse error: {exc}")
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data") or {}


def fetch_packages(store: str, token: str, api_version: str) -> List[Dict[str, str]]:
    query = """
    query Packages($first: Int!, $after: String) {
      shippingPackages(first: $first, after: $after) {
        nodes { id name }
        pageInfo { hasNextPage endCursor }
      }
    }
    """
    first = 50
    after: Optional[str] = None
    packages: List[Dict[str, str]] = []
    while True:
        data = graphql_request(store, token, query, {"first": first, "after": after}, api_version)
        sp = data.get("shippingPackages") or {}
        nodes = sp.get("nodes") or []
        packages.extend(nodes)
        page_info = sp.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
    return packages


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="List Shopify shipping packages via Admin GraphQL.")
    parser.add_argument("--store", default="a908bf-3.myshopify.com", help="Store domain.")
    parser.add_argument("--api-version", help="API version to use (e.g., 2025-10 or unstable).")
    args = parser.parse_args(argv)

    token = os.getenv("SHOPIFY_ADMIN_TOKEN", "").strip()
    if not token:
        print("Missing SHOPIFY_ADMIN_TOKEN env var.", file=sys.stderr)
        return 1

    versions_to_try = [args.api_version] if args.api_version else ["2025-10", "unstable"]

    last_error: Optional[str] = None
    for ver in versions_to_try:
        try:
            packages = fetch_packages(args.store, token, ver)
            print(f"API version {ver}: found {len(packages)} packages")
            for pkg in packages:
                print(f"- {pkg.get('name')} :: {pkg.get('id')}")
            return 0
        except Exception as exc:
            last_error = f"{ver}: {exc}"
            continue

    print(f"Failed to list packages. Last error: {last_error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
