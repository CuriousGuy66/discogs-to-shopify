"""
Smoke test: create a draft product via Shopify Admin API and delete it.
Uses REST endpoints: POST /products.json then DELETE /products/{id}.json
"""

import os
import sys
import time

from core.clients.shopify import ShopifyClient
from core.exporters.shopify_api_exporter import ShopifyAPIExporter
from core.models import ShopifyDraft


def read_token() -> str:
    token = os.getenv("SHOPIFY_ADMIN_TOKEN")
    if token:
        return token.strip()
    return input("Paste your Admin API Access Token: ").strip()


def main() -> int:
    shop = os.getenv("SHOPIFY_STORE_DOMAIN", "a908bf-3.myshopify.com")
    api_version = os.getenv("SHOPIFY_API_VERSION", "2025-01")
    token = read_token()
    if not token:
        print("No token provided.", file=sys.stderr)
        return 1

    client = ShopifyClient(store_domain=shop, access_token=token, api_version=api_version)
    exporter = ShopifyAPIExporter(client=client, publish=False, dry_run=False)

    # Unique title to avoid collisions
    ts = int(time.time())
    title = f"API Smoke Test {ts}"

    draft = ShopifyDraft(
        handle="",
        title=title,
        body_html="<strong>Smoke test product</strong>",
        vendor="SmokeTest",
        product_type="Test",
        product_category="",
        tags=["smoketest"],
        price=0.01,
        metafields={"source": "smoke_test"},
        images=[],  # add image URLs if desired
        collections=[],
    )

    print(f"Creating draft product: {title}")
    try:
        exporter.write_product(draft)
        if exporter.created_ids:
            product_id = exporter.created_ids[-1]
            print(f"Created product id: {product_id}")
            print(f"Deleting product id: {product_id}")
            client.delete_product(product_id)
            print("Delete succeeded. Smoke test complete.")
        else:
            print("No product ID recorded; creation may have failed.", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"Error during create/delete: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
