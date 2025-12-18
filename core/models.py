from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class RecordInput:
    """Normalized input from spreadsheets or uploads."""

    artist: str
    title: str
    label: Optional[str] = None
    catalog: Optional[str] = None
    barcode: Optional[str] = None
    country: Optional[str] = None
    year: Optional[int] = None
    format_hint: Optional[str] = None
    reference_price: Optional[float] = None
    sleeve_condition: Optional[str] = None
    media_condition: Optional[str] = None
    center_label_image: Optional[Path] = None
    etching_text: Optional[str] = None


@dataclass
class DiscogsResult:
    """Minimal fields needed from Discogs search/release detail."""

    release_id: int
    title: str
    artist: str
    label: str
    year: Optional[str]
    genres: List[str]
    styles: List[str]
    formats: List[str]
    images: List[str]  # URLs
    tracklist_html: str


@dataclass
class ReleaseMatch:
    """Generic matched release with source metadata for fallback flows."""

    source: str  # e.g., "musicbrainz" or "discogs"
    release_id: str
    title: str
    artist: str
    year: Optional[str]
    url: Optional[str]
    discogs_release_id: Optional[str] = None
    discogs_url: Optional[str] = None
    discogs_marketplace_stats: Optional[Dict[str, object]] = None
    discogs_price_suggestions: Optional[Dict[str, object]] = None
    raw: object = None  # raw payload or domain object for downstream use


@dataclass
class ShopifyDraft:
    """Intermediate representation before exporting to CSV or Shopify API."""

    handle: str
    title: str
    body_html: str
    vendor: str
    product_type: str
    product_category: str
    tags: List[str]
    price: float
    metafields: Dict[str, str]
    images: List[str]  # URLs or file paths
    collections: List[str]
    sku: str = ""
    barcode: str = ""


@dataclass
class ProcessSummary:
    """Aggregated results from a processing run."""

    total_rows: int
    matched_count: int
    unmatched_count: int
    total_final_price: float
    total_reference_price: float
    price_diff: float
