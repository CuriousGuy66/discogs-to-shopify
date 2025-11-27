#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discogs_to_shopify_gui_v1.2.3.py

VERSION HISTORY
===============================================================================
v1.2.3 – 2025-11-27
    - Integrated label_ocr module:
        * Uses center label photo to OCR catalog number, year, label name, etc.
        * Feeds label-derived catalog number into Discogs search when available.
    - New misprint detection:
        * Compares label OCR data vs Discogs:
            - year
            - catalog number
            - first track title
            - artist
        * Adds two Shopify columns:
            - Label_Misprint_Suspected (TRUE/FALSE)
            - Label_Misprint_Reasons (semi-colon separated text).
    - Unmatched CSV "Discogs_Query" column now shows the actual query string
      built from label + spreadsheet data (via build_discogs_query_with_label).

v1.2.2 – 2025-11-24
    - Shopify CSV tweaks:
        * Removed/disabled "Collection" column from output (kept in code
          comment for potential future use).
        * Renamed inventory policy column from
            "Continue selling when out of stock"
          to
            "Out of stock inventory policy"
          and changed value from "FALSE" to "deny".
    - Added a determinate progress bar and status label in the GUI showing
      "Processing X of Y" plus a rough ETA in seconds.
    - Added simple settings persistence:
        * Remembers last input file path.
        * Remembers last dry-run limit.
      Settings are stored in a JSON file next to the script/EXE.
    - Preserved all prior logic (Discogs matching, unmatched CSV, pricing,
      signage, center-label photo, metafields, etc.).

v1.2.1 – 2025-11-24
    - GUI checks for Discogs token in the environment variable DISCOGS_TOKEN.
    - If DISCOGS_TOKEN is set, it auto-fills the token field.
    - If DISCOGS_TOKEN is not set and the user enters a token in the GUI, the
      script saves it to:
          - The current process environment, and
          - Windows user environment (via `setx DISCOGS_TOKEN <token>`) when
            running on Windows, so it is available for future runs.

v1.2.0 – 2025-11-23
    - Added a full Tkinter GUI:
        * File picker for input (CSV/XLSX).
        * Entry for Discogs token.
        * Optional dry-run limit.
        * Status messages and completion popups.
    - GUI behavior:
        * When launched with NO command-line arguments (e.g., double-clicked),
          the script opens the GUI window.
        * Output files are automatically named based on the input file:
              <input_stem>_Output for matched records.csv
              <input_stem>_Not_Matched.csv
          and saved in the SAME folder as the input.
    - CLI behavior:
        * Still supported. If arguments are provided, the script runs in
          command-line mode and uses the "output" argument as the matched CSV,
          and "<output_stem>_unmatched" for the unmatched CSV (legacy pattern).

v1.1.1 – 2025-11-23
    - Vendor field changed to use the record label (from Discogs) instead of artist.
    - Shopify product category updated to:
        "Media > Music & Sound Recordings > Records & LPs".
    - Added a new "Collection" column in the Shopify output with value:
        "Vinyl Albums" for all products.

v1.1.0 – 2025-11-23
    - Added unmatched-record tracking and export to a separate CSV:
        * Records that fail Discogs search, have no release ID, or fail
          release-detail fetch are now written to "<output>_unmatched.csv".
        * Each unmatched row includes an "Unmatched_Reason" column and a
          "Discogs_Query" column showing artist/title/catalog/country used.
    - Appended standardized HTML footer to the end of each Description in
      the Shopify CSV, explaining sleeve protection, stock photos, and
      quality/inspection process.
    - Expanded Shop Signage categories to include Pop and Disco with clear
      priority in the signage logic.
    - Bumped internal version from v1.0.0 to v1.1.0.

v1.0.0 – 2025-11-23
    - Baseline tracked version.
    - Discogs → Shopify pipeline (no GUI).
    - Uses "Reference Price" column with robust price parsing and rounding to
      nearest $0.25 (minimum $2.50).
    - Artist normalization: leading "The " moved to end (e.g., "Beatles, The").
    - Discogs search: type=release, top match auto-selected (1A + 2A).
    - Pulls label, year, genre, styles, formats, images, and tracklist HTML.
    - Estimates weight from Discogs formats (disc quantity → grams + lb).
    - Shopify CSV mapped to product_template_csv_unit_price fields.
    - First image: Discogs cover; second: center label photo.
    - Extensive Shop Signage (genre + styles) with priority logic.
    - Metafields:
        custom.album_cover_condition
        custom.album_condition = "Used"
        custom.shop_signage
"""

import argparse
import csv
import datetime as dt
import json
import logging
import sys
import time
import re
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

import pandas as pd
import requests
from slugify import slugify

# GUI imports
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from tkinter import ttk

from label_ocr import (
    enrich_meta_with_label,
    build_discogs_query_with_label,
    detect_label_misprint,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DISCOGS_API_BASE = "https://api.discogs.com"
DISCOGS_USER_AGENT = "UnusualFindsDiscogsToShopify/1.2.3 +https://unusualfinds.com"

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def print_run_banner() -> None:
    """Print a line showing when this script is running."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    print(f"[discogs_to_shopify] Run at {now}", flush=True)


def get_settings_path() -> Path:
    """Return path to settings JSON stored next to the script/EXE."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
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
        logging.warning("Could not save settings: %s", e)


def read_input(path: Path) -> List[Dict[str, Any]]:
    """Read CSV or XLSX into a list of dict rows."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path, dtype=str)
        df = df.fillna("")
    else:
        raise ValueError(f"Unsupported input format: {suffix} (use CSV or XLSX)")
    return df.to_dict(orient="records")


def normalize_artist_the(artist: str) -> str:
    """
    Apply global rule: if the artist starts with "The " move "The" to the end,
    e.g. "The Beatles" → "Beatles, The".
    """
    s = artist.strip()
    if s.lower().startswith("the "):
        rest = s[4:].strip()
        return f"{rest}, The"
    return s


def simple_shop_signage(genre: str, styles: List[str] = None) -> str:
    """
    Simplify genre + styles for shop signage.

    Priority:
      1. Stage and Sound (includes Soundtrack)
      2. Christmas / Holiday / Xmas
      3. Gospel
      4. Religious
      5. Bluegrass
      6. Country
      7. Metal
      8. Reggae
      9. Latin
     10. Folk
     11. Pop
     12. Disco
     13. Children's
     14. Comedy
     15. New Age
     16. Spoken Word
     17. Rock
     18. Jazz
     19. Blues
     20. Soul/Funk
     21. Classical
     22. Electronic
     23. Hip-Hop/Rap
     24. Default: raw Discogs genre
    """
    g = (genre or "").lower()
    styles = styles or []
    styles_lower = [s.lower() for s in styles]

    def styles_contains(sub: str) -> bool:
        return any(sub in s for s in styles_lower)

    # 1. Stage and Sound (including Soundtrack)
    if (
        "stage" in g or "sound" in g or "soundtrack" in g or
        styles_contains("stage") or styles_contains("sound") or styles_contains("soundtrack")
    ):
        return "Stage and Sound"

    # 2. Christmas
    if (
        "christmas" in g or "holiday" in g or "xmas" in g or
        styles_contains("christmas") or styles_contains("holiday") or styles_contains("xmas")
    ):
        return "Christmas"

    # 3. Gospel
    if "gospel" in g or styles_contains("gospel"):
        return "Gospel"

    # 4. Religious
    if "religious" in g or styles_contains("religious"):
        return "Religious"

    # 5. Bluegrass
    if styles_contains("bluegrass"):
        return "Bluegrass"

    # 6. Country
    if "country" in g or styles_contains("country"):
        return "Country"

    # 7. Metal
    if "metal" in g or styles_contains("metal"):
        return "Metal"

    # 8. Reggae
    if "reggae" in g or styles_contains("reggae"):
        return "Reggae"

    # 9. Latin
    if "latin" in g or styles_contains("latin"):
        return "Latin"

    # 10. Folk
    if "folk" in g or styles_contains("folk"):
        return "Folk"

    # 11. Pop
    if "pop" in g or styles_contains("pop"):
        return "Pop"

    # 12. Disco
    if "disco" in g or styles_contains("disco"):
        return "Disco"

    # 13. Children's
    if (
        "children" in g or "kids" in g or
        styles_contains("children") or styles_contains("kids")
    ):
        return "Children's"

    # 14. Comedy
    if "comedy" in g or styles_contains("comedy"):
        return "Comedy"

    # 15. New Age
    if "new age" in g or styles_contains("new age"):
        return "New Age"

    # 16. Spoken Word
    if "spoken word" in g or styles_contains("spoken word"):
        return "Spoken Word"

    # 17. Rock
    if "rock" in g:
        return "Rock"

    # 18. Jazz
    if "jazz" in g:
        return "Jazz"

    # 19. Blues
    if "blues" in g:
        return "Blues"

    # 20. Soul/Funk
    if "soul" in g or "funk" in g:
        return "Soul/Funk"

    # 21. Classical
    if "classical" in g:
        return "Classical"

    # 22. Electronic
    if "electronic" in g:
        return "Electronic"

    # 23. Hip-Hop / Rap
    if "hip hop" in g or "rap" in g:
        return "Hip-Hop/Rap"

    # 24. Default → Raw Discogs Genre
    return genre or ""


def round_price_to_quarter(price_str: str) -> float:
    """
    Round price to nearest quarter and enforce minimum price.
    Handles values like:
      "5", "5.00", "$5.00", "  5.25 USD", "5,000.00"
    """
    if not price_str:
        raw = MIN_PRICE
    else:
        s = str(price_str).strip()
        # Remove everything except digits, dot, minus
        s = re.sub(r"[^0-9.\-]", "", s)
        if s in ("", ".", "-", "-.", ".-"):
            raw = MIN_PRICE
        else:
            try:
                raw = float(s)
            except ValueError:
                raw = MIN_PRICE

    # Round to nearest 0.25
    rounded = round(raw / PRICE_STEP) * PRICE_STEP
    if rounded < MIN_PRICE:
        rounded = MIN_PRICE
    return float(f"{rounded:.2f}")


def build_discogs_headers(token: str) -> Dict[str, str]:
    return {
        "User-Agent": DISCOGS_USER_AGENT,
        "Authorization": f"Discogs token={token}",
    }


def rate_limit_sleep(response: requests.Response) -> None:
    """Respect Discogs rate limiting heuristically."""
    try:
        remaining = int(response.headers.get("X-Discogs-Ratelimit-Remaining", "1"))
        used = int(response.headers.get("X-Discogs-Ratelimit-Used", "0"))
        limit = int(response.headers.get("X-Discogs-Ratelimit", "60"))
    except ValueError:
        remaining, used, limit = 1, 0, 60

    if remaining <= 1:
        time.sleep(1.2)


def discogs_search_release(
    token: str,
    meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Search Discogs releases and return the top matching release object (from search).

    NEW BEHAVIOR:
      - Uses label-derived catalog number and year (if available) from `meta`.
      - Builds the search query via build_discogs_query_with_label(meta).
      - Still respects 1A + 2A: use Discogs search, automatically pick top result.
    """
    params: Dict[str, Any] = {
        "type": "release",
        "per_page": 5,
        "page": 1,
    }

    artist = meta.get("artist_display") or meta.get("Artist") or ""
    title = meta.get("title_display") or meta.get("Title") or ""
    country = meta.get("Country") or ""

    # Prefer catalog number from label OCR, then spreadsheet
    cat_label = meta.get("Label_Catalog_Number")
    cat_sheet = meta.get("Catalog Number") or meta.get(COL_CATALOG)
    catno = cat_label or cat_sheet
    if catno:
        params["catno"] = catno

    q = build_discogs_query_with_label(meta)
    if not q:
        q_parts = []
        if artist:
            q_parts.append(artist)
        if title:
            q_parts.append(title)
        if catno:
            q_parts.append(catno)
        q = " ".join(q_parts)
    if q:
        params["q"] = q

    if country:
        params["country"] = country

    url = f"{DISCOGS_API_BASE}/database/search"
    headers = build_discogs_headers(token)

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    rate_limit_sleep(resp)
    if not resp.ok:
        logging.warning("Discogs search failed: %s", resp.text)
        return None

    data = resp.json()
    results = data.get("results") or []
    if not results:
        return None

    return results[0]


def discogs_get_release_details(token: str, release_id: int) -> Optional[Dict[str, Any]]:
    """Fetch full release details by id."""
    url = f"{DISCOGS_API_BASE}/releases/{release_id}"
    headers = build_discogs_headers(token)

    resp = requests.get(url, headers=headers, timeout=15)
    rate_limit_sleep(resp)
    if not resp.ok:
        logging.warning("Discogs release fetch failed: %s", resp.text)
        return None
    return resp.json()


def build_tracklist_html(release: Dict[str, Any]) -> str:
    """Build an HTML tracklist from Discogs release JSON."""
    tracks = release.get("tracklist") or []
    if not tracks:
        return ""

    out_lines = ["<h3>Tracklist</h3>", "<ol>"]
    for t in tracks:
        title = t.get("title") or ""
        position = t.get("position") or ""
        duration = t.get("duration") or ""
        parts = []
        if position:
            parts.append(position)
        if title:
            parts.append(title)
        if duration:
            parts.append(f"({duration})")
        line = " ".join(parts).strip()
        if line:
            out_lines.append(f"<li>{line}</li>")
    out_lines.append("</ol>")
    return "\n".join(out_lines)


def extract_primary_image_url(release_search_or_details: Dict[str, Any]) -> str:
    """
    Get the primary image URL from Discogs object; fall back to 'cover_image'
    from search result or first image from full release details.
    """
    cover = release_search_or_details.get("cover_image")
    if cover:
        return cover

    images = release_search_or_details.get("images") or []
    if images:
        return images[0].get("uri") or images[0].get("resource_url") or ""

    return ""


def calculate_weight_grams_from_formats(release: Dict[str, Any]) -> Optional[int]:
    """
    Estimate package weight (grams) from Discogs "formats" quantity.

    Logic:
      - Look at release["formats"] and sum "qty" values (default 1 if missing).
      - If total discs == 0 → assume 1.
      - Weight:
          1 disc  -> 300 g
          2 discs -> 500 g
          3 discs -> 700 g
          4+ discs -> 300 g + 200 g for each extra disc
    """
    formats = release.get("formats") or []
    total_discs = 0

    for fmt in formats:
        qty = fmt.get("qty")
        try:
            q = int(qty)
        except (TypeError, ValueError):
            q = 1
        if q <= 0:
            q = 1
        total_discs += q

    if total_discs <= 0:
        total_discs = 1

    if total_discs == 1:
        grams = 300
    else:
        grams = 300 + 200 * (total_discs - 1)

    return grams


def grams_to_pounds(grams: Optional[int]) -> Optional[float]:
    if grams is None:
        return None
    return round(grams / 453.59237, 3)


# ---------------------------------------------------------------------------
# Shopify row construction
# ---------------------------------------------------------------------------


def make_full_release_title(artist_display: str, title: str, label: str, year: str) -> str:
    base = f"{artist_display} – {title}".strip()
    if year and label:
        return f"{base} ({year}, {label})"
    if label:
        return f"{base} ({label})"
    if year:
        return f"{base} ({year})"
    return base


def build_seo_title(full_title: str) -> str:
    return f"{full_title} | Vinyl Record | Unusual Finds"


def build_seo_description(artist: str, album: str, year: str, genre: str) -> str:
    bits = []
    if artist:
        bits.append(artist)
    if album:
        bits.append(album)
    if year:
        bits.append(str(year))
    if genre:
        bits.append(genre)
    core = " - ".join(bits)
    return f"Vintage vinyl record: {core}. Available at Unusual Finds."


def build_tags(genre: str, styles: List[str], year: str, label: str, format_desc: str) -> str:
    tags = set()

    if genre:
        tags.add(genre)
    for s in styles:
        if s:
            tags.add(s)

    if year:
        tags.add(str(year))
    if label:
        tags.add(label)
    if format_desc:
        tags.add(format_desc)

    tags.add("Vinyl")
    tags.add("Vinyl Record")
    if genre:
        tags.add(f"{genre} Vinyl")

    return ", ".join(sorted(t for t in tags if t))


def build_format_description(release: Dict[str, Any]) -> str:
    formats = release.get("formats") or []
    if not formats:
        return ""
    parts = []
    for fmt in formats:
        name = fmt.get("name")
        descr = fmt.get("descriptions") or []
        if name:
            parts.append(name)
        parts.extend(descr)
    seen = set()
    uniq = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return ", ".join(uniq)


def extract_label_and_year(release: Dict[str, Any]) -> Tuple[str, str]:
    year = str(release.get("year") or "") or ""
    labels = release.get("labels") or []
    label = ""
    if labels:
        label = labels[0].get("name") or ""
    return label, year


def extract_genre_and_styles(release: Dict[str, Any]) -> Tuple[str, List[str]]:
    genres = release.get("genres") or []
    styles = release.get("styles") or []
    genre = genres[0] if genres else ""
    styles_list = [s for s in styles if s]
    return genre, styles_list


def slugify_handle(base: str) -> str:
    return slugify(base, lowercase=True)


def ensure_discogs_token_env(token: str) -> None:
    """
    Ensure the Discogs token is available in the environment for future runs.

    - Always sets os.environ["DISCOGS_TOKEN"] for the current process.
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
            subprocess.run(
                ["setx", "DISCOGS_TOKEN", token],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logging.warning("Could not persist DISCOGS_TOKEN via setx: %s", e)


def make_shopify_rows_for_record(
    input_row: Dict[str, Any],
    release_details: Dict[str, Any],
    release_search_obj: Dict[str, Any],
    handle_registry: Dict[str, int],
    misprint_info: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    artist_raw = input_row.get(COL_ARTIST, "").strip()
    title = input_row.get(COL_TITLE, "").strip()
    price_str = input_row.get(COL_PRICE, "").strip()
    media_cond = input_row.get(COL_MEDIA_COND, "").strip()
    sleeve_cond = input_row.get(COL_SLEEVE_COND, "").strip()
    center_label_photo = input_row.get(COL_CENTER_LABEL_PHOTO, "").strip()

    artist_display = normalize_artist_the(artist_raw)

    label, year = extract_label_and_year(release_details)
    genre, styles = extract_genre_and_styles(release_details)
    format_desc = build_format_description(release_details)
    tracklist_html = build_tracklist_html(release_details)
    shop_signage = simple_shop_signage(genre, styles)

    discogs_release_id = release_details.get("id")
    discogs_url = f"https://www.discogs.com/release/{discogs_release_id}" if discogs_release_id else ""

    primary_image_url = extract_primary_image_url(release_search_obj or release_details)

    grams = calculate_weight_grams_from_formats(release_details)
    pounds = grams_to_pounds(grams)

    full_title = make_full_release_title(artist_display, title, label, year)
    seo_title = build_seo_title(full_title)
    seo_description = build_seo_description(artist_display, title, year, genre)

    price = round_price_to_quarter(price_str)
    price_str_out = f"{price:.2f}"

    base_handle = slugify_handle(f"{artist_display} {title} {year}".strip())
    if base_handle not in handle_registry:
        handle_registry[base_handle] = 1
        handle = base_handle
    else:
        handle_registry[base_handle] += 1
        handle = f"{base_handle}-{handle_registry[base_handle]}"

    tags = build_tags(genre, styles, year, label, format_desc)

    # Misprint flag info
    mis_suspected = False
    mis_reasons = ""
    if misprint_info:
        mis_suspected = bool(misprint_info.get("Label_Misprint_Suspected"))
        mis_reasons = misprint_info.get("Label_Misprint_Reasons", "")

    body_lines = []
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
        body_lines.append(f'<b>Discogs Link:</b> <a href="{discogs_url}" target="_blank">{discogs_url}</a><br>')
    if tracklist_html:
        body_lines.append("<br>")
        body_lines.append(tracklist_html)

    body_lines.append("<br>")
    body_lines.append(DESCRIPTION_FOOTER_HTML)

    body_html = "\n".join(body_lines)

    row: Dict[str, Any] = {
        # Core product fields
        "Title": full_title,
        "URL handle": handle,
        "Description": body_html,
        "Vendor": label,  # Vendor is the label
        "Product category": SHOPIFY_PRODUCT_CATEGORY,
        "Type": SHOPIFY_PRODUCT_TYPE,
        # "Collection": "Vinyl Albums",  # intentionally disabled for now
        "Tags": tags,
        "Published on online store": SHOPIFY_PUBLISHED,
        "Status": SHOPIFY_PRODUCT_STATUS,

        # Variant options (single variant)
        "Option1 name": SHOPIFY_OPTION1_NAME,
        "Option1 value": SHOPIFY_OPTION1_VALUE,
        "Option2 name": "",
        "Option2 value": "",
        "Option3 name": "",
        "Option3 value": "",

        # Pricing
        "Price": price_str_out,
        "Compare-at price": "",
        "Cost per item": "",
        "Charge tax": SHOPIFY_VARIANT_TAXABLE,
        "Tax code": "",
        "Unit price total measure": "",
        "Unit price total measure unit": "",
        "Unit price base measure": "",
        "Unit price base measure unit": "",

        # Inventory
        "SKU": discogs_release_id or "",
        "Barcode": "",
        "Inventory tracker": "shopify",
        "Inventory quantity": 1,
        "Out of stock inventory policy": "deny",

        # Weight & shipping
        "Weight value (grams)": grams if grams is not None else "",
        "Weight unit for display": "g" if grams is not None else "",
        "Requires shipping": SHOPIFY_VARIANT_REQUIRES_SHIPPING,
        "Fulfillment service": SHOPIFY_VARIANT_FULFILLMENT_SERVICE,

        # Images
        "Product image URL": primary_image_url,
        "Image position": 1,
        "Image alt text": full_title,
        "Variant image URL": "",
        "Gift card": "FALSE",

        # SEO
        "SEO title": seo_title,
        "SEO description": seo_description,

        # Metafields and extra helper columns
        "Metafield: custom.album_cover_condition [single_line_text_field]": sleeve_cond,
        "Metafield: custom.album_condition [single_line_text_field]": "Used",
        "Metafield: custom.shop_signage [single_line_text_field]": shop_signage,
        "Variant Weight (lb)": pounds if pounds is not None else "",

        # NEW: Misprint diagnostics (Shopify will ignore unknown columns)
        "Label_Misprint_Suspected": "TRUE" if mis_suspected else "FALSE",
        "Label_Misprint_Reasons": mis_reasons,
    }

    rows: List[Dict[str, Any]] = [row]

    # If we have a center label photo, create a second row with only image
    if center_label_photo:
        img_row = {k: "" for k in row.keys()}
        img_row["Title"] = full_title
        img_row["URL handle"] = handle
        img_row["Product image URL"] = center_label_photo
        img_row["Image position"] = 2
        img_row["Image alt text"] = f"{full_title} - Center Label"
        rows.append(img_row)

    return rows


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------


def process_file(
    input_path: Path,
    matched_output_path: Path,
    unmatched_output_path: Path,
    token: str,
    dry_run_limit: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    """
    Core processing function.

    matched_output_path: where to write the Shopify CSV (matched records).
    unmatched_output_path: where to write the unmatched rows CSV.
    dry_run_limit: if set, only the first N input rows are inspected.
    progress_callback: optional callable(current_index, total_rows).

    NEW:
      - Builds a 'meta' dict per row.
      - Enriches meta with label OCR via enrich_meta_with_label.
      - Uses build_discogs_query_with_label(meta) as the Discogs query string.
      - Calls detect_label_misprint(meta, release_details) and passes the
        result into make_shopify_rows_for_record so we can flag suspected
        misprints in the Shopify CSV.
    """
    rows = read_input(input_path)
    total_rows = len(rows)
    if dry_run_limit is not None:
        total_rows = min(total_rows, dry_run_limit)

    logging.info("Loaded %d rows from %s (processing up to %d)", len(rows), input_path, total_rows)

    all_shopify_rows: List[Dict[str, Any]] = []
    handle_registry: Dict[str, int] = {}
    unmatched_rows: List[Dict[str, Any]] = []

    for idx, row in enumerate(rows, start=1):
        if dry_run_limit is not None and idx > dry_run_limit:
            break

        artist = row.get(COL_ARTIST, "") or ""
        title = row.get(COL_TITLE, "") or ""
        catalog = row.get(COL_CATALOG, "") or ""
        country = row.get(COL_COUNTRY, "") or ""

        # Build a meta dict and enrich it with label OCR BEFORE Discogs search
        meta: Dict[str, Any] = {
            "Artist": artist,
            "Title": title,
            "Catalog Number": catalog,
            "Country": country,
        }
        meta = enrich_meta_with_label(meta, row, label_image_column=COL_CENTER_LABEL_PHOTO)

        # Build a human-readable query description for unmatched CSV
        query_str = build_discogs_query_with_label(meta)
        cat_used = meta.get("Label_Catalog_Number") or catalog
        discogs_query = (
            f"query={query_str} | catalog={cat_used} | country={country}"
        )

        def record_unmatched(reason: str) -> None:
            um = dict(row)
            um["Unmatched_Reason"] = reason
            um["Discogs_Query"] = discogs_query
            unmatched_rows.append(um)

        if not (artist.strip() and title.strip()):
            logging.warning("Row %d has empty artist/title; skipping.", idx)
            record_unmatched("Missing artist and/or title")
            if progress_callback:
                progress_callback(idx, total_rows)
            continue

        logging.info("Row %d: searching Discogs for %s - %s", idx, artist, title)

        search_obj = discogs_search_release(
            token=token,
            meta=meta,
        )

        if not search_obj:
            logging.warning("No Discogs match for row %d (%s - %s); skipping.", idx, artist, title)
            record_unmatched("No Discogs search result")
            if progress_callback:
                progress_callback(idx, total_rows)
            continue

        release_id = search_obj.get("id")
        if not release_id:
            logging.warning("Search result for row %d has no release ID; skipping.", idx)
            record_unmatched("Discogs search result missing release ID")
            if progress_callback:
                progress_callback(idx, total_rows)
            continue

        details = discogs_get_release_details(token, release_id)
        if not details:
            logging.warning("Could not fetch details for release %s (row %d); skipping.", release_id, idx)
            record_unmatched(f"Failed to fetch Discogs release details for ID {release_id}")
            if progress_callback:
                progress_callback(idx, total_rows)
            continue

        # Detect misprint by comparing label OCR vs Discogs
        misprint_info = detect_label_misprint(meta, details)

        shopify_rows = make_shopify_rows_for_record(
            input_row=row,
            release_details=details,
            release_search_obj=search_obj,
            handle_registry=handle_registry,
            misprint_info=misprint_info,
        )
        all_shopify_rows.extend(shopify_rows)

        logging.info("Row %d processed successfully.", idx)

        if progress_callback:
            progress_callback(idx, total_rows)

    # Write matched Shopify rows
    if not all_shopify_rows:
        logging.warning("No Shopify rows generated; nothing to write for matched records.")
    else:
        fieldnames = list(all_shopify_rows[0].keys())
        with matched_output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_shopify_rows)
        logging.info("Wrote %d Shopify rows to %s", len(all_shopify_rows), matched_output_path)

    # Write unmatched rows to a separate CSV
    if unmatched_rows:
        unmatched_fieldnames = sorted({k for r in unmatched_rows for k in r.keys()})
        with unmatched_output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=unmatched_fieldnames)
            writer.writeheader()
            writer.writerows(unmatched_rows)
        logging.info(
            "Wrote %d unmatched input rows to %s",
            len(unmatched_rows),
            unmatched_output_path,
        )
    else:
        logging.info("No unmatched rows; no unmatched CSV written.")


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert Discogs metadata to Shopify Products CSV.\n\n"
            "If you run this script with NO arguments (for example, by double-"
            "clicking the EXE), a graphical interface will open instead."
        )
    )
    parser.add_argument("input", type=str, help="Input file (CSV or XLSX)")
    parser.add_argument("output", type=str, help="Output Shopify CSV (matched records)")
    parser.add_argument(
        "--token",
        type=str,
        required=True,
        help="Discogs personal access token",
    )
    parser.add_argument(
        "--dry-limit",
        type=int,
        default=None,
        help="Optional limit on number of input rows to process (for testing)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


class DiscogsToShopifyGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Discogs to Shopify - Vinyl Import")
        self.root.geometry("750x500")

        self.settings: Dict[str, Any] = load_settings()
        self.start_time: Optional[float] = None

        # Input file
        self.input_label = tk.Label(root, text="Input file (CSV/XLSX):")
        self.input_label.grid(row=0, column=0, sticky="e", padx=5, pady=5)
        self.input_entry = tk.Entry(root, width=60)
        self.input_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        self.browse_button = tk.Button(root, text="Browse...", command=self.browse_input)
        self.browse_button.grid(row=0, column=2, padx=5, pady=5)

        # Discogs token
        self.token_label = tk.Label(root, text="Discogs token:")
        self.token_label.grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.token_entry = tk.Entry(root, width=60, show="*")
        self.token_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")

        # Prefill token from environment if present
        env_token = os.getenv("DISCOGS_TOKEN")
        if env_token:
            self.token_entry.insert(0, env_token)

        # Dry-run limit
        self.dry_label = tk.Label(root, text="Dry-run limit (optional):")
        self.dry_label.grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.dry_entry = tk.Entry(root, width=20)
        self.dry_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")

        # Restore settings if available
        last_input = self.settings.get("last_input")
        if last_input:
            self.input_entry.insert(0, last_input)
        last_dry = self.settings.get("dry_limit")
        if last_dry is not None:
            self.dry_entry.insert(0, str(last_dry))

        # Run button
        self.run_button = tk.Button(root, text="Run", command=self.run_process)
        self.run_button.grid(row=3, column=1, padx=5, pady=10, sticky="w")

        # Progress bar + label
        self.progress_label = tk.Label(root, text="")
        self.progress_label.grid(row=4, column=0, columnspan=3, sticky="w", padx=5, pady=(0, 2))

        self.progress = ttk.Progressbar(root, orient="horizontal", mode="determinate")
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew", padx=5, pady=(0, 5))

        # Status / log area
        self.log_text = scrolledtext.ScrolledText(root, width=80, height=15, state="disabled")
        self.log_text.grid(row=6, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")

        # Make rows/cols expand
        root.grid_rowconfigure(6, weight=1)
        root.grid_columnconfigure(1, weight=1)

    def log(self, message: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")
        self.root.update_idletasks()

    def browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select input file",
            filetypes=[
                ("CSV files", "*.csv"),
                ("Excel files", "*.xlsx;*.xls"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.input_entry.delete(0, "end")
            self.input_entry.insert(0, path)

    def update_progress(self, current: int, total: int) -> None:
        if total <= 0:
            return
        if self.start_time is None:
            self.start_time = time.time()
        self.progress["maximum"] = total
        self.progress["value"] = current
        elapsed = time.time() - self.start_time
        eta_text = ""
        if current > 0 and elapsed > 0:
            per = elapsed / current
            remaining = per * (total - current)
            eta_text = f"  |  ETA ~ {int(remaining)}s"
        self.progress_label.config(text=f"Processing {current} of {total}{eta_text}")
        self.root.update_idletasks()

    def progress_callback(self, current: int, total: int) -> None:
        self.update_progress(current, total)

    def run_process(self) -> None:
        input_path_str = self.input_entry.get().strip()
        token = self.token_entry.get().strip()
        dry_limit_str = self.dry_entry.get().strip()

        if not input_path_str:
            messagebox.showerror("Error", "Please select an input file.")
            return

        input_path = Path(input_path_str)
        if not input_path.exists():
            messagebox.showerror("Error", f"Input file does not exist:\n{input_path}")
            return

        # If the token field is empty, try to read from DISCOGS_TOKEN environment variable
        if not token:
            token = os.getenv("DISCOGS_TOKEN", "").strip()

        if not token:
            messagebox.showerror(
                "Error",
                "No Discogs token found.\n\n"
                "Please enter your Discogs personal access token.",
            )
            return

        # Ensure the token is stored in the environment (and persisted on Windows)
        ensure_discogs_token_env(token)

        dry_limit: Optional[int] = None
        if dry_limit_str:
            try:
                dry_limit = int(dry_limit_str)
            except ValueError:
                messagebox.showerror("Error", "Dry-run limit must be an integer.")
                return

        # Compute output names based on input file
        out_dir = input_path.parent
        stem = input_path.stem

        matched_output = out_dir / f"{stem}_Output for matched records.csv"
        unmatched_output = out_dir / f"{stem}_Not_Matched.csv"

        # Save settings for next run
        self.settings["last_input"] = str(input_path)
        self.settings["dry_limit"] = dry_limit
        save_settings(self.settings)

        self.log(f"Input file: {input_path}")
        self.log(f"Matched output: {matched_output}")
        self.log(f"Not-matched output: {unmatched_output}")
        self.log("Starting processing... This may take a while, please wait.\n")

        self.start_time = time.time()
        self.progress["value"] = 0
        self.progress_label.config(text="")

        try:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(message)s",
            )
            print_run_banner()
            process_file(
                input_path=input_path,
                matched_output_path=matched_output,
                unmatched_output_path=unmatched_output,
                token=token,
                dry_run_limit=dry_limit,
                progress_callback=self.progress_callback,
            )
        except Exception as e:
            self.log(f"Error: {e}")
            messagebox.showerror("Error", f"Processing failed:\n{e}")
            return

        self.log("\nDone.")
        self.progress_label.config(text="Completed.")
        messagebox.showinfo(
            "Completed",
            f"Processing complete.\n\nMatched records:\n{matched_output}\n\n"
            f"Not-matched records:\n{unmatched_output}",
        )


def run_gui() -> None:
    root = tk.Tk()
    app = DiscogsToShopifyGUI(root)
    root.mainloop()


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> None:
    # If no arguments passed → launch GUI
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        run_gui()
        return

    # CLI mode
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    input_path = Path(args.input)
    if not input_path.exists():
        logging.error("Input file does not exist: %s", input_path)
        sys.exit(1)

    matched_output_path = Path(args.output)
    unmatched_output_path = matched_output_path.with_name(
        matched_output_path.stem + "_unmatched" + matched_output_path.suffix
    )

    try:
        print_run_banner()
        process_file(
            input_path=input_path,
            matched_output_path=matched_output_path,
            unmatched_output_path=unmatched_output_path,
            token=args.token,
            dry_run_limit=args.dry_limit,
            progress_callback=None,
        )
    except Exception as e:
        logging.exception("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
