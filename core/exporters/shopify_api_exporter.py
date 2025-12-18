from __future__ import annotations

import os
from typing import List, Optional
import requests
from uf_logging import get_logger

from core.clients.shopify import ShopifyClient
from core.exporters.base import Exporter
from core.models import ProcessSummary, RecordInput, ShopifyDraft


logger = get_logger(__name__)

_SHOPIFY_CATEGORY_ID_ENV = os.getenv("SHOPIFY_PRODUCT_CATEGORY_ID", "").strip()
_SHOPIFY_CATEGORY_GID_ENV = os.getenv("SHOPIFY_CATEGORY_GID", "").strip()
SHOPIFY_PRODUCT_CATEGORY = "Media > Music & Sound Recordings > Records & LPs"
# Default taxonomy node id for Records & LPs: gid://shopify/ProductTaxonomyNode/543525
# Can be overridden via env SHOPIFY_PRODUCT_CATEGORY_ID if needed.
SHOPIFY_PRODUCT_CATEGORY_ID = (
    _SHOPIFY_CATEGORY_ID_ENV or "gid://shopify/ProductTaxonomyNode/543525"
)
# Newer Shopify API versions (2024-07+) expect the category field with a TaxonomyCategory GID.
# Allow overriding via SHOPIFY_CATEGORY_GID; default to Records & LPs category gid.
SHOPIFY_CATEGORY_GID = _SHOPIFY_CATEGORY_GID_ENV or "gid://shopify/TaxonomyCategory/me-3-4"
# Fallback search queries to try if the primary name doesn't resolve (should be unused with hardcoded ID)
SHOPIFY_CATEGORY_QUERIES = [
    SHOPIFY_PRODUCT_CATEGORY,
    "Vinyl in Music & Sound Recordings",  # previous default
    "Records & LPs in Music & Sound Recordings",
]
# Expected product_type for records; used as a guard when missing.
SHOPIFY_DEFAULT_PRODUCT_TYPE = "Vinyl Record"


class ShopifyAPIExporter(Exporter):
    """
    Exporter that creates draft products in Shopify via the Admin REST API.

    This is intentionally minimal and assumes:
    - One variant per product with a price.
    - Tags as a comma-separated string.
    - Images provided as URLs in draft.images.
    - Metafields sent as single_line_text_field under namespace 'custom'.
    """

    def __init__(
        self,
        client: ShopifyClient,
        publish: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.client = client
        self.publish = publish
        self.dry_run = dry_run
        self.created_ids: List[int] = []
        self.unmatched: List[str] = []
        self.duplicates: List[str] = []
        self._product_taxonomy_node_id: Optional[str] = None

    def _preflight_images(self, images: List[str]) -> None:
        """
        Best-effort fetch to see if image URLs are reachable before sending to Shopify.
        """
        for url in images:
            try:
                resp = requests.get(url, stream=True, timeout=8)
                content_type = resp.headers.get("Content-Type")
                content_length = resp.headers.get("Content-Length")
                logger.info(
                    "Image preflight url=%s status=%s content_type=%s content_length=%s",
                    url,
                    resp.status_code,
                    content_type,
                    content_length,
                )
                resp.close()
            except Exception as exc:
                logger.warning("Image preflight failed for %s: %s", url, exc)

    def _build_payload(self, draft: ShopifyDraft) -> dict:
        status = "active" if self.publish else "draft"
        tags = ", ".join(draft.tags) if draft.tags else ""

        images = [{"src": url} for url in draft.images] if draft.images else []
        logger.debug(
            "Building payload images for handle=%s: %s",
            draft.handle or "<missing>",
            draft.images,
        )

        # Metafields: default to text, but use store definitions when known
        metafields = []
        type_map = {
            "uses_stock_photo": "boolean",
            "inventory_date": "date",
        }

        for key, value in (draft.metafields or {}).items():
            mf_type = type_map.get(key, "single_line_text_field")
            # Coerce booleans to true/false strings for Shopify
            if mf_type == "boolean":
                val_str = str(value).strip().lower()
                if val_str in ("true", "1", "yes", "y", "t"):
                    val_str = "true"
                else:
                    val_str = "false"
                mf_value = val_str
            else:
                mf_value = str(value)

            metafields.append(
                {
                    "namespace": "custom",
                    "key": key,
                    "type": mf_type,
                    "value": mf_value,
                }
            )

        # Guard product_type: default to expected vinyl type if missing; warn on missing/mismatch.
        product_type = draft.product_type or SHOPIFY_DEFAULT_PRODUCT_TYPE
        if not draft.product_type:
            logger.warning(
                "Missing product_type for handle=%s title=%s; defaulting to %s",
                draft.handle or "<missing>",
                draft.title,
                SHOPIFY_DEFAULT_PRODUCT_TYPE,
            )
        elif draft.product_type != SHOPIFY_DEFAULT_PRODUCT_TYPE:
            logger.warning(
                "Unexpected product_type=%s for handle=%s title=%s; expected %s. Proceeding with provided value.",
                draft.product_type,
                draft.handle or "<missing>",
                draft.title,
                SHOPIFY_DEFAULT_PRODUCT_TYPE,
            )

        payload = {
            "product": {
                "handle": draft.handle or None,
                "title": draft.title,
                "body_html": draft.body_html,
                "vendor": draft.vendor,
                "product_type": product_type,
                "status": status,
                "tags": tags,
                "variants": [
                    {
                        "price": f"{draft.price:.2f}",
                        "inventory_management": "shopify",
                        "inventory_policy": "deny",
                        "inventory_quantity": 1,
                        "requires_shipping": True,
                        "sku": draft.sku or None,
                        "barcode": draft.barcode or None,
                    }
                ],
            }
        }

        if images:
            payload["product"]["images"] = images
        if metafields:
            payload["product"]["metafields"] = metafields
        if draft.collections:
            # Shopify REST collections need separate calls; not included here.
            pass

        return payload

    def ensure_taxonomy_node(self) -> Optional[str]:
        if self._product_taxonomy_node_id:
            return self._product_taxonomy_node_id
        if SHOPIFY_CATEGORY_GID:
            # New category field path (preferred)
            self._product_taxonomy_node_id = SHOPIFY_CATEGORY_GID
            logger.info(
                "Using Shopify category gid %s for %s (source=%s).",
                self._product_taxonomy_node_id,
                SHOPIFY_PRODUCT_CATEGORY,
                "env override" if _SHOPIFY_CATEGORY_GID_ENV else "default",
            )
            return self._product_taxonomy_node_id
        if SHOPIFY_PRODUCT_CATEGORY_ID:
            self._product_taxonomy_node_id = SHOPIFY_PRODUCT_CATEGORY_ID
            logger.info(
                "Using Shopify taxonomy node id %s for %s (source=%s).",
                self._product_taxonomy_node_id,
                SHOPIFY_PRODUCT_CATEGORY,
                "env override" if _SHOPIFY_CATEGORY_ID_ENV else "default",
            )
            return self._product_taxonomy_node_id
        for query in SHOPIFY_CATEGORY_QUERIES:
            logger.info("Resolving Shopify taxonomy node via query %r", query)
            node_id = self.client.get_taxonomy_node_id(query)
            if node_id:
                self._product_taxonomy_node_id = node_id
                logger.info(
                    "Resolved Shopify taxonomy node id %s from query %r",
                    node_id,
                    query,
                )
                break
            logger.warning("No taxonomy node found for query %r", query)
        if not self._product_taxonomy_node_id:
            logger.warning(
                "No Shopify taxonomy node id resolved; product_category will be omitted."
            )
        return self._product_taxonomy_node_id

    def write_product(self, draft: ShopifyDraft) -> None:
        payload = self._build_payload(draft)
        if draft.images:
            self._preflight_images(draft.images)
        taxonomy_id = self.ensure_taxonomy_node()
        product_payload = payload["product"]
        if taxonomy_id and SHOPIFY_CATEGORY_GID:
            product_payload["category"] = taxonomy_id
        elif taxonomy_id:
            product_payload["product_category"] = {
                "product_taxonomy_node_id": taxonomy_id
            }
        else:
            logger.warning(
                "Skipping product_category for handle=%s title=%s; no taxonomy id.",
                draft.handle or "<missing>",
                draft.title,
            )
        # REST field name is standardized_product_type (standard_product_type is ignored)
        product_payload["standardized_product_type"] = SHOPIFY_PRODUCT_CATEGORY
        logger.info(
            "Preparing Shopify product handle=%s title=%s with taxonomy_id=%s and standardized_product_type=%s",
            draft.handle or "<missing>",
            draft.title,
            taxonomy_id,
            SHOPIFY_PRODUCT_CATEGORY,
        )
        logger.debug(
            "Shopify payload category section: product_category=%s category=%s standardized_product_type=%s",
            product_payload.get("product_category"),
            product_payload.get("category"),
            product_payload.get("standardized_product_type"),
        )
        if self.dry_run:
            return

        # Idempotency: skip if handle already exists
        existing = None
        if draft.handle:
            existing = self.client.product_by_handle(draft.handle)
            if not existing:
                existing = self.client.product_by_handle_query(draft.handle)
        if not draft.handle:
            self.unmatched.append(f"{draft.title}: missing handle")
            return
        if existing:
            self.duplicates.append(f"{draft.handle} (existing id {existing.get('id')})")
            return

        try:
            product = self.client.create_product(payload)
        except Exception:
            logger.exception(
                "Shopify create_product failed for handle=%s title=%s with taxonomy_id=%s "
                "and standard_product_type=%s. Payload category=%s",
                draft.handle or "<missing>",
                draft.title,
                taxonomy_id,
                SHOPIFY_PRODUCT_CATEGORY,
                product_payload.get("product_category"),
            )
            raise
        product_id = product.get("id")
        product_gid = product.get("admin_graphql_api_id")
        if product_id:
            self.created_ids.append(product_id)
            logger.info(
                "Created Shopify product id=%s for handle=%s title=%s",
                product_id,
                draft.handle,
                draft.title,
            )
        resp_category = product.get("product_category")
        resp_standardized = product.get("standardized_product_type")
        resp_images = product.get("images")
        if not resp_category or not resp_standardized:
            logger.warning(
                "Shopify response missing category fields for id=%s handle=%s "
                "(product_category=%s standardized_product_type=%s). "
                "Attempting REST update fallback for category.",
                product_id,
                draft.handle or "<missing>",
                resp_category,
                resp_standardized,
            )
            if product_id and taxonomy_id:
                try:
                    updated = self.client.update_product_category_rest(
                        product_id=product_id,
                        taxonomy_node_id=None if SHOPIFY_CATEGORY_GID else taxonomy_id,
                        category_gid=taxonomy_id if SHOPIFY_CATEGORY_GID else None,
                        standard_product_type=SHOPIFY_PRODUCT_CATEGORY,
                    )
                    cat_node = (
                        (updated.get("product_category") or {})
                        .get("product_taxonomy_node_id")
                    )
                    std_type = updated.get("standardized_product_type") or updated.get(
                        "standard_product_type"
                    )
                    category_field = updated.get("category")
                    logger.info(
                        "REST category update set category for id=%s handle=%s "
                        "(product_category=%s category=%s standardized/standard_product_type=%s)",
                        product_id,
                        draft.handle or "<missing>",
                        cat_node,
                        category_field,
                        std_type,
                    )
                except Exception:
                    logger.exception(
                        "REST category update fallback failed for id=%s handle=%s",
                        product_id,
                        draft.handle or "<missing>",
                    )
            logger.debug(
                "Shopify create response images for id=%s handle=%s: %s",
                product_id,
                draft.handle or "<missing>",
                resp_images,
            )

        # Best-effort GraphQL category set to ensure taxonomy sticks in 2025-01+
        if SHOPIFY_CATEGORY_GID and product_gid:
            try:
                cat_payload = self.client.update_product_category_graphql(
                    product_gid=product_gid,
                    category_gid=SHOPIFY_CATEGORY_GID,
                )
                cat = ((cat_payload.get("product") or {}).get("category")) if cat_payload else None
                logger.info(
                    "GraphQL category update applied for handle=%s title=%s product_gid=%s category=%s",
                    draft.handle or "<missing>",
                    draft.title,
                    product_gid,
                    cat,
                )
            except Exception:
                logger.exception(
                    "GraphQL category update failed for product_gid=%s handle=%s title=%s",
                    product_gid,
                    draft.handle or "<missing>",
                    draft.title,
                )

    def write_unmatched(self, record: RecordInput, reason: str) -> None:
        self.unmatched.append(f"{record.artist} - {record.title}: {reason}")

    def finalize(self, summary: ProcessSummary) -> None:
        # No-op for now; extend to log summary, persist IDs, or handle publishing.
        return
