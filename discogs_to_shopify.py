#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discogs_to_shopify.py
===============================================================================
Standalone script to:

1. Read an input spreadsheet (CSV or XLSX) of vinyl records.
2. For each row, search Discogs for the best matching release (top match).
3. Enrich the data with Discogs metadata.
4. Build a Shopify Products CSV (single-variant records) with:
   - Artist/Title/Label/Year/Format/Genre
   - Tracklist in HTML
   - Discogs link
   - Discogs cover as first image
   - Optional Center Label Photo as second image (via extra row)
   - Your Shopify preferences (price rounding, metafields, etc.)

REQUIREMENTS:
    pip install requests pandas python-slugify

USAGE:
    python discogs_to_shopify.py input.xlsx output.csv --token YOUR_DISCOGS_TOKEN

The script will:
- Print a run timestamp at the beginning.
- Skip rows where no Discogs match is found (no fallback “spreadsheet-only” rows).
"""

import argparse
import csv
import datetime as dt
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from slugify import slugify

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DISCOGS_API_BASE = "https://api.discogs.com"
DISCOGS_USER_AGENT = "UnusualFindsDiscogsToShopify/1.0 +https://unusualfinds.com"

# Shopify static preferences (from your instructions)
SHOPIFY_PRODUCT_TYPE = "Vinyl Record"
SHOPIFY_PRODUCT_CATEGORY = "Media > Music > Vinyl Record"
SHOPIFY_OPTION1_NAME = "Title"
SHOPIFY_OPTION1_VALUE = "Default Title"
SHOPIFY_VARIANT_FULFILLMENT_SERVICE = "manual"
SHOPIFY_VARIANT_REQUIRES_SHIPPING = "TRUE"
SHOPIFY_VARIANT_TAXABLE = "TRUE"
SHOPIFY_PRODUCT_STATUS = "active"
SHOPIFY_PUBLISHED = "TRUE"

MIN_PRICE = 2.50  # USD minimum
PRICE_STEP = 0.25  # round to nearest quarter

# Column names expected in the input sheet (you can adjust these as needed)
COL_ARTIST = "Artist"
COL_TITLE = "Title"
COL_PRICE = "Reference Price"
COL_COUNTRY = "Country"
COL_CATALOG = "Catalog"
COL_CENTER_LABEL_PHOTO = "Center Label Photo"
COL_MEDIA_COND = "Media Condition"
COL_SLEEVE_COND = "Sleeve Condition"

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
    Apply your global rule:
    If the artist starts with "The " move "The" to the end, e.g. "Beatles, The".
    """
    s = artist.strip()
    if s.lower().startswith("the "):
        rest = s[4:].strip()
        return f"{rest}, The"
    return s


def simple_shop_signage(genre: str, styles: list[str] = None) -> str:
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
     11. Children's
     12. Comedy
     13. New Age
     14. Spoken Word
     15. Rock
     16. Jazz
     17. Blues
     18. Soul/Funk
     19. Classical
     20. Electronic
     21. Hip-Hop/Rap
     22. Default: raw Discogs genre
    """
    g = (genre or "").lower()
    styles = styles or []
    styles_lower = [s.lower() for s in styles]

    def styles_contains(sub: str) -> bool:
        return any(sub in s for s in styles_lower)

    # 1. Stage and Sound (also capture Soundtrack)
    if (
        "stage" in g
        or "sound" in g
        or "soundtrack" in g
        or styles_contains("stage")
        or styles_contains("sound")
        or styles_contains("soundtrack")
    ):
        return "Stage and Sound"

    # 2. Christmas / Holiday / Xmas
    if (
        "christmas" in g
        or "holiday" in g
        or "xmas" in g
        or styles_contains("christmas")
        or styles_contains("holiday")
        or styles_contains("xmas")
    ):
        return "Christmas"

    # 3. Gospel
    if "gospel" in g or styles_contains("gospel"):
        return "Gospel"

    # 4. Religious (only if not Christmas or Gospel, but we already returned above if those hit)
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

    # 11. Children's
    if (
        "children" in g
        or "kids" in g
        or styles_contains("children")
        or styles_contains("kids")
    ):
        return "Children's"

    # 12. Comedy
    if "comedy" in g or styles_contains("comedy"):
        return "Comedy"

    # 13. New Age
    if "new age" in g or styles_contains("new age"):
        return "New Age"

    # 14. Spoken Word
    if "spoken word" in g or styles_contains("spoken word"):
        return "Spoken Word"

    # 15. Rock
    if "rock" in g:
        return "Rock"

    # 16. Jazz
    if "jazz" in g:
        return "Jazz"

    # 17. Blues
    if "blues" in g:
        return "Blues"

    # 18. Soul/Funk
    if "soul" in g or "funk" in g:
        return "Soul/Funk"

    # 19. Classical
    if "classical" in g:
        return "Classical"

    # 20. Electronic
    if "electronic" in g:
        return "Electronic"

    # 21. Hip-Hop / Rap
    if "hip hop" in g or "rap" in g:
        return "Hip-Hop/Rap"

    # 22. Fallback: raw genre from Discogs
    return genre or ""



def round_price_to_quarter(price_str: str) -> float:
    """Round price to nearest quarter and enforce minimum price."""
    try:
        raw = float(price_str)
    except (TypeError, ValueError):
        raw = MIN_PRICE
    # Round to nearest 0.25
    rounded = round(raw / PRICE_STEP) * PRICE_STEP
    if rounded < MIN_PRICE:
        rounded = MIN_PRICE
    # Avoid float representation quirks like 12.499999
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
        # crude back-off
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

    # If catalog number available, that usually gives the sharpest hit.
    if catalog_number:
        params["catno"] = catalog_number

    # Basic query
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

    # Just return the first result (2A: top match)
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
    # Some search results have 'cover_image'
    cover = release_search_or_details.get("cover_image")
    if cover:
        return cover

    images = release_search_or_details.get("images") or []
    if images:
        return images[0].get("uri") or images[0].get("resource_url") or ""

    return ""


def calculate_weight_grams_from_formats(release: Dict[str, Any]) -> Optional[int]:
    """
    Estimate package weight (grams) from Discogs 'formats' quantity.

    Logic:
      - Look at release["formats"] and sum 'qty' values (default 1 if missing).
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

    # basic SEO helpers
    tags.add("Vinyl")
    tags.add("Vinyl Record")
    if genre:
        tags.add(f"{genre} Vinyl")

    return ", ".join(sorted(t for t in tags if t))


def build_format_description(release: Dict[str, Any]) -> str:
    """
    Build a simple description from Discogs 'formats' field, e.g. 'LP, Album, Stereo'.
    """
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
    # de-duplicate preserving order
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
    """Slugify for Shopify handle."""
    return slugify(base, lowercase=True)


def make_shopify_rows_for_record(
    input_row: Dict[str, Any],
    release_details: Dict[str, Any],
    release_search_obj: Dict[str, Any],
    handle_registry: Dict[str, int],
) -> List[Dict[str, Any]]:
    """
    Build one or more Shopify rows (for a single record):
      - First row: has all variant data + primary image (Discogs cover).
      - Second row (optional): only second image (Center Label Photo).
    """
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

    # Weight / grams
    grams = calculate_weight_grams_from_formats(release_details)
    pounds = grams_to_pounds(grams)

    # Full release title and SEO
    full_title = make_full_release_title(artist_display, title, label, year)
    seo_title = build_seo_title(full_title)
    seo_description = build_seo_description(artist_display, title, year, genre)

    # Price logic
    price = round_price_to_quarter(price_str)
    price_str_out = f"{price:.2f}"

    # Handle (unique)
    base_handle = slugify_handle(f"{artist_display} {title} {year}".strip())
    if base_handle not in handle_registry:
        handle_registry[base_handle] = 1
        handle = base_handle
    else:
        handle_registry[base_handle] += 1
        handle = f"{base_handle}-{handle_registry[base_handle]}"

    # Tags
    tags = build_tags(genre, styles, year, label, format_desc)

    # Body HTML: include key metadata, Discogs URL, and tracklist.
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

    body_html = "\n".join(body_lines)

    # Shopify base row
    row: Dict[str, Any] = {
        "Handle": handle,
        "Title": full_title,
        "Body (HTML)": body_html,
        "Vendor": artist_display,
        "Product Category": SHOPIFY_PRODUCT_CATEGORY,
        "Product Type": SHOPIFY_PRODUCT_TYPE,
        "Tags": tags,
        "Published": SHOPIFY_PUBLISHED,
        "Option1 Name": SHOPIFY_OPTION1_NAME,
        "Option1 Value": SHOPIFY_OPTION1_VALUE,
        "Variant SKU": discogs_release_id or "",
        "Variant Grams": grams if grams is not None else "",
        "Variant Inventory Tracker": "shopify",
        "Variant Inventory Qty": 1,
        "Variant Inventory Policy": "deny",
        "Variant Fulfillment Service": SHOPIFY_VARIANT_FULFILLMENT_SERVICE,
        "Variant Price": price_str_out,
        "Variant Compare At Price": "",
        "Variant Requires Shipping": SHOPIFY_VARIANT_REQUIRES_SHIPPING,
        "Variant Taxable": SHOPIFY_VARIANT_TAXABLE,
        "Variant Barcode": "",  # you can map Discogs barcodes if needed

        "Image Src": primary_image_url,
        "Image Position": 1,
        "Image Alt Text": full_title,

        "SEO Title": seo_title,
        "SEO Description": seo_description,
        "Status": SHOPIFY_PRODUCT_STATUS,

        # Extra business fields (Shopify ignores unknown columns)
        "Shop Signage": shop_signage,
        # Product metafields based on your mapping:
        # Album_Cover_Condition = Sleeve Condition; Album_Condition = 'Used'
        "Metafield: custom.album_cover_condition [single_line_text_field]": sleeve_cond,
        "Metafield: custom.album_condition [single_line_text_field]": "Used",
        # Optional custom variant weight in pounds (even though Shopify uses grams)
        "Variant Weight (lb)": pounds if pounds is not None else "",
    }

    rows = [row]

    # If we have a center label photo, create a second row with only image
    if center_label_photo:
        img_row = {k: "" for k in row.keys()}
        img_row["Handle"] = handle
        img_row["Image Src"] = center_label_photo
        img_row["Image Position"] = 2
        img_row["Image Alt Text"] = f"{full_title} - Center Label"
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
    processed = 0

    for idx, row in enumerate(rows, start=1):
        artist = row.get(COL_ARTIST, "") or ""
        title = row.get(COL_TITLE, "") or ""
        catalog = row.get(COL_CATALOG, "") or ""
        country = row.get(COL_COUNTRY, "") or ""

        if not (artist.strip() and title.strip()):
            logging.warning("Row %d has empty artist/title; skipping.", idx)
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
            continue

        release_id = search_obj.get("id")
        if not release_id:
            logging.warning("Search result for row %d has no release ID; skipping.", idx)
            continue

        details = discogs_get_release_details(token, release_id)
        if not details:
            logging.warning("Could not fetch details for release %s (row %d); skipping.", release_id, idx)
            continue

        shopify_rows = make_shopify_rows_for_record(row, details, search_obj, handle_registry)
        all_shopify_rows.extend(shopify_rows)

        processed += 1
        logging.info("Row %d processed successfully. Total processed: %d", idx, processed)

        if dry_run_limit is not None and processed >= dry_run_limit:
            logging.info("Dry-run limit (%d) reached; stopping.", dry_run_limit)
            break

    if not all_shopify_rows:
        logging.warning("No Shopify rows generated; nothing to write.")
        return

    # Ensure consistent column order for CSV
    fieldnames = list(all_shopify_rows[0].keys())

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_shopify_rows)

    logging.info("Wrote %d Shopify rows to %s", len(all_shopify_rows), output_path)


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
