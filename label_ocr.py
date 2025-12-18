#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
label_ocr.py
===============================================================================
Center-label OCR helper for Unusual Finds vinyl workflow.

- Handles local paths and URLs (with a cache in ~/.discogs_to_shopify/ocr_cache/)
- Runs OCR tuned for vinyl center labels (crop, upscale, threshold)
- Extracts:
    Ocr_Catalog
    Ocr_Matrix
    Ocr_Label
    Ocr_Year
    Ocr_StereoMono
    Ocr_Format_Flags
    Ocr_Tracks
    Ocr_Notes
    Ocr_Scan_Confidence

Public API:

    enrich_meta_with_label(meta, row, label_image_column="Center label photo")
    build_discogs_query_with_label(meta)
    detect_label_misprint(meta, discogs_details)

Includes a __main__ test harness so you can run:

    python label_ocr.py

and inspect one image end-to-end.
===============================================================================
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional



# ---------------------------------------------------------------------------
# Optional libraries (Pillow + pytesseract)
# ---------------------------------------------------------------------------

try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    Image = None
    pytesseract = None
    OCR_AVAILABLE = False
    logging.warning("PIL or pytesseract not available; label OCR will be disabled.")

# requests is optional; needed only for URL-based images
try:
    import requests
    URL_DOWNLOAD_ENABLED = True
except Exception:
    requests = None
    URL_DOWNLOAD_ENABLED = False
    logging.warning("requests not available; URL-based label OCR will be disabled.")

logging.warning(
    "label_ocr.py loaded: OCR_AVAILABLE=%s, URL_DOWNLOAD_ENABLED=%s",
    OCR_AVAILABLE,
    URL_DOWNLOAD_ENABLED,
)
# Manual runtime switch so we can turn label OCR on/off easily
LABEL_OCR_ENABLED = False  # default OFF; flip to True to enable OCR processing

# ---------------------------------------------------------------------------
# Patterns / constants
# ---------------------------------------------------------------------------

YEAR_PATTERN = re.compile(
    r"\b(19[5-9]\d|20[0-4]\d)\b"  # 1950–2049
)

MATRIX_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9\-\.\/\+]{3,}[A-Z0-9]")  # loose alnum/dash run

COMMON_NON_LABEL_WORDS = {
    "STEREO",
    "MONO",
    "SIDE",
    "A",
    "B",
    "C",
    "D",
    "ST",
    "LP",
    "RPM",
    "RECORDS",
    "RECORD",
    "INC",
    "LIMITED",
    "LTD",
    "CORP",
    "COMPANY",
    "CO",
    "TRADEMARK",
    "TRADE",
    "MARK",
}


def _clean_ocr_text(value: str, allow_newlines: bool = False) -> str:
    """
    Normalize OCR text:
    - Unicode NFKD -> ASCII
    - Strip control chars
    - Whitelist basic punctuation (.,:;'\"&()/\\-)
    - Collapse whitespace and trim common junk
    """
    if value is None:
        return ""

    def _clean_line(line: str) -> str:
        text = unicodedata.normalize("NFKD", str(line or ""))
        text = text.encode("ascii", "ignore").decode("ascii")
        text = re.sub(r"[^A-Za-z0-9\\s\\.,:;'\"&()\\/\\-]+", " ", text)
        text = re.sub(r"\\s+", " ", text).strip(" -/.,;:'\"")
        return text

    if allow_newlines:
        parts = []
        for ln in str(value).splitlines():
            cleaned = _clean_line(ln)
            if cleaned:
                parts.append(cleaned)
        return "\\n".join(parts)

    return _clean_line(str(value))

# ---------------------------------------------------------------------------
# File / URL helpers
# ---------------------------------------------------------------------------

def _get_cache_dir() -> Path:
    """
    Cache directory for downloaded label images, e.g.:

        ~/.discogs_to_shopify/ocr_cache/
    """
    home = Path.home()
    base = home / ".discogs_to_shopify" / "ocr_cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _is_url(path: str) -> bool:
    path = (path or "").strip().lower()
    return path.startswith("http://") or path.startswith("https://")


def _download_image_to_cache(url: str) -> Optional[str]:
    """
    Download the image at URL into the cache directory and return the local path.
    If anything fails, returns None.
    """
    if not URL_DOWNLOAD_ENABLED:
        logging.warning("requests not available; cannot download URL for OCR: %s", url)
        return None

    url_str = (url or "").strip()
    if not url_str:
        return None

    cache_dir = _get_cache_dir()

    # Use a stable filename based on a hash of the URL, preserve extension if possible.
    url_hash = hashlib.sha256(url_str.encode("utf-8")).hexdigest()[:16]
    ext = os.path.splitext(url_str.split("?", 1)[0])[1]  # try to keep .jpg/.png/.webp
    if not ext:
        ext = ".img"

    dest = cache_dir / f"{url_hash}{ext}"

    # If already cached, reuse it
    if dest.exists():
        logging.info("Label OCR: using cached image %s for URL %s", dest, url_str)
        return str(dest)

    logging.info("Label OCR: downloading label image for OCR: %s -> %s", url_str, dest)

    try:
        resp = requests.get(url_str, stream=True, timeout=30)
    except Exception as e:
        logging.warning("Failed to download label image %s: %s", url_str, e)
        return None

    if not resp.ok:
        logging.warning(
            "Failed to download label image %s: HTTP %s",
            url_str,
            resp.status_code,
        )
        return None

    try:
        with dest.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                f.write(chunk)
    except Exception as e:
        logging.warning("Failed to write cached label image %s: %s", dest, e)
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    return str(dest)


def _resolve_image_path(image_path: str) -> Optional[str]:
    """
    Given a value from the spreadsheet, return a local file path ready for PIL:

        - If it's a URL, download to cache and return the cached path.
        - If it's a local path and exists, return it.
        - Otherwise, return None.
    """
    if not image_path:
        return None

    path_str = str(image_path).strip()
    if not path_str:
        return None

    if _is_url(path_str):
        resolved = _download_image_to_cache(path_str)
        logging.info("Label OCR: URL path=%r resolved to cached=%r", path_str, resolved)
        return resolved

    # Treat as local path
    if os.path.exists(path_str):
        logging.info("Label OCR: local image path exists: %s", path_str)
        return path_str

    logging.warning("Center label image path does not exist: %s", path_str)
    return None


# ---------------------------------------------------------------------------
# Core OCR helpers
# ---------------------------------------------------------------------------

def _run_ocr(image_path: str) -> List[str]:
    if not LABEL_OCR_ENABLED:
        logging.info("Label OCR disabled; skipping OCR for %s", image_path)
        return []

    """
    Run OCR on the center label image and return a list of text lines.

    Uses:
      - central 60% crop (square)
      - grayscale
      - upscale 2x
      - autocontrast
      - hard threshold (good for printed text on labels)
    """
    print("DEBUG: _run_ocr() called with:", repr(image_path))

    if not OCR_AVAILABLE:
        print("DEBUG: OCR not available (PIL or pytesseract missing).")
        return []

    resolved = _resolve_image_path(image_path)
    print("DEBUG: resolved path:", repr(resolved))
    if not resolved:
        return []

    try:
        im = Image.open(resolved)
    except Exception as e:
        print("DEBUG: failed to open image:", e)
        return []

    print("DEBUG: opened image size:", im.size, "mode:", im.mode)

    try:
        from PIL import ImageOps
    except Exception:
        ImageOps = None

    # 1) Crop central 60% square
    w, h = im.size
    side = int(min(w, h) * 0.60)
    left = (w - side) // 2
    upper = (h - side) // 2
    im = im.crop((left, upper, left + side, upper + side))
    print("DEBUG: cropped central 60%, new size:", im.size)

    # 2) Grayscale
    im = im.convert("L")

    # 3) Upscale 2x
    scale = 2
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    print("DEBUG: resized for OCR, new size:", im.size)

    # 4) Autocontrast
    if ImageOps:
        im = ImageOps.autocontrast(im)
        print("DEBUG: applied autocontrast")

    # 5) Hard threshold (gives that strong black/white effect)
    im = im.point(lambda x: 0 if x < 140 else 255)
    print("DEBUG: applied hard threshold (140)")

    # Save debug image
    debug_path = _get_cache_dir() / "debug_preprocessed_label.png"
    im.save(debug_path)
    print("DEBUG: saved preprocessed image to:", debug_path)

    # 6) OCR
    print("DEBUG: running pytesseract...")
    try:
        text = pytesseract.image_to_string(im, config="--psm 6")
    except Exception as e:
        print("DEBUG: pytesseract failed:", e)
        return []

    print("DEBUG: raw text length:", len(text or ""))
    if text:
        print("DEBUG: raw text preview:", repr(text[:400]))

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    print("DEBUG: extracted", len(lines), "non-empty lines")
    return lines


# ---------------------------------------------------------------------------
# Catalog extraction (global, tolerant)
# ---------------------------------------------------------------------------

def _extract_catalog_from_text(lines: List[str]) -> Optional[str]:
    """
    Scan the entire OCR text for something that looks like a catalog number.

    We look for:
        2–5 letters
        up to 5 junk chars (spaces, dashes, etc.)
        3–6 digits

    Then normalize to: AAA-1234
    """
    all_text = " ".join(lines).upper()
    # Very loose pattern: letters, a bit of junk, then digits
    m = re.search(r"[A-Z]{2,5}[^A-Z0-9]{0,5}\d{3,6}", all_text)
    if not m:
        return None

    raw = m.group(0)
    # Example raw: "SLL.g3¢¢", "SLL- 838%"
    cleaned = re.sub(r"[^A-Z0-9]", "", raw)  # -> e.g. "SLL83", "SLL8386"
    # Expect at least 2 letters followed by 3+ digits
    m2 = re.match(r"([A-Z]{2,5})(\d{3,6})", cleaned)
    if not m2:
        return None

    letters, digits = m2.group(1), m2.group(2)
    return f"{letters}-{digits}"


def _extract_matrix_from_text(lines: List[str]) -> Optional[str]:
    """
    Very loose heuristic to grab a matrix/runout-like token from label OCR text.

    Looks for alphanumeric/dash strings (5–14 chars) that contain both letters
    and digits and are not obviously words. Prefers tokens with side markers
    like A/B/1/2.
    """
    candidates: List[str] = []
    side_bias = ("A", "B", "1", "2")

    for line in lines:
        for m in MATRIX_TOKEN.finditer(line.upper()):
            token = m.group(0)
            if len(token) < 5 or len(token) > 14:
                continue
            if not (re.search(r"[A-Z]", token) and re.search(r"\d", token)):
                continue
            # skip if looks like a plain catalog already caught
            if re.match(r"^[A-Z]{2,5}\d{3,6}$", token.replace("-", "")):
                continue
            candidates.append(token)

    if not candidates:
        return None

    # Prefer tokens with side hints
    def score(tok: str) -> int:
        s = 0
        if any(ch in tok for ch in side_bias):
            s += 1
        if "-" in tok:
            s += 1
        return s

    candidates.sort(key=lambda t: (score(t), len(t)), reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# Extract structured fields
# ---------------------------------------------------------------------------

def _extract_from_lines(lines: List[str]) -> Dict[str, Any]:
    """Given OCR text lines, extract structured fields."""
    # First, try to grab a catalog number from the whole text
    ocr_catalog: Optional[str] = _extract_catalog_from_text(lines)
    ocr_matrix: Optional[str] = _extract_matrix_from_text(lines)

    ocr_label: Optional[str] = None
    ocr_year: Optional[str] = None
    stereo_mono: Optional[str] = None
    format_flags: List[str] = []
    tracks: List[str] = []
    notes: List[str] = []
    label_tokens: List[str] = []

    for line in lines:
        upper = line.upper()

        # Year candidate
        if not ocr_year:
            m = YEAR_PATTERN.search(line)
            if m:
                ocr_year = m.group(1)

        # Stereo / Mono
        if "STEREO" in upper:
            stereo_mono = "STEREO"
        elif "MONO" in upper and not stereo_mono:
            stereo_mono = "MONO"

        # Format-ish flags
        if any(flag in upper for flag in ("33", "33⅓", "45 RPM", "33 RPM", "33 1/3")):
            format_flags.append("33⅓ RPM")
        if "LP" in upper:
            format_flags.append("LP")

        # Tokenize for label guess
        tokens = re.split(r"\s+|[,;/]", line)
        clean_tokens = [t.strip() for t in tokens if t.strip()]

        for t in clean_tokens:
            tup = t.upper()
            if len(tup) >= 3 and tup.isalpha() and tup not in COMMON_NON_LABEL_WORDS:
                label_tokens.append(tup)

        # Track-like heuristic: A1 Track, 1. Track, etc.
        if re.match(r"^[ABCD]?\d+[\.\)]?\s+", line, re.IGNORECASE):
            tracks.append(line.strip())
        else:
            if re.search(r"[A-Za-z]", line) and not any(
                kw in upper
                for kw in ("MANUFACTURED", "DISTRIBUTED", "UNAUTHORIZED", "COPYING")
            ):
                notes.append(line.strip())

    # Label guess from uppercase tokens
    if label_tokens:
        unique: List[str] = []
        for t in label_tokens:
            if t not in unique:
                unique.append(t)
        ocr_label = " ".join(unique[:3])

    # Confidence score
    score = 0.0
    if ocr_catalog:
        score += 0.4
    if ocr_label:
        score += 0.2
    if ocr_year:
        score += 0.1
    if stereo_mono:
        score += 0.1
    if tracks:
        score += 0.2
    if score > 1.0:
        score = 1.0

    raw_fields = {
        "Ocr_Catalog": ocr_catalog or "",
        "Ocr_Matrix": ocr_matrix or "",
        "Ocr_Label": ocr_label or "",
        "Ocr_Year": ocr_year or "",
        "Ocr_StereoMono": stereo_mono or "",
        "Ocr_Format_Flags": ", ".join(sorted(set(format_flags))) if format_flags else "",
        "Ocr_Tracks": "\n".join(tracks) if tracks else "",
        "Ocr_Notes": "\n".join(notes) if notes else "",
        "Ocr_Scan_Confidence": round(score, 3),
    }

    cleaned_fields: Dict[str, Any] = {}
    for key, val in raw_fields.items():
        if key in ("Ocr_Tracks", "Ocr_Notes"):
            cleaned_fields[key] = _clean_ocr_text(val, allow_newlines=True)
        else:
            cleaned_fields[key] = _clean_ocr_text(val)

    # Keep confidence as numeric
    cleaned_fields["Ocr_Scan_Confidence"] = raw_fields["Ocr_Scan_Confidence"]

    # Preserve raw alongside cleaned for debugging
    raw_with_suffix = {f"{k}_Raw": v for k, v in raw_fields.items()}

    combined = {}
    combined.update(raw_with_suffix)
    combined.update(cleaned_fields)

    return combined


# ---------------------------------------------------------------------------
# Public API used by discogs_to_shopify_gui.py
# ---------------------------------------------------------------------------

def enrich_meta_with_label(
    meta: Dict[str, Any],
    row: Mapping[str, Any],
    label_image_column: str = "Center label photo",
) -> Dict[str, Any]:
    """
    Enrich the 'meta' dict with OCR-derived fields.

    meta: dict with keys like Artist, Title, Catalog Number, Label, Country, Year.
    row:  full input row (used to locate the label image path).
    """
    if not LABEL_OCR_ENABLED:
        logging.info("Label OCR disabled via LABEL_OCR_ENABLED; skipping.")
        return meta

    image_path = str(row.get(label_image_column, "")).strip()
    logging.info("Label OCR: row image_path=%r", image_path)

    if not image_path:
        return meta

    lines = _run_ocr(image_path)
    if not lines:
        # OCR was attempted but no text was detected
        enriched = dict(meta)
        enriched.setdefault("Ocr_Scan_Confidence", 0.0)

        logging.info(
            "Label OCR: no text detected (image=%s)",
            image_path,
        )
        return enriched

    ocr_fields = _extract_from_lines(lines)

    enriched = dict(meta)
    enriched.update(ocr_fields)

    # For compatibility with Discogs search, provide a generic label-derived cat#
    if ocr_fields.get("Ocr_Catalog"):
        enriched["Label_Catalog_Number"] = ocr_fields["Ocr_Catalog"]

    logging.info(
        "Label OCR: catalog=%s matrix=%s label=%s year=%s conf=%s (image=%s)",
        ocr_fields.get("Ocr_Catalog") or "",
        ocr_fields.get("Ocr_Matrix") or "",
        ocr_fields.get("Ocr_Label") or "",
        ocr_fields.get("Ocr_Year") or "",
        ocr_fields.get("Ocr_Scan_Confidence"),
        image_path,
    )

    return enriched

import re
import unicodedata

def sanitize_for_discogs(q: str) -> str:
    """
    Normalize and strip punctuation so Discogs search sees clean ASCII tokens.
    Example:
      'Nu Shooz – I Can’t Wait – DM-49073 – Atlantic – stereo – 1986'
      -> 'Nu Shooz I Cant Wait DM49073 Atlantic stereo 1986'
    """
    if not q:
        return ""

    # Normalize unicode accents / punctuation to ASCII
    q = unicodedata.normalize("NFKD", q)

    # Replace smart quotes & apostrophes with ASCII versions
    q = q.replace("’", "'").replace("‘", "'")
    q = q.replace("“", '"').replace("”", '"')

    # Replace en-dash and em-dash with spaces
    q = q.replace("–", " ").replace("—", " ")

    # Remove everything except letters, digits, and spaces
    q = re.sub(r"[^A-Za-z0-9 ]+", " ", q)

    # Collapse multiple spaces
    q = re.sub(r"\s+", " ", q)

    return q.strip()



def build_discogs_query_with_label(meta: Mapping[str, Any]) -> str:
    """
    Build a Discogs search query string combining:
      - Artist / Title
      - Spreadsheet + OCR catalog/label
      - OCR matrix (if present; used as a hint)
      - Stereo/Mono
      - Year (ALWAYS last in the query)

    Then sanitize to ASCII tokens for Discogs.
    """
    artist = str(meta.get("artist_display") or meta.get("Artist") or "").strip()
    title = str(meta.get("title_display") or meta.get("Title") or "").strip()

    parts: List[str] = []

    # Artist / Title first
    if artist:
        parts.append(artist)
    if title:
        parts.append(title)

    # Catalog – prefer sheet catalog, then OCR catalog
    cat_sheet = str(meta.get("Catalog Number") or "").strip()
    cat_ocr = str(meta.get("Ocr_Catalog") or "").strip()
    catalog = cat_sheet or cat_ocr
    if catalog:
        parts.append(f"Cat:{catalog}")

    # Label – prefer sheet label, then OCR label
    lab_sheet = str(meta.get("Label") or "").strip()
    lab_ocr = str(meta.get("Ocr_Label") or "").strip()
    label = lab_sheet or lab_ocr
    if label:
        parts.append(f"Lbl:{label}")

    # Matrix hint (optional)
    matrix = str(meta.get("Ocr_Matrix") or "").strip()
    if matrix and len(matrix) >= 5:
        parts.append(f"Mat:{matrix}")

    # Stereo / Mono
    stereo = str(meta.get("Ocr_StereoMono") or "").strip()
    if stereo:
        parts.append(stereo)

    # Year – ALWAYS appended last
    year_sheet = str(meta.get("Year") or "").strip()
    year_ocr = str(meta.get("Ocr_Year") or "").strip()
    year = year_sheet or year_ocr
    if year:
        parts.append(f"Yr:{year}")

    # Join and sanitize (this guarantees raw_q is always defined)
    raw_q = " ".join(parts)
    return sanitize_for_discogs(raw_q)



def detect_label_misprint(
    meta: Mapping[str, Any],
    discogs_details: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Compare OCR label/catalog vs. Discogs vs. spreadsheet to see if there is a
    hint of a misprint.

    Returns:
        {
            "Label_Misprint_Suspected": bool,
            "Label_Misprint_Reasons": str,
        }
    """
    reasons: List[str] = []

    cat_sheet = str(meta.get("Catalog Number") or "").strip().upper()
    cat_label = str(meta.get("Label_Catalog_Number") or "").strip().upper()
    cat_ocr = str(meta.get("Ocr_Catalog") or "").strip().upper()

    discogs_labels_list = discogs_details.get("labels") or []
    discogs_catnos = {
        str(lbl.get("catno", "")).strip().upper()
        for lbl in discogs_labels_list
        if str(lbl.get("catno", "")).strip()
    }

    if cat_ocr and discogs_catnos and cat_ocr not in discogs_catnos:
        reasons.append(f"OCR catalog '{cat_ocr}' not in Discogs catnos {sorted(discogs_catnos)}")

    if cat_label and discogs_catnos and cat_label not in discogs_catnos:
        reasons.append(f"Label-derived catalog '{cat_label}' not in Discogs catnos {sorted(discogs_catnos)}")

    if cat_sheet and discogs_catnos and cat_sheet not in discogs_catnos:
        reasons.append(f"Spreadsheet catalog '{cat_sheet}' not in Discogs catnos {sorted(discogs_catnos)}")

    lab_sheet = str(meta.get("Label") or "").strip().upper()
    lab_ocr = str(meta.get("Ocr_Label") or "").strip().upper()
    discogs_label_names = {
        str(lbl.get("name", "")).strip().upper()
        for lbl in discogs_labels_list
        if str(lbl.get("name", "")).strip()
    }

    if lab_ocr and discogs_label_names and lab_ocr not in discogs_label_names:
        reasons.append(f"OCR label '{lab_ocr}' not in Discogs labels {sorted(discogs_label_names)}")

    if lab_sheet and discogs_label_names and lab_sheet not in discogs_label_names:
        reasons.append(f"Spreadsheet label '{lab_sheet}' not in Discogs labels {sorted(discogs_label_names)}")

    suspected = bool(reasons)
    return {
        "Label_Misprint_Suspected": suspected,
        "Label_Misprint_Reasons": "; ".join(reasons),
    }


# ---------------------------------------------------------------------------
# Standalone test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Quick manual test:

    1. Edit `test_image` below to point to:
         • a local file path, OR
         • a full URL to a center-label image.

    2. From the repo folder, run:
         python label_ocr.py

    3. Watch the console for:
         • debug prints from download / OCR
         • printed ENRICHED META fields
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )

    # SET THIS to your test file:
    test_image = r"C:\Users\unbre\.discogs_to_shopify\ocr_cache\f963080011c444ee.webp"

    import os
    print("DEBUG: test_image exists?:", os.path.exists(test_image))

    if not test_image:
        print("Please set test_image in label_ocr.py __main__ block before testing.")
    else:
        print("Running standalone OCR test on:", test_image)
        lines = _run_ocr(test_image)
        print("\nRAW OCR LINES:")
        for i, ln in enumerate(lines, 1):
            print(f"{i:02d}: {ln}")

        meta = {}
        row = {"Center label photo": test_image}
        enriched = enrich_meta_with_label(meta, row)

        print("\nENRICHED META FROM TEST:")
        for k, v in enriched.items():
            print(f"{k}: {v!r}")
