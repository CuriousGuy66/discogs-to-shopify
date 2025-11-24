#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discogs_to_shopify_v1.1.1.py

VERSION HISTORY
===============================================================================
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
import logging
import sys
import time
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from slugify import slugify

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DISCOGS_API_BASE = "https://api.discogs.com"
DISCOGS_USER_AGENT = "UnusualFindsDiscogsToShopify/1.1.1 +https://unusualfinds.com"

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
    token: str, artist: str, title: str, catalog_number: str = "", country: str = ""
) -> Optional[Dict[str, Any]]:
    """
    Search Discogs releases and return the top matching release object (from search).
    Preference 1A + 2A: use Discogs search, automatically pick top result.
    """
    params: Dict[str, Any] = {
        "type": "release",
        "per_page": 5,
        "page": 1,
    }

    if catalog_number:
        params["catno"] = catalog_number

    q_parts = []
    if artist:
        q_parts.append(artist)
    if title:
        q_parts.append(title)
    if q_parts:
        params["q"] = " - ".join(q_parts)

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


def make_shopify_rows_for_record(
    input_row: Dict[str, Any],
    release_details: Dict[str, Any],
    release_search_obj: Dict[str, Any],
    handle_registry: Dict[str, int],
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
        "Vendor": label,  # Vendor is now the label
        "Product category": SHOPIFY_PRODUCT_CATEGORY,
        "Type": SHOPIFY_PRODUCT_TYPE,
        "Collection": "Vinyl Albums",  # New collection column
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
        "Continue selling when out of stock": "FALSE",

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
    input_path: Path, output_path: Path, token: str, dry_run_limit: Optional[int] = None
) -> None:
    rows = read_input(input_path)
    logging.info("Loaded %d rows from %s", len(rows), input_path)

    all_shopify_rows: List[Dict[str, Any]] = []
    handle_registry: Dict[str, int] = {}
    unmatched_rows: List[Dict[str, Any]] = []
    processed = 0

    for idx, row in enumerate(rows, start=1):
        artist = row.get(COL_ARTIST, "") or ""
        title = row.get(COL_TITLE, "") or ""
        catalog = row.get(COL_CATALOG, "") or ""
        country = row.get(COL_COUNTRY, "") or ""

        discogs_query = (
            f"artist={artist} | title={title} | catalog={catalog} | country={country}"
        )

        def record_unmatched(reason: str) -> None:
            um = dict(row)
            um["Unmatched_Reason"] = reason
            um["Discogs_Query"] = discogs_query
            unmatched_rows.append(um)

        if not (artist.strip() and title.strip()):
            logging.warning("Row %d has empty artist/title; skipping.", idx)
            record_unmatched("Missing artist and/or title")
            continue

        logging.info("Row %d: searching Discogs for %s - %s", idx, artist, title)

        search_obj = discogs_search_release(
            token=token,
            artist=artist,
            title=title,
            catalog_number=catalog,
            country=country,
        )

        if not search_obj:
            logging.warning("No Discogs match for row %d (%s - %s); skipping.", idx, artist, title)
            record_unmatched("No Discogs search result")
            continue

        release_id = search_obj.get("id")
        if not release_id:
            logging.warning("Search result for row %d has no release ID; skipping.", idx)
            record_unmatched("Discogs search result missing release ID")
            continue

        details = discogs_get_release_details(token, release_id)
        if not details:
            logging.warning("Could not fetch details for release %s (row %d); skipping.", release_id, idx)
            record_unmatched(f"Failed to fetch Discogs release details for ID {release_id}")
            continue

        shopify_rows = make_shopify_rows_for_record(row, details, search_obj, handle_registry)
        all_shopify_rows.extend(shopify_rows)

        processed += 1
        logging.info("Row %d processed successfully. Total processed: %d", idx, processed)

        if dry_run_limit is not None and processed >= dry_run_limit:
            logging.info("Dry-run limit (%d) reached; stopping.", dry_run_limit)
            break

    # Write matched Shopify rows
    if not all_shopify_rows:
        logging.warning("No Shopify rows generated; nothing to write for matched records.")
    else:
        fieldnames = list(all_shopify_rows[0].keys())
        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_shopify_rows)
        logging.info("Wrote %d Shopify rows to %s", len(all_shopify_rows), output_path)

    # Write unmatched rows to a separate CSV
    if unmatched_rows:
        unmatched_path = output_path.with_name(
            output_path.stem + "_unmatched" + output_path.suffix
        )
        unmatched_fieldnames = sorted({k for r in unmatched_rows for k in r.keys()})
        with unmatched_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=unmatched_fieldnames)
            writer.writeheader()
            writer.writerows(unmatched_rows)
        logging.info(
            "Wrote %d unmatched input rows to %s",
            len(unmatched_rows),
            unmatched_path,
        )
    else:
        logging.info("No unmatched rows; no unmatched CSV written.")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Discogs metadata to Shopify Products CSV.")
    parser.add_argument("input", type=str, help="Input file (CSV or XLSX)")
    parser.add_argument("output", type=str, help="Output Shopify CSV")
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
        help="Optional limit on number of records to process (for testing)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    print_run_banner()

    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        logging.error("Input file does not exist: %s", input_path)
        sys.exit(1)

    try:
        process_file(
            input_path=input_path,
            output_path=output_path,
            token=args.token,
            dry_run_limit=args.dry_limit,
        )
    except Exception as e:
        logging.exception("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
