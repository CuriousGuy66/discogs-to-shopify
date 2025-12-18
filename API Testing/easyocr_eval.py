import argparse
from pathlib import Path
import sys

import numpy as np
from PIL import Image

# Ensure repo root on path for any shared helpers, if needed later
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Optional: use existing Tesseract helper if available
try:
    import label_ocr
    LABEL_OCR_READY = getattr(label_ocr, "OCR_AVAILABLE", False)
except Exception:
    label_ocr = None
    LABEL_OCR_READY = False

try:
    import easyocr
except ImportError as exc:
    raise SystemExit("easyocr is not installed. Install with: python -m pip install easyocr pillow torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu") from exc


def load_image(path_or_url: str) -> np.ndarray:
    """Load image with EasyOCR's helper (supports URLs)."""
    return easyocr.utils.loadImage(path_or_url)


def run_easyocr(image_path: str, overlay_path: str | None = None, min_conf: float = 0.3, min_len: int = 2):
    # Initialize reader once (English by default)
    reader = easyocr.Reader(["en"], gpu=False)

    base_img = load_image(image_path)

    # Try simple rotations to counter upside-down/sideways labels
    angles = (0, 90, 180, 270)
    best = None  # (score, angle, results, rotated_image)
    for angle in angles:
        pil_img = Image.fromarray(base_img)
        rotated = pil_img.rotate(angle, expand=True)
        rotated_np = np.array(rotated)
        results = reader.readtext(rotated_np)
        # Score: total confidence for accepted texts
        score = sum(r[2] for r in results if r[2] >= min_conf and len(str(r[1])) >= min_len)
        if best is None or score > best[0]:
            best = (score, angle, results, rotated_np)

    if best is None:
        print("EasyOCR: no results.")
        return []

    _, best_angle, results, best_img = best
    print(f"EasyOCR picked rotation {best_angle} degrees")

    # Filter and print text-only list
    filtered = []
    for bbox, text, conf in results:
        if conf < min_conf:
            continue
        text_clean = str(text).strip()
        if len(text_clean) < min_len:
            continue
        filtered.append((bbox, text_clean, conf))

    if not filtered:
        print("EasyOCR: no filtered results (try lowering min_conf or min_len)")
    else:
        print("EasyOCR detected text:")
        for i, (_, txt, conf) in enumerate(filtered, 1):
            print(f"{i:02d}: {txt}  (conf={conf:.2f})")

    # Save overlay if requested
    if overlay_path:
        boxes = [b for b, _, _ in filtered]
        texts = [t for _, t, _ in filtered]
        scores = [c for _, _, c in filtered]
        annotated = easyocr.utils.draw_boxes(best_img, boxes, scores, texts)
        Image.fromarray(annotated).save(overlay_path)
        print(f"Saved overlay with boxes to: {overlay_path}")

    return filtered


def run_tesseract(image_path: str):
    """
    Run existing label_ocr Tesseract path if available; otherwise skip gracefully.
    """
    if not (label_ocr and LABEL_OCR_READY):
        print("Tesseract (label_ocr) not available in this environment; skipping.")
        return []

    print("Running Tesseract-based OCR (label_ocr._run_ocr)...")
    lines = label_ocr._run_ocr(image_path)
    if not lines:
        print("Tesseract returned no lines.")
        return []

    print("Tesseract lines:")
    for i, ln in enumerate(lines, 1):
        print(f"{i:02d}: {ln}")
    return lines


def main():
    parser = argparse.ArgumentParser(description="Quick EasyOCR eval for a label image")
    parser.add_argument("image", help="Path or URL to center-label image")
    parser.add_argument("--overlay", default="easyocr_overlay.png", help="Where to save annotated overlay")
    parser.add_argument("--min-conf", type=float, default=0.3, help="Minimum confidence to keep a detection (default: 0.3)")
    parser.add_argument("--min-len", type=int, default=2, help="Minimum text length to keep a detection (default: 2)")
    args = parser.parse_args()

    if not args.image.lower().startswith("http") and not Path(args.image).exists():
        parser.error(f"Image not found: {args.image}")

    # Side-by-side: Tesseract (if available) then EasyOCR
    run_tesseract(args.image)
    run_easyocr(args.image, overlay_path=args.overlay, min_conf=args.min_conf, min_len=args.min_len)


if __name__ == "__main__":
    main()
