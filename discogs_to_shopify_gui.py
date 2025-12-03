#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discogs_to_shopify_gui.py
===============================================================================
Graphical interface for the Discogs → Shopify vinyl import pipeline.

This application reads a CSV or Excel inventory file, searches for matching
releases on Discogs, enriches metadata, and generates:

1) A Shopify-compatible Products CSV for matched records.
2) A separate Metafields CSV for matched records to update product metafields
   after the products have been created in Shopify.

FEATURE SUMMARY
-------------------------------------------------------------------------------
• Tkinter GUI with:
    - File picker (CSV/XLSX)
    - Discogs token input (auto-loads from environment)
    - Progress bar & status updates
    - Log window
    - Settings persistence (last-used input file, token, etc.)

• Core processing:
    - Input sheet normalization
    - Discogs search (artist/title/year/country/catalog)
    - Best-match scoring
    - Release details fetch (label, year, genre, styles, format, tracklist)
    - Shopify row construction (single-variant)
    - Tracklist HTML.
    - Shopify metafields + pricing rules.

• Pricing:
    - Separate pricing engine (pricing.py) for all pricing logic.
    - Uses:
        - Reference price from spreadsheet
        - Discogs marketplace stats (high price)
        - eBay SOLD & ACTIVE listings (via ebay_search.py)
          (currently disabled, but wiring kept in place)
    - Applies:
        - Shipping normalization
        - Condition adjustments
        - Competitive discount
        - Rounding to nearest $0.25
        - Global floor ($2.50)
    - Writes:
        - Price
        - Pricing Strategy Used
        - Pricing Notes

• Not-matched handling:
    - Logged and exported to separate CSV
    - Includes reason + final Discogs query used

• OCR module integration (label_ocr.py):
    - Optional center-label analysis
    - Catalog number extraction
    - Misprint detection
    - Query enrichment
    - Exposes Ocr_* fields and Label_Catalog_Number in the output CSV.

VERSION HISTORY
===============================================================================
v1.2.4 – 2025-11-29
    - Added Discogs marketplace stats as input to pricing engine.
    - Integrated external pricing module (pricing.py).
    - Integrated eBay pricing module (ebay_search.py).
    - Added OCR URL handling (via label_ocr.py).
    - Exposed Ocr_* fields and Label_Catalog_Number in the matched output CSV.

v1.2.5 – 2025-12-02
    - Added product metafields as product.metafields.custom.* columns:
        shop_signage, album_cover_condtion, album_condition,
        condition, condition_description.

v1.2.6 – 2025-12-02
    - Added a separate metafields-only CSV for Shopify metafield update imports:
        <input>_Metafields for matched records.csv
      with columns:
        Handle,
        product.metafields.custom.shop_signage,
        product.metafields.custom.album_cover_condtion,
        product.metafields.custom.album_condition,
        product.metafields.custom.condition,
        product.metafields.custom.condition_description.

v1.2.7 – 2025-12-02
    - Fixed watermark / primary_image_url handling.
    - Centralized Discogs calls via discogs_client wrapper.
    - Cleaned up logging usage and small bugs.
"""

# ================================================================
# 1. Standard Library Imports
# ================================================================
import os
import sys
import csv
import time
import json
import logging
import datetime as dt
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

# ================================================================
# 2. Third-Party Imports
# ================================================================
import requests
import pandas as pd
from PIL import Image  # not directly used here, but kept if needed later
from slugify import slugify

# ================================================================
# 3. Local Project Imports
# ================================================================
import discogs_client
import image_watermark
import ebay_search
import pricing
from label_ocr import (
    enrich_meta_with_label,
    build_discogs_query_with_label,
    detect_label_misprint,
)
from uf_logging import setup_logging, get_logger

# ================================================================
# Initialize Central Logging Early
# ================================================================
log_file = setup_logging()
logger = get_logger(__name__)
logger.info("discogs_to_shopify_gui.py started. Log file: %s", log_file)

DISCOGS_API_BASE = "https://api.discogs.com"
APP_VERSION = "v1.2.7"
DISCOGS_USER_AGENT = (
    f"UnusualFindsDiscogsToShopify/{APP_VERSION} +https://unusualfinds.com"
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Shopify static preferences
SHOPIFY_PRODUCT_TYPE = "Vinyl Record"
SHOPIFY_PRODUCT_CATEGORY = "Media > Music & Sound Recordings > Records & LPs"
SHOPIFY_OPTION1_NAME = "Title"
SHOPIFY_OPTION1_VALUE = "Default Title"
SHOPIFY_VARIANT_FULFILLMENT_SERVICE = "manual"
SHOPIFY_VARIANT_REQUIRES_SHIPPING = "TRUE"
SHOPIFY_VARIANT_TAXABLE = "TRUE"
SHOPIFY_PRODUCT_STATUS = "active"
SHOPIFY_PUBLISHED = "TRUE"

MIN_PRICE = 2.50  # USD minimum
PRICE_STEP = 0.25  # round to nearest quarter

# Column names expected in the input sheet
COL_ARTIST = "Artist"
COL_TITLE = "Title"
COL_PRICE = "Reference Price"
COL_COUNTRY = "Country"
COL_CATALOG = "Catalog"
COL_CENTER_LABEL_PHOTO = "Center label photo"
COL_MEDIA_COND = "Media Condition"
COL_SLEEVE_COND = "Sleeve Condition"
COL_TYPE = "Type"

# Description footer HTML appended to every product description
DESCRIPTION_FOOTER_HTML = (
    "<p>All albums are stored in heavy-duty protective sleeves to help preserve "
    "their condition. The first image shown is a stock photo for reference.</p>"
    "<p>Please note that every record we sell goes through a careful process "
    "that includes inspection, research, detailed listing, and photography. "
    "Our prices may not always be the lowest, but we take pride in accurately "
    "representing each album and providing thorough information so you can "
    "buy with confidence.</p>"
)

# Shop signage categories (simplified mapping from genres/styles)
SHOP_SIGNAGE_MAP: Dict[str, str] = {
    "Blues": "Blues",
    "Jazz": "Jazz",
    "Rock": "Rock",
    "Funk / Soul": "Soul/Funk",
    "Soul": "Soul/Funk",
    "Funk": "Soul/Funk",
    "Classical": "Classical",
    "Stage & Screen": "Stage & Sound",
    "Stage & Sound": "Stage & Sound",
    "Religious": "Religious",
    "Gospel": "Gospel",
    "Holiday": "Holiday/Christmas",
    "Christmas": "Holiday/Christmas",
    "Children's": "Children",
    "Reggae": "Reggae",
    "Latin": "Latin",
    "Folk": "Folk",
    "Pop": "Pop",
    "Disco": "Disco",
    "Comedy": "Comedy",
    "New Age": "New Age",
    "Spoken Word": "Spoken Word",
    "Electronic": "Electronic",
    "Metal": "Metal",
    "Bluegrass": "Bluegrass",
    "Soundtrack": "Stage & Sound",
}


def simple_shop_signage(genre: Optional[str], styles: List[str]) -> str:
    """
    Very simple logic to derive a shop signage bucket from Discogs genre/styles.
    """
    # Styles override genre if they map directly
    for st in styles:
        if st in SHOP_SIGNAGE_MAP:
            return SHOP_SIGNAGE_MAP[st]

    if genre in SHOP_SIGNAGE_MAP:
        return SHOP_SIGNAGE_MAP[genre]

    # Special logic: if style is religious but not gospel or holiday, mark as Religious
    lower_styles = [s.lower() for s in styles]
    if "religious" in lower_styles and "gospel" not in lower_styles and "holiday" not in lower_styles:
        return "Religious"

    return genre or "Misc"


def normalize_artist_the(name: str) -> str:
    """
    If the artist starts with 'The', move 'The' to the end:
    'The Beatles' -> 'Beatles, The'
    """
    if not name:
        return name
    name_stripped = name.strip()
    if name_stripped.lower().startswith("the "):
        core = name_stripped[4:].strip()
        return f"{core}, The"
    return name_stripped


def slugify_handle(text: str) -> str:
    """
    Create a Shopify handle from text.
    """
    if not text:
        return ""
    return slugify(text, lowercase=True)


def round_price(value: float) -> float:
    """
    Round to nearest quarter, with a hard floor at MIN_PRICE.
    """
    rounded = round(value / PRICE_STEP) * PRICE_STEP
    if rounded < MIN_PRICE:
        rounded = MIN_PRICE
    return rounded


def build_format_description(release: Dict[str, Any]) -> str:
    """
    Build a human-readable format string from a Discogs release JSON.
    """
    formats = release.get("formats") or []
    parts: List[str] = []
    for f in formats:
        name = f.get("name", "")
        desc = f.get("descriptions", [])
        if name:
            parts.append(name)
        parts.extend(desc)
    return ", ".join(parts)


def build_tracklist_html(release: Dict[str, Any]) -> str:
    """
    Build an HTML ordered list from a Discogs release tracklist.
    """
    tracks = release.get("tracklist") or []
    if not tracks:
        return ""
    lines = ["<h3>Tracklist</h3>", "<ol>"]
    for t in tracks:
        title = t.get("title", "")
        duration = t.get("duration", "")
        if duration:
            lines.append(f"<li>{title} ({duration})</li>")
        else:
            lines.append(f"<li>{title}</li>")
    lines.append("</ol>")
    return "\n".join(lines)


def extract_genre_and_styles(release: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    genres = release.get("genres") or []
    styles = release.get("styles") or []
    primary_genre = genres[0] if genres else None
    return primary_genre, styles


def extract_label_and_year(release: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    """
    Extract label name and year from a Discogs release.
    """
    label_name = None
    labels = release.get("labels") or []
    if labels:
        label_name = labels[0].get("name")

    year = release.get("year")
    try:
        year_int = int(year) if year is not None else None
    except (TypeError, ValueError):
        year_int = None

    return label_name, year_int


def extract_primary_image_url(release: Dict[str, Any]) -> str:
    """
    Extract the first image URL from a Discogs release.
    """
    images = release.get("images") or []
    if not images:
        return ""
    first = images[0]
    return first.get("uri", "") or first.get("resource_url", "")


def calculate_weight_grams_from_formats(release: Dict[str, Any]) -> Optional[int]:
    """
    Estimate record weight from format information.
    """
    formats = release.get("formats") or []
    if not formats:
        return None

    # Basic heuristic: number of LPs * 300g
    total_discs = 0
    for f in formats:
        qty = f.get("qty")
        try:
            qty_int = int(qty)
        except (TypeError, ValueError):
            qty_int = 1
        total_discs += qty_int

    if total_discs <= 0:
        return None
    return total_discs * 300


def grams_to_pounds(grams: Optional[int]) -> Optional[float]:
    if grams is None:
        return None
    return round(grams / 453.59237, 3)


def make_full_release_title(
    artist: str, title: str, label: Optional[str], year: Optional[int]
) -> str:
    """
    Build a full Shopify product title from artist, album, label, year.
    """
    base = f"{artist} – {title}"
    if year and label:
        return f"{base} ({year}, {label})"
    if year:
        return f"{base} ({year})"
    if label:
        return f"{base} ({label})"
    return base


def build_seo_title(full_title: str) -> str:
    return f"{full_title} | Vinyl Record | Unusual Finds"


def build_seo_description(
    artist: str, title: str, year: Optional[int], genre: Optional[str]
) -> str:
    year_str = str(year) if year else ""
    genre_str = genre or ""
    core = f"Vintage vinyl record: {artist} - {title}"
    parts = [core]
    if year_str:
        parts.append(year_str)
    if genre_str:
        parts.append(genre_str)
    return ". ".join(parts) + ". Available at Unusual Finds."


def build_tags(
    genre: Optional[str],
    styles: List[str],
    year: Optional[int],
    label: Optional[str],
    format_desc: str,
) -> str:
    """
    Build a comma-separated list of Shopify tags, SEO-friendly.
    """
    tags: List[str] = []
    if year:
        tags.append(str(year))
    if label:
        tags.append(label)
    if genre:
        tags.append(genre)
        tags.append(f"{genre} Vinyl")
    for s in styles:
        tags.append(s)
        tags.append(f"{s} Vinyl")
    if format_desc:
        tags.append("Vinyl")
        tags.append(format_desc)
    # Deduplicate, preserve order
    seen = set()
    final_tags = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            final_tags.append(t)
    return ", ".join(final_tags)


def normalize_ascii_punctuation(text: str) -> str:
    """
    Replace some common Unicode punctuation with simple ASCII equivalents
    so CSV/Excel/Shopify displays cleanly.
    """
    if not text:
        return text
    return (
        text.replace("\u2013", "-")  # en dash
        .replace("\u2014", "-")  # em dash
        .replace("\u2018", "'")  # left single quote
        .replace("\u2019", "'")  # right single quote
        .replace("\u201c", '"')  # left double quote
        .replace("\u201d", '"')  # right double quote
    )


# ---------------------------------------------------------------------------
# Discogs API helpers (wrappers around discogs_client)
# ---------------------------------------------------------------------------


def build_discogs_headers(token: str) -> Dict[str, str]:
    return {
        "User-Agent": DISCOGS_USER_AGENT,
        "Authorization": f"Discogs token={token}",
    }


def rate_limit_sleep(resp: requests.Response) -> None:
    """
    Respect Discogs rate limits by sleeping if X-Discogs-Ratelimit-Remaining is low.
    """
    remaining = resp.headers.get("X-Discogs-Ratelimit-Remaining")
    if remaining is None:
        return
    try:
        rem_int = int(remaining)
    except ValueError:
        return
    if rem_int < 5:
        time.sleep(1.0)


def discogs_search_release(
    token: str,
    artist: str,
    title: str,
    country: Optional[str],
    catalog: Optional[str],
    year: Optional[int],
) -> Optional[Dict[str, Any]]:
    """
    Perform a Discogs release search.
    Uses discogs_client for retry + throttle behavior.
    """
    return discogs_client.search_release(
        token=token,
        artist=artist,
        title=title,
        country=country,
        catalog=catalog,
        year=year,
    )


def discogs_get_release_details(token: str, release_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetch full release details for a given Discogs release ID.
    """
    return discogs_client.get_release_details(token, release_id)


def discogs_get_marketplace_stats(
    token: str, release_id: int
) -> Optional[Dict[str, Any]]:
    """
    Fetch Discogs marketplace stats for a release.
    """
    return discogs_client.get_marketplace_stats(token, release_id)


# ================================================================
# Watermark wrapper
# ================================================================
def apply_watermarked_cover(url: str, handle: str) -> str:
    """
    Wrapper around image_watermark.watermark_stock_photo that automatically
    sets the correct cache directory and handle slug.

    Returns the local watermarked file path, or original url if failed.
    """
    if not url:
        return ""

    # Save under ~/.discogs_to_shopify/watermarked/
    cache_dir = os.path.expanduser("~/.discogs_to_shopify/watermarked")

    try:
        watermarked = image_watermark.watermark_stock_photo(
            image_url=url,
            cache_dir=cache_dir,
            handle=handle,
        )
        return watermarked
    except Exception as e:
        logger.warning("Watermark wrapper failed for %s: %s", url, e)
        return url


# ---------------------------------------------------------------------------
# Settings helpers (load/save last used token & file)
# ---------------------------------------------------------------------------


def print_run_banner() -> None:
    """Print a line showing when this script is running."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    print(f"[discogs_to_shopify] Run at {now}", flush=True)


def get_settings_path() -> Path:
    """
    Return the JSON settings file path located under the user's home directory.
    """
    home = Path.home()
    base = home / ".discogs_to_shopify"
    base.mkdir(parents=True, exist_ok=True)
    return base / "discogs_to_shopify_settings.json"


def load_settings() -> Dict[str, Any]:
    path = get_settings_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(settings: Dict[str, Any]) -> None:
    path = get_settings_path()
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save settings: %s", e)


def persist_discogs_token_to_env(token: str) -> None:
    """
    Best-effort attempt to persist the Discogs token to the environment so that
    future runs (and a frozen EXE) can use it without re-typing.

    - On Windows, attempts to persist it to the user's environment using `setx`,
      so it is available to future processes (including future EXE runs).
    """
    if not token:
        return

    current = os.getenv("DISCOGS_TOKEN")
    if current == token:
        # Already set to this value; nothing to do.
        return

    # Set for the current process
    os.environ["DISCOGS_TOKEN"] = token

    # Persist on Windows for future sessions
    if os.name == "nt":
        try:
            subprocess.run(["setx", "DISCOGS_TOKEN", token], check=False)
        except Exception as e:
            logger.warning("Failed to persist Discogs token with setx: %s", e)


# ---------------------------------------------------------------------------
# Row processing
# ---------------------------------------------------------------------------


def make_shopify_rows_for_record(
    input_row: Dict[str, Any],
    release_search_obj: Dict[str, Any],
    release_details: Dict[str, Any],
    misprint_info: Optional[Dict[str, Any]],
    handle_registry: Dict[str, int],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Build one or more Shopify rows (main product + optional image-only row)
    for a single matched Discogs release.

    Returns:
        (shopify_rows, metafield_row)
    """

    artist_raw = str(input_row.get(COL_ARTIST, "")).strip()
    title = str(input_row.get(COL_TITLE, "")).strip()

    # Reference price may come in as float/NaN from pandas; normalize to clean string
    raw_price = input_row.get(COL_PRICE, "")
    try:
        if raw_price is None or (isinstance(raw_price, float) and pd.isna(raw_price)):
            price_str = ""
        else:
            price_str = str(raw_price).strip()
    except Exception:
        price_str = ""

    media_cond = str(input_row.get(COL_MEDIA_COND, "")).strip()
    sleeve_cond = str(input_row.get(COL_SLEEVE_COND, "")).strip()
    center_label_photo = str(input_row.get(COL_CENTER_LABEL_PHOTO, "")).strip()

    artist_display = normalize_artist_the(artist_raw)

    label, year = extract_label_and_year(release_details)
    genre, styles = extract_genre_and_styles(release_details)
    format_desc = build_format_description(release_details)
    tracklist_html = build_tracklist_html(release_details)
    shop_signage = simple_shop_signage(genre, styles)

    discogs_release_id = release_details.get("id")
    discogs_url = (
        f"https://www.discogs.com/release/{discogs_release_id}"
        if discogs_release_id
        else ""
    )

    # --- Weight estimation ---
    grams = calculate_weight_grams_from_formats(release_details)
    pounds = grams_to_pounds(grams)

    full_title = make_full_release_title(artist_display, title, label, year)
    seo_title = build_seo_title(full_title)
    seo_description = build_seo_description(artist_display, title, year, genre)

    # --- Discogs marketplace stats for pricing (HIGH price) ---
    market_stats = release_details.get("_marketplace_stats") or {}
    discogs_high_price: Optional[float] = None

    highest_obj = market_stats.get("highest_price")

    if isinstance(highest_obj, dict):
        # Normal case: {"value": 45.0, "currency": "USD"}
        val = highest_obj.get("value")
        try:
            discogs_high_price = float(val) if val is not None else None
        except (TypeError, ValueError):
            discogs_high_price = None
    else:
        # Fallback in case the API ever returns a bare number
        try:
            discogs_high_price = float(highest_obj) if highest_obj is not None else None
        except (TypeError, ValueError):
            discogs_high_price = None

    logger.info("Discogs HIGH marketplace price: %s", discogs_high_price)

    # --- eBay SOLD and ACTIVE listings for pricing ---
    # Temporarily disabled due to eBay API token/scope issues.
    # We keep the structure and types so the pricing engine still works,
    # but it will behave as "no eBay data available".
    ebay_sold_listings: List[pricing.EbayListing] = []
    ebay_active_listings: List[pricing.EbayListing] = []

    logger.info(
        "eBay pricing temporarily DISABLED for %s; using only Discogs + reference price.",
        full_title,
    )

    # Build pricing context
    ctx = pricing.PricingContext(
        format_type=format_desc or (str(input_row.get(COL_TYPE, "")).strip() or "LP"),
        media_condition=media_cond,
        reference_price=float(price_str) if price_str else None,
        discogs_median=None,
        discogs_last=None,
        discogs_low=discogs_high_price,  # using HIGH as the discogs_low input intentionally
        comparable_price=None,
        ebay_sold=ebay_sold_listings,
        ebay_active=ebay_active_listings,
    )

    # Compute price using the pricing engine
    pricing_result = pricing.compute_price(ctx)
    price = pricing_result.final_price
    price_str_out = f"{price:.2f}"

    # Unique handle
    base_handle = slugify_handle(f"{artist_display} {title} {year}".strip())
    if base_handle not in handle_registry:
        handle_registry[base_handle] = 1
        handle = base_handle
    else:
        handle_registry[base_handle] += 1
        handle = f"{base_handle}-{handle_registry[base_handle]}"

    # ------------------------------------------------------------
    # Main cover image: prefer release details, fall back to search
    # Then apply STOCK PHOTO watermark, saving to a local cache path.
    # ------------------------------------------------------------
    primary_image_url = extract_primary_image_url(release_details)
    if not primary_image_url:
        primary_image_url = extract_primary_image_url(release_search_obj or {})

    if primary_image_url:
        primary_image_url = apply_watermarked_cover(
            primary_image_url,  # original Discogs URL
            base_handle,        # used to build unique filename
        )

    tags = build_tags(genre, styles, year, label, format_desc)

    # Misprint flag info
    mis_suspected = False
    mis_reasons = ""
    if misprint_info:
        mis_suspected = bool(misprint_info.get("Label_Misprint_Suspected"))
        mis_reasons = misprint_info.get("Label_Misprint_Reasons", "")

    # -------------------------
    # Description (Body HTML)
    # -------------------------
    body_lines: List[str] = []
    body_lines.append(f"<b>Artist:</b> {artist_display}<br>")
    body_lines.append(f"<b>Album Title:</b> {title}<br>")
    if label:
        body_lines.append(f"<b>Label:</b> {label}<br>")
    if year:
        body_lines.append(f"<b>Year:</b> {year}<br>")
    if format_desc:
        body_lines.append(f"<b>Format:</b> {format_desc}<br>")
    if genre:
        body_lines.append(f"<b>Genre:</b> {genre}<br>")
    if media_cond:
        body_lines.append(f"<b>Media Condition:</b> {media_cond}<br>")
    if sleeve_cond:
        body_lines.append(f"<b>Sleeve Condition:</b> {sleeve_cond}<br>")
    if discogs_url:
        body_lines.append(
            f'<b>Discogs Link:</b> <a href="{discogs_url}" target="_blank">{discogs_url}</a><br>'
        )
    if tracklist_html:
        body_lines.append("<br>")
        body_lines.append(tracklist_html)

    # Footer
    body_lines.append("<br>")
    body_lines.append(DESCRIPTION_FOOTER_HTML)

    body_html = "\n".join(body_lines)

    # -------------------------
    # Metafield values
    # -------------------------
    album_cover_condtion_value = sleeve_cond
    album_condition_value = "Used"

    cond_parts: List[str] = []
    if media_cond:
        cond_parts.append(f"Media: {media_cond}")
    if sleeve_cond:
        cond_parts.append(f"Sleeve: {sleeve_cond}")
    condition_summary = "; ".join(cond_parts)

    condition_description_value = (
        str(input_row.get("Condition Description", "") or "").strip()
        or str(input_row.get("Notes", "") or "").strip()
    )

    # -------------------------
    # Main product row
    # -------------------------
    row: Dict[str, Any] = {
        "Handle": handle,
        "Title": full_title,
        "Description": body_html,
        "Vendor": label,
        "Product category": SHOPIFY_PRODUCT_CATEGORY,
        "Type": SHOPIFY_PRODUCT_TYPE,
        "Tags": tags,
        "Published": SHOPIFY_PUBLISHED,
        "Status": SHOPIFY_PRODUCT_STATUS,
        "Option1 Name": SHOPIFY_OPTION1_NAME,
        "Option1 Value": SHOPIFY_OPTION1_VALUE,
        "Option2 Name": "",
        "Option2 Value": "",
        "Option3 Name": "",
        "Option3 Value": "",
        "Variant Price": price_str_out,
        "Variant Compare At Price": "",
        "Cost per item": "",
        "Variant SKU": "",
        "Variant Barcode": "",
        "Variant Inventory Tracker": "shopify",
        "Variant Inventory Policy": "deny",
        "Variant Inventory Qty": 1,
        "Variant Fulfillment Service": SHOPIFY_VARIANT_FULFILLMENT_SERVICE,
        "Variant Requires Shipping": SHOPIFY_VARIANT_REQUIRES_SHIPPING,
        "Variant Taxable": SHOPIFY_VARIANT_TAXABLE,
        "Image Src": primary_image_url,
        "Image Position": 1,
        "Image Alt Text": full_title,
        "SEO Title": seo_title,
        "SEO Description": seo_description,
        "Pricing Strategy Used": pricing_result.strategy_code,
        "Pricing Notes": pricing_result.notes,
        "Variant Weight Unit": "lb",
        "Variant Weight": pounds if pounds is not None else "",
        # Product metafields (full product CSV)
        "product.metafields.custom.shop_signage": shop_signage,
        "product.metafields.custom.album_cover_condtion": album_cover_condtion_value,
        "product.metafields.custom.album_condition": album_condition_value,
        "product.metafields.custom.condition": condition_summary,
        "product.metafields.custom.condition_description": condition_description_value,
        # Misprint diagnostics
        "Label_Misprint_Suspected": "TRUE" if mis_suspected else "FALSE",
        "Label_Misprint_Reasons": mis_reasons,
        # OCR / label diagnostics
        "Ocr_Catalog": input_row.get("Ocr_Catalog", ""),
        "Ocr_Label": input_row.get("Ocr_Label", ""),
        "Ocr_Year": input_row.get("Ocr_Year", ""),
        "Ocr_StereoMono": input_row.get("Ocr_StereoMono", ""),
        "Ocr_Format_Flags": input_row.get("Ocr_Format_Flags", ""),
        "Ocr_Tracks": input_row.get("Ocr_Tracks", ""),
        "Ocr_Notes": input_row.get("Ocr_Notes", ""),
        "Ocr_Scan_Confidence": input_row.get("Ocr_Scan_Confidence", ""),
        "Label_Catalog_Number": input_row.get("Label_Catalog_Number", ""),
    }

    rows: List[Dict[str, Any]] = [row]

    # If we have a center label photo, create a second "image-only" row
    if center_label_photo:
        img_row = {k: "" for k in row.keys()}
        img_row["Handle"] = handle
        img_row["Image Src"] = center_label_photo
        img_row["Image Position"] = 2  # keep cover as position 1
        img_row["Image Alt Text"] = full_title
        rows.append(img_row)

    # Metafield-only row for the metafields CSV
    metafield_row: Dict[str, Any] = {
        "Handle": handle,
        "product.metafields.custom.shop_signage": shop_signage,
        "product.metafields.custom.album_cover_condtion": album_cover_condtion_value,
        "product.metafields.custom.album_condition": album_condition_value,
        "product.metafields.custom.condition": condition_summary,
        "product.metafields.custom.condition_description": condition_description_value,
    }

    return rows, metafield_row


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------


def process_file(
    input_path: Path,
    discogs_token: str,
    output_matched: Path,
    output_not_matched: Path,
    output_metafields: Path,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    """
    Load the input CSV/XLSX, process each row, and write output CSVs.
    """
    logger.info("Loading input file: %s", input_path)
    if input_path.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(input_path)
    else:
        df = pd.read_csv(input_path)

    records = df.to_dict(orient="records")
    total_rows = len(records)
    logger.info("Loaded %d rows from input.", total_rows)

    matched_rows: List[Dict[str, Any]] = []
    unmatched_rows: List[Dict[str, Any]] = []
    metafield_rows: List[Dict[str, Any]] = []

    handle_registry: Dict[str, int] = {}

    for idx, row in enumerate(records, start=1):
        artist = str(row.get(COL_ARTIST, "")).strip()
        title = str(row.get(COL_TITLE, "")).strip()
        country = str(row.get(COL_COUNTRY, "")).strip() or None
        catalog = str(row.get(COL_CATALOG, "")).strip() or None

        year_raw = row.get(COL_TYPE, "")
        year_val: Optional[int] = None
        if year_raw:
            m = re.search(r"\b(19[0-9]{2}|20[0-2][0-9])\b", str(year_raw))
            if m:
                try:
                    year_val = int(m.group(1))
                except ValueError:
                    year_val = None

        if not artist or not title:
            unmatched_rows.append(
                {
                    "Reason": "Missing artist or title",
                    **row,
                }
            )
            if progress_callback:
                progress_callback(idx, total_rows)
            continue

        meta: Dict[str, Any] = {
            "Artist": artist,
            "Title": title,
            "Catalog Number": catalog,
            "Label": str(row.get("Label", "")).strip(),
            "Country": country,
            "Year": year_val,
        }

        # Enrich meta with label OCR (center label photo), if enabled
        meta = enrich_meta_with_label(meta, row, label_image_column=COL_CENTER_LABEL_PHOTO)

        # Copy OCR/label fields back onto the row so downstream code can use them
        for _key in [
            "Ocr_Catalog",
            "Ocr_Label",
            "Ocr_Year",
            "Ocr_StereoMono",
            "Ocr_Format_Flags",
            "Ocr_Tracks",
            "Ocr_Notes",
            "Ocr_Scan_Confidence",
            "Label_Catalog_Number",
        ]:
            if _key in meta:
                row[_key] = meta[_key]

        enriched_query = build_discogs_query_with_label(meta)
        logger.info("Row %d: searching Discogs for %s", idx, enriched_query)

        # Catalog numbers from sheet vs OCR
        catalog_sheet = (meta.get("Catalog Number") or catalog) or None
        catalog_ocr = (
            meta.get("Label_Catalog_Number")
            or meta.get("Ocr_Catalog")
            or None
        )

        # ------------------------------------------------------------------
        # FIRST ATTEMPT: normal search (sheet catalog first, then fallback)
        # ------------------------------------------------------------------
        primary_cat = catalog_sheet or catalog_ocr

        search_obj = discogs_search_release(
            discogs_token,
            artist,
            title,
            country,
            primary_cat,
            year_val,
        )

        # ------------------------------------------------------------------
        # SECOND ATTEMPT: if no result but we DO have an OCR catalog,
        # retry with OCR catalog only, and relax country/year.
        # ------------------------------------------------------------------
        if not search_obj and catalog_ocr and catalog_ocr != catalog_sheet:
            logger.info(
                "Row %d: no match on primary search; retrying with OCR catalog only: %s",
                idx,
                catalog_ocr,
            )

            search_obj = discogs_search_release(
                discogs_token,
                artist,
                title,
                None,         # relax country
                catalog_ocr,  # trust OCR catalog
                None,         # relax year
            )

        if not search_obj:
            unmatched_rows.append(
                {
                    "Reason": "Discogs search returned no results (after OCR retry)",
                    "Discogs_Query_Used": enriched_query,
                    "Catalog_Used_Sheet": catalog_sheet or "",
                    "Catalog_Used_OCR": catalog_ocr or "",
                    **row,
                }
            )
            if progress_callback:
                progress_callback(idx, total_rows)
            continue

        release_id = search_obj.get("id")
        if not release_id:
            logger.warning(
                "Search result for row %d has no release ID; skipping.", idx
            )
            unmatched_rows.append(
                {
                    "Reason": "Discogs search result missing release ID",
                    "Discogs_Query_Used": enriched_query,
                    **row,
                }
            )
            if progress_callback:
                progress_callback(idx, total_rows)
            continue

        details = discogs_get_release_details(discogs_token, release_id)
        if not details:
            logger.warning(
                "Could not fetch details for release %s (row %d); skipping.",
                release_id,
                idx,
            )
            unmatched_rows.append(
                {
                    "Reason": f"Failed to fetch Discogs release details for ID {release_id}",
                    "Discogs_Query_Used": enriched_query,
                    **row,
                }
            )
            if progress_callback:
                progress_callback(idx, total_rows)
            continue

        market_stats = discogs_get_marketplace_stats(discogs_token, release_id)
        if market_stats:
            details["_marketplace_stats"] = market_stats

        misprint_info = detect_label_misprint(meta, details)

        logger.info("Matched row %d to Discogs release %s", idx, release_id)

        shopify_rows, metafield_row = make_shopify_rows_for_record(
            row,
            search_obj,
            details,
            misprint_info,
            handle_registry,
        )
        matched_rows.extend(shopify_rows)
        metafield_rows.append(metafield_row)

        if progress_callback:
            progress_callback(idx, total_rows)

    # Write matched CSV (products)
    if matched_rows:
        logger.info("Writing matched output CSV (products): %s", output_matched)
        with output_matched.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(matched_rows[0].keys()))
            writer.writeheader()
            for r in matched_rows:
                writer.writerow(r)
    else:
        logger.info("No matched rows; not writing matched CSV.")

    # Write unmatched CSV
    if unmatched_rows:
        logger.info("Writing unmatched output CSV: %s", output_not_matched)
        with output_not_matched.open("w", newline="", encoding="utf-8") as f:
            fieldnames = list(unmatched_rows[0].keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in unmatched_rows:
                writer.writerow(r)
    else:
        logger.info("No unmatched rows; not writing unmatched CSV.")

    # Write metafields CSV
    if metafield_rows:
        logger.info("Writing metafields output CSV: %s", output_metafields)
        with output_metafields.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(metafield_rows[0].keys()))
            writer.writeheader()
            for r in metafield_rows:
                writer.writerow(r)
    else:
        logger.info("No metafield rows; not writing metafields CSV.")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


def run_gui() -> None:
    """
    Run a very simple Tkinter-based GUI to pick input file, token, and run
    the processing pipeline.
    """
    import tkinter as tk
    from tkinter import filedialog, ttk, scrolledtext, messagebox

    print_run_banner()

    root = tk.Tk()
    root.title(f"Discogs → Shopify Vinyl Import ({APP_VERSION})")

    settings = load_settings()

    mainframe = ttk.Frame(root, padding="8 8 8 8")
    mainframe.grid(row=0, column=0, sticky="NSEW")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    # Input file selection
    input_path_var = tk.StringVar(value=settings.get("last_input_path", ""))
    token_var = tk.StringVar(
        value=settings.get("last_discogs_token", os.getenv("DISCOGS_TOKEN", ""))
    )

    def browse_input() -> None:
        path = filedialog.askopenfilename(
            title="Select inventory file",
            filetypes=[
                ("Spreadsheet files", "*.xlsx *.xls *.csv"),
                ("All files", "*.*"),
            ],
        )
        if path:
            input_path_var.set(path)

    ttk.Label(mainframe, text="Input inventory file:").grid(
        row=0, column=0, sticky="W"
    )
    input_entry = ttk.Entry(mainframe, width=60, textvariable=input_path_var)
    input_entry.grid(row=0, column=1, sticky="WE")
    ttk.Button(mainframe, text="Browse…", command=browse_input).grid(
        row=0, column=2, sticky="W"
    )

    # Discogs token
    ttk.Label(mainframe, text="Discogs token:").grid(row=1, column=0, sticky="W")
    token_entry = ttk.Entry(mainframe, width=40, textvariable=token_var, show="*")
    token_entry.grid(row=1, column=1, sticky="WE")

    # Output directory (optional – default is same as input)
    output_dir_var = tk.StringVar(value=settings.get("last_output_dir", ""))

    def browse_output_dir() -> None:
        path = filedialog.askdirectory(title="Select output directory")
        if path:
            output_dir_var.set(path)

    ttk.Label(mainframe, text="Output directory (optional):").grid(
        row=2, column=0, sticky="W"
    )
    out_entry = ttk.Entry(mainframe, width=60, textvariable=output_dir_var)
    out_entry.grid(row=2, column=1, sticky="WE")
    ttk.Button(mainframe, text="Browse…", command=browse_output_dir).grid(
        row=2, column=2, sticky="W"
    )

    # Progress bar
    progress = ttk.Progressbar(
        mainframe, orient="horizontal", mode="determinate"
    )
    progress.grid(row=3, column=0, columnspan=3, sticky="WE", pady=(8, 4))

    # Log window
    log_text = scrolledtext.ScrolledText(
        mainframe, width=80, height=20, state="disabled"
    )
    log_text.grid(row=4, column=0, columnspan=3, sticky="NSEW")
    mainframe.rowconfigure(4, weight=1)

    # Redirect logging to the Tkinter text widget as well
    class TextHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            msg = self.format(record)
            log_text.configure(state="normal")
            log_text.insert(tk.END, msg + "\n")
            log_text.configure(state="disabled")
            log_text.see(tk.END)

    handler = TextHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logging.getLogger().addHandler(handler)

    # Start button
    def start_processing() -> None:
        input_path_str = input_path_var.get().strip()
        token_str = token_var.get().strip()

        if not input_path_str:
            messagebox.showerror("Error", "Please select an input file.")
            return
        if not token_str:
            messagebox.showerror("Error", "Please enter your Discogs token.")
            return

        input_path = Path(input_path_str)
        if not input_path.exists():
            messagebox.showerror(
                "Error", f"Input file does not exist:\n{input_path}"
            )
            return

        # Save settings
        new_settings = {
            "last_input_path": input_path_str,
            "last_discogs_token": token_str,
            "last_output_dir": output_dir_var.get().strip(),
        }
        save_settings(new_settings)
        persist_discogs_token_to_env(token_str)

        # Determine output paths
        out_dir = (
            Path(output_dir_var.get().strip())
            if output_dir_var.get().strip()
            else input_path.parent
        )
        output_matched = out_dir / (
            input_path.stem + "_Output for matched records.csv"
        )
        output_not_matched = out_dir / (input_path.stem + "_Not_Matched.csv")
        output_metafields = out_dir / (
            input_path.stem + "_Metafields for matched records.csv"
        )

        logger.info("Input file: %s", input_path)
        logger.info("Matched products output: %s", output_matched)
        logger.info("Not-matched output: %s", output_not_matched)
        logger.info("Metafields output: %s", output_metafields)
        logger.info("Starting processing... This may take a while.\n")

        progress["value"] = 0

        def progress_cb(done: int, total: int) -> None:
            if total > 0:
                pct = int((done / total) * 100)
                progress["value"] = pct
                root.update_idletasks()

        try:
            process_file(
                input_path=input_path,
                discogs_token=token_str,
                output_matched=output_matched,
                output_not_matched=output_not_matched,
                output_metafields=output_metafields,
                progress_callback=progress_cb,
            )
        except Exception as e:
            logger.exception("Error during processing: %s", e)
            messagebox.showerror("Error", f"An error occurred:\n{e}")
            return

        logger.info("\nDone.")
        messagebox.showinfo(
            "Complete",
            "Processing complete.\n\n"
            "Files created:\n"
            f"  - {output_matched.name}\n"
            f"  - {output_not_matched.name}\n"
            f"  - {output_metafields.name}\n",
        )

    ttk.Button(mainframe, text="Start", command=start_processing).grid(
        row=5, column=0, columnspan=3, pady=(8, 4)
    )

    root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # Default to GUI if no args or --gui present
    if "--gui" in argv or not argv:
        run_gui()
        return 0

    # CLI mode
    import argparse

    parser = argparse.ArgumentParser(
        description="Discogs → Shopify vinyl import"
    )
    parser.add_argument("input_file", help="Input inventory CSV or XLSX file")
    parser.add_argument(
        "--discogs-token",
        help="Discogs API token (or set DISCOGS_TOKEN env var)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory (default: directory of input file)",
        default=None,
    )
    args = parser.parse_args(argv)

    token = args.discogs_token or os.getenv("DISCOGS_TOKEN")
    if not token:
        print(
            "Error: Discogs token not provided. "
            "Use --discogs-token or set DISCOGS_TOKEN.",
            file=sys.stderr,
        )
        return 1

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(
            f"Error: input file does not exist: {input_path}",
            file=sys.stderr,
        )
        return 1

    out_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    output_matched = out_dir / (
        input_path.stem + "_Output for matched records.csv"
    )
    output_not_matched = out_dir / (input_path.stem + "_Not_Matched.csv")
    output_metafields = out_dir / (
        input_path.stem + "_Metafields for matched records.csv"
    )

    print_run_banner()

    process_file(
        input_path=input_path,
        discogs_token=token,
        output_matched=output_matched,
        output_not_matched=output_not_matched,
        output_metafields=output_metafields,
        progress_callback=None,
    )

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
