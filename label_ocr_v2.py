#!/usr/bin/env python3
"""
label_ocr_v2.py
----------------------------------------------------------------------
Region-focused OCR for vinyl center labels.

- Detects the label circle (HoughCircles fallback to center crop)
- Polar unwraps specific regions:
    * Top arc (label / stereo / side)
    * Bottom arc (catalog / matrix / copyright)
    * Left wedge (tracks)
    * Right wedge (tracks)
- Runs OCR per region (Tesseract; optional PaddleOCR if installed)
- Extracts catalog / matrix via regex
- Extracts track list with basic heuristics

Dependencies (install into your environment):
    python -m pip install opencv-python pillow pytesseract
Optional Paddle fallback (in a Python 3.11 venv):
    python -m pip install paddlepaddle==2.6.2 paddleocr
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# Optional imports with graceful fallbacks
try:
    import cv2
except Exception:
    cv2 = None

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    from paddleocr import PaddleOCR
except Exception:
    PaddleOCR = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")

TESS_CONFIG_MAIN = "--psm 6 --oem 3"
TESS_CONFIG_TRACKS = "--psm 4 --oem 3"

CAT_REGEX = re.compile(r"[A-Z]{2,5}[- ]?\d{3,6}")
CAT_REGEX_LOOSE = re.compile(r"[A-Z]{2,5}(?:[- ]?\d){3,7}")
MATRIX_REGEX = re.compile(r"[A-Z0-9][A-Z0-9\\-\\/\\.]{3,15}[A-Z0-9]")
TRACK_LINE_REGEX = re.compile(r"^(?:[ABCD] ?)?\\d+[\\.\\)]?\\s+.*", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class RegionResult:
    name: str
    lines: List[str]
    engine: str


@dataclass
class OCRBundle:
    regions: List[RegionResult]
    catalogs: List[str]
    matrices: List[str]
    tracks: List[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    text = text or ""
    text = text.strip()
    return re.sub(r"\\s+", " ", text)


def _extract_catalogs(text: str) -> List[str]:
    txt = text.upper()
    found = set(m.group(0).replace(" ", "").replace("--", "-") for m in CAT_REGEX.finditer(txt))
    for m in CAT_REGEX_LOOSE.finditer(txt):
        cleaned = re.sub(r"[- ]+", "-", m.group(0)).upper()
        if len(cleaned) >= 5:
            found.add(cleaned)
    return sorted(found)


def _extract_matrices(text: str) -> List[str]:
    txt = text.upper()
    found: List[str] = []
    for m in MATRIX_REGEX.finditer(txt):
        tok = m.group(0)
        if re.match(r"^[A-Z]{2,5}-?\\d{3,6}$", tok.replace(" ", "")):
            # likely a catalog; skip here
            continue
        if 5 <= len(tok) <= 17:
            found.append(tok)
    # Dedup preserving order
    uniq: List[str] = []
    for tok in found:
        if tok not in uniq:
            uniq.append(tok)
    return uniq


def _extract_tracks(lines: Sequence[str]) -> List[str]:
    tracks: List[str] = []
    for ln in lines:
        norm = _normalize_text(ln)
        if not norm:
            continue
        if TRACK_LINE_REGEX.match(norm) or re.search(r"[A-Za-z]", norm):
            tracks.append(norm)
    # Simple dedup
    seen = set()
    out = []
    for t in tracks:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# Image transforms
# ---------------------------------------------------------------------------

def detect_label_circle(img_bgr: np.ndarray) -> Tuple[Tuple[int, int], int]:
    """Detect the label circle; fallback to center-based estimate."""
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=min(h, w) // 4,
        param1=100,
        param2=30,
        minRadius=min(h, w) // 6,
        maxRadius=min(h, w) // 2,
    )
    if circles is not None and len(circles) > 0:
        c = circles[0][0]
        center = (int(c[0]), int(c[1]))
        radius = int(c[2])
        return center, radius
    # Fallback: centered circle
    center = (w // 2, h // 2)
    radius = int(min(h, w) * 0.35)
    return center, radius


def polar_unwrap(
    img_bgr: np.ndarray,
    center: Tuple[int, int],
    radius: int,
    angle_start: float,
    angle_end: float,
    height_scale: float = 1.0,
) -> np.ndarray:
    """
    Unwrap a polar sector into a rectangle.
    angle_start/end in degrees (0 = right, counterclockwise).
    """
    angle_span = (angle_end - angle_start) % 360
    if angle_span == 0:
        angle_span = 360
    dest_w = int(angle_span)
    dest_h = int(radius * height_scale)
    polar = cv2.warpPolar(
        img_bgr,
        (dest_w, dest_h),
        center,
        radius,
        cv2.WARP_POLAR_LINEAR + cv2.WARP_FILL_OUTLIERS,
    )
    # Rotate polar so angle_start maps to left edge
    shift_cols = int(angle_start % 360)
    polar = np.roll(polar, -shift_cols, axis=1)
    # Crop to span
    if dest_w < polar.shape[1]:
        polar = polar[:, :dest_w]
    return polar


def preprocess(gray: np.ndarray) -> np.ndarray:
    gray = cv2.equalizeHist(gray)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    thresh = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        25,
        15,
    )
    return thresh


# ---------------------------------------------------------------------------
# OCR runners
# ---------------------------------------------------------------------------

def ocr_tesseract(img: np.ndarray, config: str) -> List[str]:
    if pytesseract is None:
        return []
    pil = Image.fromarray(img)
    try:
        text = pytesseract.image_to_string(pil, config=config)
    except Exception:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines


def ocr_paddle(img: np.ndarray) -> List[str]:
    if PaddleOCR is None:
        return []
    ocr = PaddleOCR(use_angle_cls=True, lang="en", use_gpu=False, det=True, rec=True)
    res = ocr.ocr(img, cls=True)
    entries = res[0] if res else []
    out: List[str] = []
    for entry in entries:
        _, (text, conf) = entry
        if conf >= 0.3 and len(text.strip()) >= 2:
            out.append(text.strip())
    return out


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_regions(img_bgr: np.ndarray) -> OCRBundle:
    center, radius = detect_label_circle(img_bgr)

    regions: List[Tuple[str, float, float, float]] = [
        ("top_arc", 330, 30, 0.6),
        ("bottom_arc", 150, 210, 0.6),
        ("left_wedge", 210, 330, 1.0),
        ("right_wedge", 30, 150, 1.0),
    ]

    results: List[RegionResult] = []

    for name, a_start, a_end, h_scale in regions:
        sector = polar_unwrap(img_bgr, center, radius, a_start, a_end, height_scale=h_scale)
        gray = cv2.cvtColor(sector, cv2.COLOR_BGR2GRAY)
        proc = preprocess(gray)
        tess_cfg = TESS_CONFIG_TRACKS if "wedge" in name else TESS_CONFIG_MAIN
        lines = ocr_tesseract(proc, tess_cfg)
        engine = "tesseract"

        # Optional Paddle fallback: only if no lines from Tesseract
        if not lines and PaddleOCR is not None:
            lines = ocr_paddle(proc)
            engine = "paddle" if lines else engine

        results.append(RegionResult(name=name, lines=lines, engine=engine))

    # Aggregate text for catalog/matrix
    combined_text = " ".join(" ".join(r.lines) for r in results)
    catalogs = _extract_catalogs(combined_text)
    matrices = _extract_matrices(combined_text)

    # Tracks from left/right wedges only
    track_lines: List[str] = []
    for r in results:
        if "wedge" in r.name:
            track_lines.extend(r.lines)
    tracks = _extract_tracks(track_lines)

    return OCRBundle(regions=results, catalogs=catalogs, matrices=matrices, tracks=tracks)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Region-focused label OCR with polar unwrap")
    parser.add_argument("image", help="Path to label image (local file)")
    args = parser.parse_args()

    if cv2 is None:
        raise SystemExit("OpenCV (opencv-python) is required but not installed.")
    if not Path(args.image).exists():
        raise SystemExit(f"Image not found: {args.image}")

    bgr = cv2.imread(args.image)
    if bgr is None:
        raise SystemExit(f"Failed to read image: {args.image}")

    bundle = run_regions(bgr)

    print("\n== Region OCR ==")
    for r in bundle.regions:
        print(f"[{r.name}] engine={r.engine} lines={len(r.lines)}")
        for i, ln in enumerate(r.lines, 1):
            print(f"  {i:02d}: {ln}")

    print("\n== Catalog candidates ==")
    for cat in bundle.catalogs or ["<none>"]:
        print(f"- {cat}")

    print("\n== Matrix candidates ==")
    for m in bundle.matrices or ["<none>"]:
        print(f"- {m}")

    print("\n== Tracks (merged wedges) ==")
    for t in bundle.tracks or ["<none>"]:
        print(f"- {t}")


if __name__ == "__main__":
    main()
