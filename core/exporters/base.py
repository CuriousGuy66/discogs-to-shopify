from __future__ import annotations

from typing import Protocol

from core.models import ProcessSummary, RecordInput, ShopifyDraft


class Exporter(Protocol):
    """Common interface for output targets (CSV, Shopify API, etc.)."""

    def write_product(self, draft: ShopifyDraft) -> None:
        ...

    def write_unmatched(self, record: RecordInput, reason: str) -> None:
        ...

    def finalize(self, summary: ProcessSummary) -> None:
        ...
