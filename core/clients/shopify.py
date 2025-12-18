from __future__ import annotations

import time
from typing import Any, Dict, Optional
import logging

import requests

from core.models import ShopifyDraft

logger = logging.getLogger(__name__)

class ShopifyClient:
    """Minimal Shopify Admin API REST client for product creation."""

    def __init__(
        self,
        store_domain: str,
        access_token: str,
        api_version: str = "2025-01",
        session: Optional[requests.Session] = None,
        calls_per_second: float = 2.0,
    ) -> None:
        self.store_domain = store_domain
        self.access_token = access_token
        self.api_version = api_version
        self.session = session or requests.Session()
        # Conservative rate: ~2 calls/second is Shopify's bucket; we default to 1.5.
        self.min_interval = 1.0 / max(1.0, min(calls_per_second, 2.0))
        self._last_call_ts = 0.0

    def _sleep_for_rate_limit(self) -> None:
        now = time.time()
        elapsed = now - self._last_call_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_ts = time.time()

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"https://{self.store_domain}/admin/api/{self.api_version}/{path.lstrip('/')}"

    def create_product(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a product via REST. Expects a payload shaped for /products.json."""
        self._sleep_for_rate_limit()
        url = self._url("products.json")
        resp = self.session.post(url, headers=self._headers(), json=payload, timeout=20)
        if resp.status_code != 201:
            # Raise with context; caller can catch and log.
            try:
                details = resp.json()
            except Exception:
                details = resp.text
            raise RuntimeError(f"Shopify create_product failed: {resp.status_code} {details}")
        return resp.json().get("product", {})

    def product_by_handle(self, handle: str) -> Optional[Dict[str, Any]]:
        """
        Look up a product by handle via GraphQL. Returns the product dict (id, title) or None.
        """
        self._sleep_for_rate_limit()
        url = self._url("graphql.json")
        query = """
        query ($handle: String!) {
          productByHandle(handle: $handle) {
            id
            title
          }
        }
        """
        variables = {"handle": handle}
        resp = self.session.post(
            url,
            headers=self._headers(),
            json={"query": query, "variables": variables},
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        errors = data.get("errors")
        if errors:
            return None
        product = data.get("data", {}).get("productByHandle")
        return product

    def product_by_handle_query(self, handle: str, first: int = 1) -> Optional[Dict[str, Any]]:
        """
        Fallback search for a product by handle using the products query (handle:foo).
        Useful when productByHandle returns null for drafts or unusual handles.
        """
        if not handle:
            return None
        self._sleep_for_rate_limit()
        query = """
        query ($query: String!, $first: Int!) {
          products(first: $first, query: $query) {
            edges {
              node {
                id
                handle
                title
                status
              }
            }
          }
        }
        """
        variables = {"query": f"handle:{handle}", "first": first}
        resp = self.session.post(
            self._url("graphql.json"),
            headers=self._headers(),
            json={"query": query, "variables": variables},
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        errors = data.get("errors")
        if errors:
            return None
        edges = (
            data.get("data", {})
            .get("products", {})
            .get("edges", [])
        )
        if not edges:
            return None
        return edges[0].get("node")

    def get_taxonomy_node_id(self, query: str, first: int = 1) -> Optional[str]:
        """Fetch the first product taxonomy node id matching the query."""
        self._sleep_for_rate_limit()
        graphql = """
        query($query: String!, $first: Int!) {
          productTaxonomyNodes(query: $query, first: $first) {
            nodes {
              id
              fullName
            }
          }
        }
        """
        resp = self.session.post(
            self._url("graphql.json"),
            headers=self._headers(),
            json={"query": graphql, "variables": {"query": query, "first": first}},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        nodes = (
            data.get("data", {})
            .get("productTaxonomyNodes", {})
            .get("nodes", [])
        )
        if not nodes:
            return None
        return nodes[0].get("id")

    def update_product_category_via_category_update(
        self,
        product_gid: str,
        taxonomy_node_id: str,
        standard_product_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Set taxonomy using productCategoryUpdate. Shopify 2025-01 expects
        productCategoryId and standardProductType on the input.
        """
        if not product_gid or not taxonomy_node_id:
            raise ValueError("product_gid and taxonomy_node_id are required")

        self._sleep_for_rate_limit()
        mutation = """
        mutation productCategoryUpdate($input: ProductCategoryUpdateInput!) {
          productCategoryUpdate(input: $input) {
            product {
              id
              handle
              productCategory {
                productTaxonomyNode { id fullName }
              }
              standardizedProductType {
                productTaxonomyNode { id fullName }
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        input_data: Dict[str, Any] = {
            "id": product_gid,
            "productCategoryId": taxonomy_node_id,
        }
        if standard_product_type:
            input_data["standardProductType"] = standard_product_type

        resp = self.session.post(
            self._url("graphql.json"),
            headers=self._headers(),
            json={"query": mutation, "variables": {"input": input_data}},
            timeout=20,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Shopify productCategoryUpdate failed HTTP {resp.status_code}: {resp.text}"
            )
        data = resp.json()
        errors = data.get("errors")
        if errors:
            raise RuntimeError(f"Shopify productCategoryUpdate errors: {errors}")
        payload = data.get("data", {}).get("productCategoryUpdate", {})
        user_errors = payload.get("userErrors") or []
        if user_errors:
            logger.warning("Shopify productCategoryUpdate userErrors: %s", user_errors)
        return payload

    def update_product_category_rest(
        self,
        product_id: int,
        taxonomy_node_id: str,
        standard_product_type: Optional[str] = None,
        category_gid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Attempt to set product_category + standardized_product_type via REST PUT.
        """
        if not product_id:
            raise ValueError("product_id is required")
        if not taxonomy_node_id and not category_gid:
            raise ValueError("taxonomy_node_id or category_gid is required")
        self._sleep_for_rate_limit()
        url = self._url(f"products/{product_id}.json")
        product_payload: Dict[str, Any] = {"id": product_id}
        if category_gid:
            product_payload["category"] = category_gid
        if taxonomy_node_id:
            product_payload["product_category"] = {"product_taxonomy_node_id": taxonomy_node_id}
        # send both variants to maximize compatibility across API versions
        if standard_product_type:
            product_payload["standardized_product_type"] = standard_product_type
            product_payload["standard_product_type"] = standard_product_type
        resp = self.session.put(
            url, headers=self._headers(), json={"product": product_payload}, timeout=20
        )
        if resp.status_code != 200:
            try:
                details = resp.json()
            except Exception:
                details = resp.text
            raise RuntimeError(
                f"Shopify update_product_category_rest failed: {resp.status_code} {details}"
            )
        return resp.json().get("product", {})

    def delete_product(self, product_id: int) -> None:
        """Delete a product by ID."""
        self._sleep_for_rate_limit()
        url = self._url(f"products/{product_id}.json")
        resp = self.session.delete(url, headers=self._headers(), timeout=20)
        if resp.status_code not in (200, 204):
            try:
                details = resp.json()
            except Exception:
                details = resp.text
            raise RuntimeError(f"Shopify delete_product failed: {resp.status_code} {details}")

    def update_product_category_graphql(
        self,
        product_gid: str,
        category_gid: str,
    ) -> Dict[str, Any]:
        """
        Set product category via GraphQL (preferred for taxonomy in 2025-01+).
        Uses productUpdate with ProductInput.category.
        """
        if not product_gid or not category_gid:
            raise ValueError("product_gid and category_gid are required")
        self._sleep_for_rate_limit()
        mutation = """
        mutation UpdateProductCategory($id: ID!, $categoryId: ID!) {
          productUpdate(input: { id: $id, category: $categoryId }) {
            product {
              id
              title
              category { id fullName }
              standardizedProductType {
                productTaxonomyNode { id }
              }
            }
            userErrors { field message }
          }
        }
        """
        resp = self.session.post(
            self._url("graphql.json"),
            headers=self._headers(),
            json={"query": mutation, "variables": {"id": product_gid, "categoryId": category_gid}},
            timeout=20,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Shopify productUpdate (category) failed HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        errors = data.get("errors")
        if errors:
            raise RuntimeError(f"Shopify productUpdate (category) errors: {errors}")
        payload = data.get("data", {}).get("productUpdate") or {}
        user_errors = payload.get("userErrors") or []
        if user_errors:
            logger.warning("Shopify productUpdate (category) userErrors: %s", user_errors)
        return payload
