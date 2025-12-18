import argparse
from pathlib import Path
import sys

import numpy as np
from PIL import Image

# Ensure repo root on sys.path for optional shared helpers
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Optional Tesseract via existing label_ocr
try:
    import label_ocr
    LABEL_OCR_READY = getattr(label_ocr, "OCR_AVAILABLE", False)
except Exception:
    label_ocr = None
    LABEL_OCR_READY = False

try:
    from paddleocr import PaddleOCR, draw_ocr
except ImportError as exc:
    raise SystemExit(
        "paddleocr is not installed. Use a Python 3.11 venv and run:\n"
        "  python -m pip install --upgrade pip\n"
        "  python -m pip install paddlepaddle==2.6.2 paddleocr pillow\n"
        "GPU users: install the matching paddlepaddle-gpu wheel for your CUDA."
    ) from exc


def load_image(path_str: str) -> Image.Image:
    """Load a local image as RGB."""
    p = Path(path_str)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {path_str}")
    return Image.open(p).convert("RGB")


def run_tesseract(image_path: str):
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


def run_paddleocr(image_path: str, overlay_path: str | None, min_conf: float, min_len: int):
    print("Running PaddleOCR with multi-angle search (0/90/180/270)...")
    ocr = PaddleOCR(use_angle_cls=True, lang="en", use_gpu=False)

    base_img = load_image(image_path)
    angles = (0, 90, 180, 270)
    best = None  # (score, angle, filtered, rotated_np)

    for angle in angles:
        rotated = base_img.rotate(angle, expand=True)
        rotated_np = np.array(rotated)
        result = ocr.ocr(rotated_np, cls=True)
        entries = result[0] if result else []
        filtered = []
        score = 0.0
        for entry in entries:
            box, (text, conf) = entry
            text_clean = (text or "").strip()
            if conf < min_conf or len(text_clean) < min_len:
                continue
            filtered.append((box, text_clean, conf))
            score += conf
        if best is None or score > best[0]:
            best = (score, angle, filtered, rotated_np)

    if best is None:
        print("PaddleOCR: no results.")
        return []

    _, best_angle, filtered, best_img = best
    print(f"PaddleOCR picked rotation {best_angle} degrees")

    if not filtered:
        print("PaddleOCR: no filtered results (try lowering --min-conf or --min-len)")
    else:
        print("PaddleOCR detected text:")
        for i, (_, txt, conf) in enumerate(filtered, 1):
            print(f"{i:02d}: {txt}  (conf={conf:.2f})")

    if overlay_path:
        boxes = [b for b, _, _ in filtered]
        texts = [t for _, t, _ in filtered]
        scores = [c for _, _, c in filtered]
        annotated = draw_ocr(best_img, boxes, texts, scores, font_path=None)
        annotated.save(overlay_path)
        print(f"Saved overlay with boxes to: {overlay_path}")

    return filtered


def main():
    parser = argparse.ArgumentParser(description="PaddleOCR eval for a label image")
    parser.add_argument("image", help="Path to center-label image (local)")
    parser.add_argument("--overlay", default="paddleocr_overlay.png", help="Where to save annotated overlay")
    parser.add_argument("--min-conf", type=float, default=0.3, help="Minimum confidence to keep a detection (default: 0.3)")
    parser.add_argument("--min-len", type=int, default=2, help="Minimum text length to keep a detection (default: 2)")
    args = parser.parse_args()

    if not Path(args.image).exists():
        parser.error(f"Image not found: {args.image}")

    # Tesseract comparison (if available)
    run_tesseract(args.image)
    # PaddleOCR run
    run_paddleocr(args.image, overlay_path=args.overlay, min_conf=args.min_conf, min_len=args.min_len)


if __name__ == "__main__":
    main()
