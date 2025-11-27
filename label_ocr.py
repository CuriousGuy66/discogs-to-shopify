##!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
label_ocr.py

Helper module for:
- Running OCR on center label photos.
- Extracting catalog number / year / label / artist / first track from text.
- Enriching the meta dict used for Discogs search.
- Building an improved Discogs query string.
- Detecting possible label misprints by comparing OCR vs Discogs.

This module is designed to be used by:
    discogs_to_shopify_gui_v1_2_3.py
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from PIL import Image  # type: ignore
    import pytesseract  # type: ignore

    _HAS_OCR = True
except Exception as e:  # pragma: no cover - best-effort import
    logging.warning("label_ocr: OCR libraries not available (%s). OCR disabled.", e)
    Image = None  # type: ignore
    pytesseract = None  # type: ignore
    _HAS_OCR = False


# ---------------------------------------------------------------------------
# OCR core
# ---------------------------------------------------------------------------


def ocr_label_image(image_path: str) -> str:
    """
    Run OCR on a label image and return raw text.

    Returns "" on any error so that callers can gracefully ignore failures.
    """
    if not _HAS_OCR:
        return ""

    if not image_path:
        return ""

    try:
        p = Path(image_path)
        if not p.exists():
            logging.warning("label_ocr: label image does not exist: %s", p)
            return ""

        img = Image.open(p)
        # Simple pre-processing: convert to grayscale, let tesseract handle the rest
        img = img.convert("L")
        text = pytesseract.image_to_string(img)
        if not text:
            return ""
        # Normalize whitespace
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return text
    except Exception as e:
        logging.warning("label_ocr: OCR failed for %s: %s", image_path, e)
        return ""


# ---------------------------------------------------------------------------
# Text parsing helpers
# ---------------------------------------------------------------------------


CATALOG_REGEXES = [
    # Common patterns like "ABC-12345", "ST-1234", "XYZ 1234"
    r"\b[A-Z]{1,4}[- ]?\d{2,6}\b",
    # Fallback: mix of letters/digits with a dash, at least 4 chars
    r"\b[A-Z0-9]{2,5}-[A-Z0-9]{2,6}\b",
]

YEAR_REGEX = r"\b(19[0-9]{2}|20[0-4][0-9])\b"  # 1900–2049


def parse_catalog_number(text: str) -> Optional[str]:
    s = text.upper()
    for pattern in CATALOG_REGEXES:
        m = re.search(pattern, s)
        if m:
            return m.group(0).strip()
    return None


def parse_year(text: str) -> Optional[str]:
    m = re.search(YEAR_REGEX, text)
    if m:
        return m.group(1)
    return None


def parse_label_name(text: str) -> Optional[str]:
    """
    Very light heuristic: look for words around 'Records', 'Record', 'Stereo', etc.
    This is intentionally simple and conservative.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return None

    candidates = []
    for line in lines:
        low = line.lower()
        if "records" in low or "record" in low:
            candidates.append(line)
            continue
        if "stereo" in low or "mono" in low:
            candidates.append(line)
            continue

    if candidates:
        # Pick the shortest non-trivial candidate
        candidates = sorted(candidates, key=len)
        return candidates[0]

    # Fallback: use the first line as the label name
    return lines[0]


def parse_first_track_title(text: str) -> Optional[str]:
    """
    Try to guess the first track title from OCR text.

    Heuristic:
      - Look for lines that have a dash or quotes.
      - Otherwise, return the second or third line if they look "title-like".
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return None

    # Look for lines that look like "A1 – Song Title" or '“Song Title”'
    for line in lines:
        if " - " in line or " – " in line:
            parts = re.split(r"\s[–-]\s", line, maxsplit=1)
            if len(parts) == 2 and len(parts[1].strip()) > 2:
                return parts[1].strip().strip('"').strip("“”'")

    # Fallback: try a middle-short line as a title
    for idx in range(1, min(4, len(lines))):
        candidate = lines[idx]
        if 3 <= len(candidate) <= 40:
            return candidate

    return None


def parse_artist_from_text(text: str) -> Optional[str]:
    """
    Best-effort guess of artist name from OCR text.

    We just take the first line that doesn't look like catalog/label boilerplate.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return None

    for line in lines:
        if re.search(CATALOG_REGEXES[0], line.upper()):
            continue
        if "records" in line.lower():
            continue
        if "stereo" in line.lower() or "mono" in line.lower():
            continue
        if len(line) >= 3:
            return line

    return lines[0]


def extract_label_metadata(text: str) -> Dict[str, Any]:
    """
    Given raw OCR text, extract as much structured info as we can.
    """
    meta: Dict[str, Any] = {}

    if not text:
        return meta

    cat = parse_catalog_number(text)
    if cat:
        meta["Label_Catalog_Number"] = cat

    year = parse_year(text)
    if year:
        meta["Label_Year"] = year

    label_name = parse_label_name(text)
    if label_name:
        meta["Label_LabelName"] = label_name

    first_track = parse_first_track_title(text)
    if first_track:
        meta["Label_FirstTrackTitle"] = first_track

    artist = parse_artist_from_text(text)
    if artist:
        meta["Label_Artist"] = artist

    meta["Label_RawText"] = text

    return meta


# ---------------------------------------------------------------------------
# Public API used by GUI script
# ---------------------------------------------------------------------------


def enrich_meta_with_label(
    meta: Dict[str, Any],
    row: Dict[str, Any],
    label_image_column: str = "Center label photo",
) -> Dict[str, Any]:
    """
    Enrich the per-row meta dict using OCR on the center label photo.

    - Reads label image path from the spreadsheet row.
    - Runs OCR if possible.
    - Extracts catalog number, year, label, artist, first track into meta.
    """
    if not isinstance(meta, dict):
        meta = {}

    image_path = row.get(label_image_column, "") or ""
    image_path = str(image_path).strip()
    if not image_path:
        return meta

    text = ocr_label_image(image_path)
    if not text:
        return meta

    extracted = extract_label_metadata(text)
    meta.update(extracted)
    return meta


def build_discogs_query_with_label(meta: Dict[str, Any]) -> str:
    """
    Build a Discogs search query string using both spreadsheet and label data.

    Priority:
      - Use label-derived catalog number if available.
      - Include artist/title from label when confident, otherwise spreadsheet.
      - Include label name and year to narrow the search.
    """
    parts = []

    # Catalog number
    cat = meta.get("Label_Catalog_Number") or meta.get("Catalog Number")
    if cat:
        parts.append(str(cat))

    # Artist (prefer label OCR, then spreadsheet)
    artist = meta.get("Label_Artist") or meta.get("artist_display") or meta.get("Artist")
    if artist:
        parts.append(str(artist))

    # Title
    title = meta.get("title_display") or meta.get("Title")
    if title:
        parts.append(str(title))

    # Label name
    label_name = meta.get("Label_LabelName")
    if label_name:
        parts.append(str(label_name))

    # Year
    year = meta.get("Label_Year") or meta.get("Year")
    if year:
        parts.append(str(year))

    # If nothing, return empty string
    return " ".join(str(p).strip() for p in parts if str(p).strip())


def detect_label_misprint(
    meta: Dict[str, Any],
    discogs_release: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compare label OCR data vs Discogs data and flag potential misprints.

    Returns:
        {
            "Label_Misprint_Suspected": bool,
            "Label_Misprint_Reasons": str,
        }
    """
    reasons = []

    # Compare catalog number
    label_cat = (meta.get("Label_Catalog_Number") or "").upper()
    discogs_cats = []
    for lab in discogs_release.get("labels") or []:
        catno = lab.get("catno") or ""
        if catno and catno.lower() != "none":
            discogs_cats.append(catno.upper())

    if label_cat and discogs_cats and label_cat not in discogs_cats:
        reasons.append(f"Label catalog '{label_cat}' vs Discogs {discogs_cats}")

    # Compare year
    label_year = meta.get("Label_Year")
    discogs_year = discogs_release.get("year")
    if label_year and discogs_year:
        try:
            dy = int(discogs_year)
            ly = int(label_year)
            if abs(ly - dy) >= 2:  # allow small OCR off-by-one
                reasons.append(f"Label year {ly} vs Discogs {dy}")
        except Exception:
            pass

    # Compare first track title (very loose)
    label_track = (meta.get("Label_FirstTrackTitle") or "").lower().strip()
    tracks = discogs_release.get("tracklist") or []
    first_discogs_title = ""
    if tracks:
        first_discogs_title = (tracks[0].get("title") or "").lower().strip()

    if label_track and first_discogs_title:
        # If they share no 4+ character word, consider it suspicious
        words_label = {w for w in re.split(r"\W+", label_track) if len(w) >= 4}
        words_disc = {w for w in re.split(r"\W+", first_discogs_title) if len(w) >= 4}
        if words_label and words_disc and not (words_label & words_disc):
            reasons.append(
                f"Label first track '{meta.get('Label_FirstTrackTitle')}' vs Discogs '{tracks[0].get('title')}'"
            )

    # Compare artist (very coarse)
    label_artist = (meta.get("Label_Artist") or "").lower()
    discogs_artists = discogs_release.get("artists") or []
    discogs_artist_name = ""
    if discogs_artists:
        discogs_artist_name = (discogs_artists[0].get("name") or "").lower()

    if label_artist and discogs_artist_name:
        if label_artist not in discogs_artist_name and discogs_artist_name not in label_artist:
            reasons.append(
                f"Label artist '{meta.get('Label_Artist')}' vs Discogs '{discogs_artists[0].get('name')}'"
            )

    suspected = bool(reasons)
    return {
        "Label_Misprint_Suspected": suspected,
        "Label_Misprint_Reasons": "; ".join(reasons),
    }
