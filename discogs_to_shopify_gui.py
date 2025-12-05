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

v1.2.8 – 2025-12-02
    - Removed image watermarking logic and image_watermark dependency.
    - Added metafield product.metafields.custom.uses_stock_photo to flag Discogs
      cover images as stock photos (in both product CSV and metafields CSV).
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
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

# ================================================================
# 2. Third-Party Imports
# ================================================================
import requests
import pandas as pd
from PIL import Image  # retained in case of future image handling
from slugify import slugify

# ================================================================
# 3. Local Project Imports
# ================================================================
import discogs_client
import ebay_search
import pricing
from label_ocr import (
    enrich_meta_with_label,
    build_discogs_query_with_label,
    detect_label_misprint,
)
from uf_logging import setup_logging, get_logger

# ---------------------------------------------------------------------------
# Default Paths / App Structure
# ---------------------------------------------------------------------------
DEFAULT_BASE_DIR = Path.home() / "Documents" / "UnusualFindsAlbumApp"
INPUT_DIR_NAME = "input"
OUTPUT_DIR_NAME = "output"
LOGS_DIR_NAME = "logs"
CACHE_DIR_NAME = "cache"
PROCESSED_DIR_NAME = "processed"

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


def normalize_inventory_date(value: Any) -> str:
    """
    Normalize an inventory date to YYYY-MM-DD (Shopify date metafield).
    Fallback to today's date if missing or unparseable.
    """
    today = dt.date.today().isoformat()
    if value is None:
        return today

    try:
        import pandas as _pd  # type: ignore
        if _pd.isna(value):  # pragma: no cover
            return today
    except Exception:
        pass

    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()

    s = str(value).strip()
    if not s:
        return today

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue

    try:
        return dt.date.fromisoformat(s).isoformat()
    except Exception:
        return today


def _looks_like_person(name: str) -> bool:
    """
    Heuristic: treat as person if 2-4 tokens and no band/orchestra keywords.
    """
    if not name:
        return False
    n_upper = name.upper()
    band_keywords = [
        "&",
        " AND ",
        "BAND",
        "ORCHESTRA",
        "PHILHARMONIC",
        "SYMPHONY",
        "ENSEMBLE",
        "CHOIR",
        "CHORUS",
        "QUARTET",
        "TRIO",
        "DUO",
        "COMPANY",
        "PLAYERS",
        "SINGERS",
    ]
    for kw in band_keywords:
        if kw in n_upper:
            return False
    tokens = [t for t in name.strip().split() if t]
    return 2 <= len(tokens) <= 4


def format_person_name(name: str) -> str:
    """
    Flip 'First Middle Last' -> 'Last, First Middle'. Preserve existing commas.
    """
    if not name:
        return name
    if "," in name:
        return name.strip()
    parts = [p for p in name.strip().split() if p]
    if len(parts) < 2:
        return name.strip()
    last = parts[-1]
    first_middle = " ".join(parts[:-1])
    return f"{last}, {first_middle}"


def extract_composer(release_details: Dict[str, Any]) -> Optional[str]:
    """
    Attempt to pull a composer from Discogs extraartists with role containing 'Composed'.
    """
    extras = release_details.get("extraartists") or []
    for ex in extras:
        role = str(ex.get("role", "")).lower()
        name = str(ex.get("name", "")).strip()
        if "composed" in role and name:
            return name
    return None


def build_shop_artist(artist_name: str, release_details: Dict[str, Any]) -> str:
    """
    Compute Shop_Artist:
      - For orchestras/conductors, prefer composer if available.
      - For apparent persons, flip to Last, First Middle.
      - For bands/groups, leave as-is (but still normalized 'The X' later if desired).
    """
    composer = extract_composer(release_details)
    upper_artist = artist_name.upper()
    orchestra_words = ["ORCHESTRA", "PHILHARMONIC", "SYMPHONY", "CONDUCTOR"]

    if composer and any(w in upper_artist for w in orchestra_words):
        return composer

    if _looks_like_person(artist_name):
        return format_person_name(artist_name)

    return artist_name.strip()


def normalize_discogs_suggestion_key(key: str) -> Optional[str]:
    """
    Map Discogs price suggestion condition labels to our normalized ladder.
    """
    k = key.lower()
    if "mint (m)" in k and "near" not in k:
        return "M"
    if "near mint" in k or "m-" in k:
        return "NM"
    if "vg+" in k or "very good plus" in k or "excellent" in k:
        return "VG+"
    if "very good" in k and "+" not in k:
        return "VG"
    if "good plus" in k or "g+" in k:
        return "G+"
    if k.startswith("good"):
        return "G"
    if "fair" in k or "poor" in k:
        return "F/P"
    return None


def discogs_price_from_suggestions(
    media_condition: str, suggestions: Dict[str, Any]
) -> Optional[float]:
    """
    Pick a price suggestion based on media condition.

    - Exact match: use that value.
    - Otherwise: take the next lower condition (if present) minus 10%.
    - If no lower condition is available: take the next higher condition minus 10%.
    - Sleeve condition is ignored.
    """
    if not suggestions:
        return None

    norm_media = pricing.normalize_condition(media_condition)
    if not norm_media:
        return None

    # Normalize suggestion keys to ladder values
    norm_map: Dict[str, float] = {}
    for key, obj in suggestions.items():
        if not isinstance(obj, dict):
            continue
        norm_key = normalize_discogs_suggestion_key(str(key))
        if not norm_key:
            continue
        try:
            val = obj.get("value")
            price_val = float(val) if val is not None else None
        except (TypeError, ValueError):
            price_val = None
        if price_val is not None:
            norm_map[norm_key] = price_val

    if not norm_map:
        return None

    ladder = pricing.CONDITION_LADDER
    if norm_media in norm_map:
        return norm_map[norm_media]

    # Search next lower condition first
    try:
        idx = ladder.index(norm_media)
    except ValueError:
        return None

    for j in range(idx + 1, len(ladder)):
        cond = ladder[j]
        if cond in norm_map:
            return norm_map[cond] * 0.9

    # Then search next higher condition
    for j in range(idx - 1, -1, -1):
        cond = ladder[j]
        if cond in norm_map:
            return norm_map[cond] * 0.9

    return None


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




def sanitize_catalog_for_search(cat: Optional[str]) -> Optional[str]:
    """Clean a catalog number for searching.

    If it looks like it's just a year (e.g. 1969, 1972), ignore it — that's
    almost always an OCR misread from the center label.
    """
    if not cat:
        return None
    s = str(cat).strip()
    # Collapse spaces/hyphens just for the year check
    compact = re.sub(r"[\s-]", "", s)
    if re.fullmatch(r"(19[0-9]{2}|20[0-2][0-9])", compact):
        return None
    return s or None




# ---------------------------------------------------------------------------
# Settings helpers (load/save last used token & file)
# ---------------------------------------------------------------------------


def print_run_banner() -> None:
    """Print a line showing when this script is running."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    print(f"[discogs_to_shopify] Run at {now}", flush=True)


def open_path(path: Path) -> None:
    """
    Open a file or folder in the platform file browser.
    """
    if not path:
        return
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as e:
        try:
            logger.warning("Failed to open path %s: %s", path, e)
        except NameError:
            print(f"Failed to open path {path}: {e}")

def ensure_base_dirs(base_dir: Path) -> Dict[str, Path]:
    """
    Ensure the base directory structure exists and return key paths.
    """
    input_dir = base_dir / INPUT_DIR_NAME
    output_dir = base_dir / OUTPUT_DIR_NAME
    logs_dir = base_dir / LOGS_DIR_NAME
    cache_dir = base_dir / CACHE_DIR_NAME
    processed_dir = input_dir / PROCESSED_DIR_NAME

    for d in [base_dir, input_dir, output_dir, logs_dir, cache_dir, processed_dir]:
        d.mkdir(parents=True, exist_ok=True)

    return {
        "base": base_dir,
        "input": input_dir,
        "output": output_dir,
        "logs": logs_dir,
        "cache": cache_dir,
        "processed": processed_dir,
    }


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
            data = json.load(f)
    except Exception:
        data = {}
    if "base_dir" not in data or not data.get("base_dir"):
        data["base_dir"] = str(DEFAULT_BASE_DIR)
    return data


def save_settings(settings: Dict[str, Any]) -> None:
    path = get_settings_path()
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        try:
            logger.warning("Failed to save settings: %s", e)
        except NameError:
            print(f"Failed to save settings: {e}")


# ---------------------------------------------------------------------------
# App metadata and bootstrap (base dirs + logging)
# ---------------------------------------------------------------------------
DISCOGS_API_BASE = "https://api.discogs.com"
APP_VERSION = "v1.3.0"
DISCOGS_USER_AGENT = (
    f"UnusualFindsDiscogsToShopify/{APP_VERSION} +https://unusualfinds.com"
)

_boot_settings = load_settings()
BASE_DIR = Path(_boot_settings.get("base_dir", str(DEFAULT_BASE_DIR))).expanduser()
DIRS = ensure_base_dirs(BASE_DIR)
_boot_settings["base_dir"] = str(BASE_DIR)
save_settings(_boot_settings)

log_file = setup_logging(log_root=str(DIRS["logs"]))
logger = get_logger(__name__)
logger.info("discogs_to_shopify_gui.py started. Log file: %s", log_file)


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

def clean_price(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except:
        return None
  
# ---------------------------------------------------------------------------
# Row processing
# ---------------------------------------------------------------------------


def make_shopify_rows_for_record(
    input_row: Dict[str, Any],
    release_search_obj: Dict[str, Any],
    release_details: Dict[str, Any],
    misprint_info: Optional[Dict[str, Any]],
    handle_registry: Dict[str, int],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], float, Optional[float]]:
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
    inventory_date = normalize_inventory_date(
        input_row.get("Inventory Date") or input_row.get("inventory_date")
    )

    discogs_release_id = release_details.get("id")
    discogs_url = (
        f"https://www.discogs.com/release/{discogs_release_id}"
        if discogs_release_id
        else ""
    )

    # --- Weight
    # --- Barcode / SKU ---
    discogs_barcode: Optional[str] = None
    for ident in release_details.get("identifiers") or []:
        id_type = str(ident.get("type", "")).strip().lower()
        if id_type == "barcode":
            val = str(ident.get("value", "")).strip()
            if val:
                discogs_barcode = val
                break

    # Pull SKU from input if present
    sku_raw = (
        input_row.get("SKU")
        or input_row.get("Sku")
        or input_row.get("sku")
        or input_row.get("Variant SKU")
        or ""
    )
    sku = str(sku_raw).strip()

    # Weight estimation
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

    # --- Discogs price suggestions (condition-based) ---
    price_suggestions = release_details.get("_price_suggestions") or {}
    discogs_suggested_price = discogs_price_from_suggestions(media_cond, price_suggestions)
    logger.info(
        "Discogs price suggestion (media=%s): %s",
        media_cond,
        discogs_suggested_price,
    )

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
        reference_price=clean_price(price_str),
        discogs_suggested=discogs_suggested_price,
        discogs_high=discogs_high_price,
        discogs_median=None,
        discogs_last=None,
        discogs_low=None,
        comparable_price=None,
        ebay_sold=ebay_sold_listings,
        ebay_active=ebay_active_listings,
    )

    # Compute price using the pricing engine
    pricing_result = pricing.compute_price(ctx)
    price = pricing_result.final_price
    price_str_out = f"{price:.2f}"
    ref_price_val = clean_price(price_str)

    # Unique handle
    base_handle = slugify_handle(f"{artist_display} {title} {year}".strip())
    if base_handle not in handle_registry:
        handle_registry[base_handle] = 1
        handle = base_handle
    else:
        handle_registry[base_handle] += 1
        handle = f"{base_handle}-{handle_registry[base_handle]}"

    # ------------------------------------------------------------
    # Main cover image: prefer release details, fall back to search.
    # We now **do not** watermark; we keep the Discogs URL as-is and
    # flag it via product.metafields.custom.uses_stock_photo.
    # ------------------------------------------------------------
    discogs_cover_url = extract_primary_image_url(release_details)
    if not discogs_cover_url:
        discogs_cover_url = extract_primary_image_url(release_search_obj or {})

    # Sleeve in poor/fair condition? Prefer the label photo as primary to avoid a pristine stock image.
    sleeve_upper = sleeve_cond.upper()
    sleeve_is_poor = any(
        token in sleeve_upper for token in ["POOR", "FAIR", "F/P", "(P)", "(F)"]
    )

    primary_image_url = discogs_cover_url
    additional_images: List[str] = []

    if sleeve_is_poor and center_label_photo:
        primary_image_url = center_label_photo
        if discogs_cover_url:
            additional_images.append(discogs_cover_url)
        uses_stock_photo_value = "FALSE"
    else:
        # Default: use Discogs cover if available; otherwise center label if present.
        if not primary_image_url and center_label_photo:
            primary_image_url = center_label_photo
        elif primary_image_url and center_label_photo:
            additional_images.append(center_label_photo)
        uses_stock_photo_value = "TRUE" if primary_image_url == discogs_cover_url else "FALSE"

    shop_artist = build_shop_artist(artist_display, release_details)

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
        "Variant SKU": sku,
        "Variant Barcode": discogs_barcode or sku,
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
        "product.metafields.custom.uses_stock_photo": uses_stock_photo_value,
        "product.metafields.custom.shop_artist": shop_artist,
        "product.metafields.custom.inventory_date": inventory_date,
        # Misprint diagnostics
        "Label_Misprint_Suspected": "TRUE" if mis_suspected else "FALSE",
        "Label_Misprint_Reasons": mis_reasons,
        # OCR / label diagnostics
        "Ocr_Catalog": input_row.get("Ocr_Catalog", ""),
        "Ocr_Matrix": input_row.get("Ocr_Matrix", ""),
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

    # Additional image rows
    pos = 2
    for img in additional_images:
        if not img:
            continue
        img_row = {k: "" for k in row.keys()}
        img_row["Handle"] = handle
        img_row["Image Src"] = img
        img_row["Image Position"] = pos
        img_row["Image Alt Text"] = full_title
        rows.append(img_row)
        pos += 1

    # Metafield-only row for the metafields CSV
    metafield_row: Dict[str, Any] = {
        "Handle": handle,
        "product.metafields.custom.shop_signage": shop_signage,
        "product.metafields.custom.album_cover_condtion": album_cover_condtion_value,
        "product.metafields.custom.album_condition": album_condition_value,
        "product.metafields.custom.condition": condition_summary,
        "product.metafields.custom.condition_description": condition_description_value,
        "product.metafields.custom.uses_stock_photo": uses_stock_photo_value,
        "product.metafields.custom.shop_artist": shop_artist,
        "product.metafields.custom.inventory_date": inventory_date,
    }

    return rows, metafield_row, price, ref_price_val


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
) -> Dict[str, Any]:
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
    retry_rows: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    total_final_price = 0.0
    total_reference_price = 0.0

    handle_registry: Dict[str, int] = {}

    def process_single_row(idx: int, row: Dict[str, Any], allow_retry: bool = True) -> None:
        nonlocal total_final_price, total_reference_price
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
            return

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
        catalog_sheet_raw = (meta.get("Catalog Number") or catalog) or None
        catalog_ocr_raw = (
            meta.get("Label_Catalog_Number")
            or meta.get("Ocr_Catalog")
            or None
        )

        # Basic sanity cleanup: drop pure-year "catnos" like "1969"
        catalog_sheet = sanitize_catalog_for_search(catalog_sheet_raw)
        catalog_ocr = sanitize_catalog_for_search(catalog_ocr_raw)

        # ------------------------------------------------------------------
        # FIRST ATTEMPT: *loose* search – artist + title (+ country).
        # Do NOT filter by catalog or year here; that over-constrains things
        # and can break cases where spreadsheet/OCR year or catalog are off.
        # ------------------------------------------------------------------
        search_obj = discogs_search_release(
            discogs_token,
            artist,
            title,
            country,
            None,   # no catalog filter
            None,   # no year filter
        )

        # ------------------------------------------------------------------
        # SECOND ATTEMPT: if no result and we DO have an OCR catalog,
        # retry with OCR catalog, relaxing country/year.
        # ------------------------------------------------------------------
        if not search_obj and catalog_ocr:
            logger.info(
                "Row %d: no match on primary search; retrying with OCR catalog only: %s",
                idx,
                catalog_ocr,
            )
            search_obj = discogs_search_release(
                discogs_token,
                artist,
                title,
                None,          # relax country
                catalog_ocr,   # OCR-derived catalog
                None,          # relax year
            )

        # ------------------------------------------------------------------
        # THIRD ATTEMPT: if still no result, try the sheet catalog only
        # (if it's different from the OCR catalog).
        # ------------------------------------------------------------------
        if not search_obj and catalog_sheet and catalog_sheet != catalog_ocr:
            logger.info(
                "Row %d: still no result; retrying with sheet catalog only: %s",
                idx,
                catalog_sheet,
            )
            search_obj = discogs_search_release(
                discogs_token,
                artist,
                title,
                None,          # relax country
                catalog_sheet, # sheet-derived catalog
                None,          # relax year
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
            return

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
            return

        # brief pause to ease rate limits
        time.sleep(0.2)

        details = discogs_get_release_details(discogs_token, release_id)
        if not details:
            logger.warning(
                "Could not fetch details for release %s (row %d).",
                release_id,
                idx,
            )
            if allow_retry:
                retry_rows.append((idx, row, enriched_query))
            else:
                unmatched_rows.append(
                    {
                        "Reason": f"Failed to fetch Discogs release details for ID {release_id}",
                        "Discogs_Query_Used": enriched_query,
                        **row,
                    }
                )
            if progress_callback:
                progress_callback(idx, total_rows)
            return

        market_stats = discogs_get_marketplace_stats(discogs_token, release_id)
        if market_stats:
            details["_marketplace_stats"] = market_stats

        price_suggestions = discogs_client.get_price_suggestions(
            discogs_token, release_id
        )
        if price_suggestions:
            details["_price_suggestions"] = price_suggestions

        misprint_info = detect_label_misprint(meta, details)

        logger.info("Matched row %d to Discogs release %s", idx, release_id)

        shopify_rows, metafield_row, final_price_val, ref_price_val = make_shopify_rows_for_record(
            row,
            search_obj,
            details,
            misprint_info,
            handle_registry,
        )
        matched_rows.extend(shopify_rows)
        metafield_rows.append(metafield_row)
        total_final_price += float(final_price_val or 0.0)
        if ref_price_val is not None:
            total_reference_price += float(ref_price_val)

        if progress_callback:
            progress_callback(idx, total_rows)

    # First pass
    for idx, row in enumerate(records, start=1):
        process_single_row(idx, row, allow_retry=True)

    # Second pass for rows that failed Discogs details
    if retry_rows:
        logger.info("Retrying %d rows with extended backoff after initial failures...", len(retry_rows))
        time.sleep(2.0)
        for idx, row, _enriched_query in retry_rows:
            process_single_row(idx, row, allow_retry=False)

    # Write matched CSV (products)
    if matched_rows:
        logger.info("Writing matched output CSV (products): %s", output_matched)
        with output_matched.open("w", newline="", encoding="utf-8") as f:
            fieldnames = sorted({k for r in matched_rows for k in r.keys()})
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in matched_rows:
                writer.writerow(r)
    else:
        logger.info("No matched rows; not writing matched CSV.")

    # Write unmatched CSV
    if unmatched_rows:
        logger.info("Writing unmatched output CSV: %s", output_not_matched)
        with output_not_matched.open("w", newline="", encoding="utf-8") as f:
            fieldnames = sorted({k for r in unmatched_rows for k in r.keys()})
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in unmatched_rows:
                writer.writerow(r)
    else:
        logger.info("No unmatched rows; not writing unmatched CSV.")

    summary = {
        "total_rows": total_rows,
        "matched_count": len(metafield_rows),
        "unmatched_count": len(unmatched_rows),
        "total_final_price": round(total_final_price, 2),
        "total_reference_price": round(total_reference_price, 2),
        "price_diff": round(total_final_price - total_reference_price, 2),
    }
    logger.info(
        "Summary: total=%s matched=%s unmatched=%s final_sum=%.2f ref_sum=%.2f diff=%.2f",
        summary["total_rows"],
        summary["matched_count"],
        summary["unmatched_count"],
        summary["total_final_price"],
        summary["total_reference_price"],
        summary["price_diff"],
    )

    return summary
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
    Run a Tkinter-based GUI to pick input file, token, and run the processing
    pipeline. Files default to the app's base folder tree so users don't have
    to browse each time.
    """
    import tkinter as tk
    from tkinter import filedialog, ttk, scrolledtext, messagebox

    print_run_banner()

    root = tk.Tk()
    root.title(f"Discogs -> Shopify Vinyl Import ({APP_VERSION})")

    settings = load_settings()

    mainframe = ttk.Frame(root, padding="8 8 8 8")
    mainframe.grid(row=0, column=0, sticky="NSEW")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    base_dir_var = tk.StringVar(value=str(BASE_DIR))

    last_outputs: Dict[str, Optional[Path]] = {
        "matched": None,
        "not_matched": None,
        "metafields": None,
        "log": Path(log_file),
    }

    def pick_first_input() -> str:
        for pattern in ("*.xlsx", "*.xls", "*.csv"):
            files = sorted(DIRS["input"].glob(pattern))
            if files:
                return str(files[0])
        return ""

    input_path_var = tk.StringVar(
        value=settings.get("last_input_path", "") or pick_first_input()
    )
    token_var = tk.StringVar(
        value=settings.get("last_discogs_token", os.getenv("DISCOGS_TOKEN", ""))
    )

    header = ttk.Frame(mainframe)
    header.grid(row=0, column=0, columnspan=3, sticky="WE", pady=(0, 8))
    header.columnconfigure(1, weight=1)
    ttk.Label(header, text="Base folder:").grid(row=0, column=0, sticky="W")
    ttk.Label(header, textvariable=base_dir_var).grid(row=0, column=1, sticky="W")

    def open_settings_dialog() -> None:
        dlg = tk.Toplevel(root)
        dlg.title("Settings")
        dlg.grab_set()

        new_base_var = tk.StringVar(value=base_dir_var.get())
        ttk.Label(dlg, text="Base folder for input/output/logs:").grid(
            row=0, column=0, sticky="W", padx=8, pady=(8, 2)
        )
        ttk.Entry(dlg, width=60, textvariable=new_base_var).grid(
            row=1, column=0, padx=8, pady=2, sticky="WE"
        )

        def browse_base() -> None:
            path = filedialog.askdirectory(
                title="Select base folder", initialdir=new_base_var.get()
            )
            if path:
                new_base_var.set(path)

        ttk.Button(dlg, text="Browse", command=browse_base).grid(
            row=1, column=1, padx=4, pady=2, sticky="W"
        )

        def save_base() -> None:
            nonlocal settings
            new_base = Path(new_base_var.get()).expanduser()
            ensure_base_dirs(new_base)
            settings["base_dir"] = str(new_base)
            save_settings(settings)
            base_dir_var.set(str(new_base))
            globals()["BASE_DIR"] = new_base
            globals()["DIRS"] = ensure_base_dirs(new_base)
            dlg.destroy()

        ttk.Button(dlg, text="Save", command=save_base).grid(
            row=2, column=0, padx=8, pady=8, sticky="W"
        )
        ttk.Button(dlg, text="Cancel", command=dlg.destroy).grid(
            row=2, column=1, padx=4, pady=8, sticky="E"
        )

    ttk.Button(header, text="Settings", command=open_settings_dialog).grid(
        row=0, column=2, sticky="E"
    )

    ttk.Label(
        mainframe,
        text=(
            f"Place inventory files in {DIRS['input']}.\n"
            'Outputs and logs will be written under the base folder automatically.'
        ),
    ).grid(row=1, column=0, columnspan=3, sticky="W", pady=(0, 6))

    def browse_input() -> None:
        path = filedialog.askopenfilename(
            initialdir=str(DIRS["input"]),
            title="Select inventory file",
            filetypes=[
                ("Spreadsheet files", "*.xlsx *.xls *.csv"),
                ("All files", "*.*"),
            ],
        )
        if path:
            input_path_var.set(path)

    ttk.Label(mainframe, text="Input inventory file:").grid(
        row=2, column=0, sticky="W"
    )
    ttk.Entry(mainframe, width=60, textvariable=input_path_var).grid(
        row=2, column=1, sticky="WE"
    )
    ttk.Button(mainframe, text="Browse", command=browse_input).grid(
        row=2, column=2, sticky="W"
    )
    ttk.Button(mainframe, text="Open input folder", command=lambda: open_path(DIRS["input"])).grid(
        row=3, column=0, sticky="W", pady=(2, 8)
    )

    ttk.Label(mainframe, text="Discogs token:").grid(row=4, column=0, sticky="W")
    ttk.Entry(mainframe, width=40, textvariable=token_var, show="*").grid(
        row=4, column=1, sticky="WE"
    )

    progress = ttk.Progressbar(
        mainframe, orient="horizontal", mode="determinate"
    )
    progress.grid(row=5, column=0, columnspan=3, sticky="WE", pady=(8, 4))

    log_text = scrolledtext.ScrolledText(
        mainframe, width=80, height=20, state="disabled"
    )
    log_text.grid(row=6, column=0, columnspan=3, sticky="NSEW")
    mainframe.rowconfigure(6, weight=1)

    def clear_log_window() -> None:
        log_text.configure(state="normal")
        log_text.delete("1.0", tk.END)
        log_text.configure(state="disabled")

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

    output_msg_var = tk.StringVar(value="")

    def open_matched() -> None:
        if last_outputs["matched"]:
            open_path(last_outputs["matched"])

    def open_output_folder() -> None:
        open_path(DIRS["output"])

    def open_logs_folder() -> None:
        open_path(DIRS["logs"])

    buttons_frame = ttk.Frame(mainframe)
    buttons_frame.grid(row=8, column=0, columnspan=3, sticky="WE", pady=(6, 0))
    ttk.Button(buttons_frame, text="Open matched CSV", command=open_matched).grid(
        row=0, column=0, padx=4, sticky="W"
    )
    ttk.Button(buttons_frame, text="Open output folder", command=open_output_folder).grid(
        row=0, column=1, padx=4, sticky="W"
    )
    ttk.Button(buttons_frame, text="Open logs folder", command=open_logs_folder).grid(
        row=0, column=2, padx=4, sticky="W"
    )

    ttk.Label(mainframe, textvariable=output_msg_var).grid(
        row=7, column=0, columnspan=3, sticky="W", pady=(4, 0)
    )

    def start_processing() -> None:
        input_path_str = input_path_var.get().strip()
        token_str = token_var.get().strip()

        clear_log_window()

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

        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

        settings.update(
            {
                "last_input_path": input_path_str,
                "last_discogs_token": token_str,
            }
        )
        save_settings(settings)
        persist_discogs_token_to_env(token_str)

        output_matched = DIRS["output"] / (
            input_path.stem + f"_Output for matched records_Completed_{ts}.csv"
        )
        output_not_matched = DIRS["output"] / (
            input_path.stem + f"_Not_Matched_Completed_{ts}.csv"
        )
        output_metafields = DIRS["output"] / (
            input_path.stem + f"_Metafields for matched records_Completed_{ts}.csv"
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
            summary = process_file(
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

        try:
            dest = DIRS["processed"] / (
                input_path.stem + f"_Processed_{ts}{input_path.suffix}"
            )
            shutil.move(str(input_path), dest)
            logger.info("Moved processed input to %s", dest)
        except Exception as e:
            logger.warning("Could not move processed input: %s", e)

        last_outputs["matched"] = output_matched
        last_outputs["not_matched"] = output_not_matched
        last_outputs["metafields"] = output_metafields

        msg = (
            "Processing complete.\n"
            f"Matched: {output_matched}\n"
            f"Not matched: {output_not_matched}\n"
            f"Metafields: {output_metafields}\n"
            f"Totals: processed={summary['total_rows']}, matched={summary['matched_count']}, "
            f"unmatched={summary['unmatched_count']}, "
            f"final_sum=${summary['total_final_price']:.2f}, "
            f"ref_sum=${summary['total_reference_price']:.2f}, "
            f"diff=${summary['price_diff']:.2f}\n"
            f"Logs: {DIRS['logs']}\n"
        )
        output_msg_var.set(msg)

        logger.info("\nDone.")
        messagebox.showinfo(
            "Complete",
            "Processing complete.\n\n"
            "Files created:\n"
            f"  - {output_matched.name}\n"
            f"  - {output_not_matched.name}\n"
            f"  - {output_metafields.name}\n\n"
            "Summary:\n"
            f"  Processed: {summary['total_rows']}\n"
            f"  Matched: {summary['matched_count']}\n"
            f"  Unmatched: {summary['unmatched_count']}\n"
            f"  Final sum: ${summary['total_final_price']:.2f}\n"
            f"  Ref sum: ${summary['total_reference_price']:.2f}\n"
            f"  Difference: ${summary['price_diff']:.2f}\n",
        )

    ttk.Button(mainframe, text="Start", command=start_processing).grid(
        row=9, column=0, columnspan=3, pady=(8, 4)
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

    out_dir = Path(args.output_dir) if args.output_dir else DIRS["output"]
    output_matched = out_dir / (
        input_path.stem + "_Output for matched records.csv"
    )
    output_not_matched = out_dir / (input_path.stem + "_Not_Matched.csv")
    output_metafields = out_dir / (
        input_path.stem + "_Metafields for matched records.csv"
    )

    print_run_banner()

    summary = process_file(
        input_path=input_path,
        discogs_token=token,
        output_matched=output_matched,
        output_not_matched=output_not_matched,
        output_metafields=output_metafields,
        progress_callback=None,
    )

    print("Done.")
    print(
        f"Summary: total={summary['total_rows']} matched={summary['matched_count']} "
        f"unmatched={summary['unmatched_count']} final_sum={summary['total_final_price']:.2f} "
        f"ref_sum={summary['total_reference_price']:.2f} diff={summary['price_diff']:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


