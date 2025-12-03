#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_watermark.py
===============================================================================
Helper to create a diagonal semi-transparent "STOCK PHOTO" watermark on
Discogs cover images.

- Downloads the image from URL.
- Draws "STOCK PHOTO" in semi-transparent white, diagonally.
- Saves to a cache directory (you pass the path).
- Returns the local file path to the watermarked image.
- If anything fails, returns the original image_url unchanged.
===============================================================================
"""

from __future__ import annotations

import logging
import os
import re
from io import BytesIO
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


def _safe_slug(text: str) -> str:
    """
    Very simple slugifier for filenames: lowercase, letters/numbers/_/- only.
    """
    text = (text or "").strip().lower()
    if not text:
        return "watermarked"
    slug = re.sub(r"[^a-z0-9_-]+", "-", text)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "watermarked"


def watermark_stock_photo(
    image_url: str,
    cache_dir: str,
    handle: Optional[str] = None,
) -> str:
    """
    Download an image from image_url, apply a diagonal semi-transparent
    'STOCK PHOTO' watermark, save into cache_dir (unique filename per handle),
    and return the local file path.

    If anything fails, returns the original image_url.
    """

    if not image_url:
        return ""

    # --- Download image ---
    try:
        resp = requests.get(image_url, timeout=20)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
    except Exception as e:
        logger.warning(
            "Watermark: failed to download or open image %s: %s",
            image_url,
            e,
        )
        return image_url  # fallback to original

    width, height = img.size

    # --- Prepare text layer ---
    text = "STOCK PHOTO"
    txt_layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)

    # Font size proportional to image width
    fontsize = max(36, width // 10)
    try:
        font = ImageFont.truetype("arial.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    # Text size + center position
    text_w, text_h = draw.textsize(text, font=font)
    x = (width - text_w) // 2
    y = (height - text_h) // 2

    # Draw text on its own layer
    text_img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    text_draw = ImageDraw.Draw(text_img)

    # Semi-transparent white
    fill = (255, 255, 255, 120)

    text_draw.text((x, y), text, font=font, fill=fill)

    # Rotate diagonally
    angle = -30
    rotated = text_img.rotate(angle, expand=False, resample=Image.BICUBIC)

    # Composite onto original
    watermarked = Image.alpha_composite(img, rotated)

    # --- Save to cache_dir with unique filename ---
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except Exception as e:
        logger.warning("Watermark: failed to create cache dir %s: %s", cache_dir, e)
        return image_url

    base = _safe_slug(handle) if handle else _safe_slug("")
    filename = f"{base}_stock_photo.png"
    out_path = os.path.join(cache_dir, filename)

    try:
        watermarked.convert("RGB").save(out_path, "PNG")
    except Exception as e:
        logger.warning(
            "Watermark: failed to save watermarked image %s: %s",
            out_path,
            e,
        )
        return image_url

    logger.info("Watermark: saved watermarked cover to %s", out_path)
    return out_path
