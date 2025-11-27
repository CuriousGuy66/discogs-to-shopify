# label_ocr.py
# =====================================================================
# Center-label OCR and parsing logic for vinyl records.
#
# This module is called from user_logic.py so that label-processing
# logic is centralized here. If we change OCR, parsing, or misprint
# detection later, we only touch this file.
# =====================================================================

from typing import Dict, Any, Optional, List
import re
import os

# Try to import OCR dependencies (Pillow + Tesseract)
try:
    from PIL import Image
    import pytesseract
    _HAS_OCR = True
except Exception:
    _HAS_OCR = False


def ocr_label_image(image_path: str) -> Optional[str]:
    """
    Run OCR on a center label image and return the raw text, or None on failure.

    IMPORTANT:
    - We DO NOT 'correct' the text. Misprints are preserved exactly as seen.
    - For now, this only handles local file paths (not URLs).
    """
    if not _HAS_OCR:
        return None

    if not image_path:
        return None

    # Only handle local files; URL downloading (if needed) can be added later.
    if image_path.lower().startswith("http"):
        return None

    if not os.path.exists(image_path):
        return None

    try:
        img = Image.open(image_path)
        # Simple pre-processing: convert to grayscale
        img = img.convert("L")
        text = pytesseract.image_to_string(img)
        text = text.replace("\n", " ").strip()
        # Collapse multiple spaces
        return re.sub(r"\s{2,}", " ", text)
    except Exception:
        return None
