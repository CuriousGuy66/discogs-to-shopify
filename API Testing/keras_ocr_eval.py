import argparse
import tempfile
from pathlib import Path
import sys

import keras_ocr
import numpy as np
from PIL import Image

# Ensure repo root is on sys.path so label_ocr imports cleanly when run from CLI.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import label_ocr

try:
    import pytesseract
except Exception:
    pytesseract = None


def maybe_autorotate(image_path: str) -> str:
    """
    Try a quick auto-rotate/deskew pass using Tesseract's OSD.
    Falls back to the original path if rotation isn't available.
    """
    if pytesseract is None or not hasattr(pytesseract, "image_to_osd"):
        return image_path

    # Resolve URLs through label_ocr's cache helper so PIL can open the file.
    local_path = image_path
    if image_path.lower().startswith("http"):
        local_path = label_ocr._resolve_image_path(image_path) or image_path

    try:
        img = Image.open(local_path)
    except Exception:
        return image_path

    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        angle = float(osd.get("rotate", 0))
    except Exception:
        return image_path

    if angle % 360 == 0:
        return image_path

    rotated = img.rotate(-angle, expand=True)
    tmp_dir = Path(tempfile.gettempdir()) / "keras_ocr_eval"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    rotated_path = tmp_dir / f"deskewed_{Path(image_path).stem}.png"
    rotated.save(rotated_path)
    print(f"Auto-rotated by {angle} degrees -> {rotated_path}")
    return str(rotated_path)


def run_tesseract(image_path: str):
    if not getattr(label_ocr, "OCR_AVAILABLE", False):
        print("Tesseract/PIL not available in this environment; skipping Tesseract run.")
        return []

    print("Running Tesseract-based OCR (label_ocr._run_ocr)...")
    lines = label_ocr._run_ocr(image_path)
    print("Tesseract lines:")
    for i, line in enumerate(lines, 1):
        print(f"{i:02d}: {line}")
    return lines


def run_keras(image_path: str, output_overlay: str, pipeline: keras_ocr.pipeline.Pipeline):
    print("Running Keras-OCR pipeline with multi-angle search (0/90/180/270)...")
    base_image = keras_ocr.tools.read(image_path)

    best = None  # (score, angle, predictions, rotated_image)
    for angle in (0, 90, 180, 270):
        # Rotate via Pillow to keep orientation correct
        pil_img = Image.fromarray(base_image)
        rotated_img = pil_img.rotate(angle, expand=True)
        rotated_np = np.array(rotated_img)
        preds = pipeline.recognize([rotated_np])[0]
        # Score: more detections + longer text -> higher score
        score = (len(preds), sum(len(str(t)) for t, _ in preds))
        if best is None or score > best[0]:
            best = (score, angle, preds, rotated_np)

    if best is None:
        print("Keras-OCR: no predictions.")
        return []

    (_, best_angle, predictions, best_image) = best
    print(f"Keras-OCR picked rotation {best_angle} degrees.")

    # Readable text-only list
    print("Keras-OCR detected text (best angle):")
    texts = []
    for text, _ in predictions:
        texts.append(str(text))
    for i, text in enumerate(texts, 1):
        print(f"{i:02d}: {text}")

    if output_overlay:
        boxes = [box for _, box in predictions]
        annotated = keras_ocr.tools.drawBoxes(image=best_image, boxes=boxes)
        Image.fromarray(annotated).save(output_overlay)
        print(f"Saved overlay with boxes to: {output_overlay}")

    return predictions


def main():
    parser = argparse.ArgumentParser(
        description="Compare label_ocr (Tesseract) vs. Keras-OCR on a label image",
    )
    parser.add_argument("image", help="Path or URL to center-label image")
    parser.add_argument(
        "--overlay",
        default="keras_ocr_overlay.png",
        help="Path to save annotated overlay (default: %(default)s)",
    )
    args = parser.parse_args()

    image_path = args.image
    if not image_path.lower().startswith("http") and not Path(image_path).exists():
        parser.error(f"Image not found: {image_path}")

    # Quick deskew/auto-rotate path (uses Tesseract OSD). Falls back if not available.
    deskewed_path = maybe_autorotate(image_path)

    # Build Keras pipeline once
    pipeline = keras_ocr.pipeline.Pipeline()

    tesseract_lines = run_tesseract(deskewed_path)
    keras_preds = run_keras(deskewed_path, args.overlay, pipeline)

    print("\nSummary:")
    print(f"- Tesseract lines: {len(tesseract_lines)}")
    print(f"- Keras-OCR detections: {len(keras_preds)}; overlay -> {args.overlay}")
    print("\nNext: inspect the overlay image and text outputs to judge quality.")


if __name__ == "__main__":
    main()
