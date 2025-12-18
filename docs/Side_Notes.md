# Side Notes for Album Listing Project

## Pricing Strategy Key
- **Minimum price:** $2.50  
- **Round to nearest quarter:** Always round the computed price to the nearest $0.25  
- **Base price source:** Spreadsheet Reference Price  
- **If spreadsheet price missing:** Use default $2.50  
- **Pricing Adjustments (future phases):**  
  - eBay Sold Comparisons (currently disabled)  
  - Condition modifiers  
  - Genre/Signage premium adjustments  

---

## Condition Matching Key
- **Album_Condition metafield:** Always "Used"  
- **Album_Cover_Condition metafield:** Pulled from spreadsheet Sleeve Condition  
- **Discogs conditions:** Not used directly for Shopify  
- **Condition notes:**  
  - If center label photo missing → no penalty  
  - If stock photo used → add metafield `uses_stock_photo = true`  

---

## Pricing Notes Column (Template)
This is the template to use inside internal spreadsheets or logs:

```
Pricing Notes:
- Reference Price: <value from sheet>
- Adjusted Price: <final price after rounding>
- Source: Spreadsheet / Fallback / Manual
- Condition Impact: None (phase 2 will add)
- eBay Sold Reference: Disabled
- Final Shopify Price: <computed price>
```

---

## Other Side Notes
- Unique Shopify handles required for duplicates  
- Discogs cover image = image #1  
- Center label photo = image #2  
- Shop Signage metafield logic (Rules):  
  - Gospel → Gospel  
  - Christmas → Christmas  
  - If Religious but not Gospel or Holiday → Religious  
  - Stage & Sound → includes Soundtrack  
  - Bluegrass → if in Discogs styles  
- OCR toggle: `LABEL_OCR_ENABLED` in `label_ocr.py` (currently **OFF** by default; set to True to enable). OCR runs before lookup so Discogs/MusicBrainz can use its hints when enabled.
- OCR status/details:
  - `label_ocr_v2.py` is a standalone, region-focused OCR (polar unwrap top/bottom/left/right) with catalog/matrix regex and track extraction; run via CLI and read results in the console.
  - Eval scripts: `API Testing/easyocr_eval.py` (EasyOCR) and `API Testing/paddleocr_eval.py` (PaddleOCR) for side-by-side checks.
  - PaddleOCR must run in Python 3.11 venv (e.g., `.venv-paddle`): `pip install paddlepaddle==2.6.2 paddleocr pillow` inside that venv; main app stays on 3.12. If accidentally installed in 3.12, uninstall there and reinstall in 3.11.
  - Tesseract/Pillow/OpenCV needed in whichever env runs OCR: `pip install pillow pytesseract opencv-python`.
  - Docker container option not set up; if needed later, build a 3.11 image with Paddle/Tesseract and call it via CLI/HTTP instead of mixing environments.
- Optional test columns: `MUSICBRAINZ_ALBUMID` (force a specific release MBID) and `MUSICBRAINZ_RELEASEGROUPID` (fallback to a release-group; app will pick a release inside it using label/catalog/barcode/country/year hints).
- Future DB note: when a database is added, consider a lookup table of normalized label names/aliases to improve MusicBrainz/Discogs label matching (e.g., strip “Records”, parenthetical suffixes, and map common variants).
- When OCR is enabled, add a sanity check for OCR-extracted artist/release strings to ensure they match or closely align with the spreadsheet values (avoid chasing misspellings from OCR output).
- Future camera/app note: when capturing photos, leverage physical size/stack count (e.g., 12\" LP vs 7\" single) to strengthen MusicBrainz format matching.
- Optional test column: `MUSICBRAINZ_ALBUMID` can be added to the spreadsheet to force a specific MBID for a row; the app will use it and pull any Discogs relation before searching.

## Installer Plan (Program Files)
- Build EXE via PyInstaller (current workflow, `--noconsole`).
- Use Inno Setup to package the EXE into an installer targeting `{pf}\\Discogs to Shopify` with Start Menu/Desktop shortcuts and uninstall entry.
- In GitHub Actions (Windows), add steps after PyInstaller: install Inno Setup (e.g., `choco install innosetup`), run `iscc` on the `.iss` script, and upload the installer artifact/release asset.
- Keep user-writable data in `%APPDATA%`/`%LOCALAPPDATA%`; installer should only place binaries under Program Files.
- If available, code-sign both the EXE and installer to reduce SmartScreen prompts.

## Future Plan (API + Mobile + OCR)
- Step 1: Abstract outputs and processing; move core logic into `core/` with models, processors, and exporters.
- Step 1a: Add a lightweight database backend (SQLite first) to track jobs/products/unmatched and enable syncing to multiple marketplaces (Shopify now; eBay/Discogs later). Keep persistence optional so desktop runs can stay file-based.
- Step 2: Shopify API exporter to create products directly; keep CSV exporter for fallback.
- Step 3: Mobile/Flutter front end using camera input; expose core via an HTTP API that accepts images and returns drafts/unmatched.
- Step 4: Etching OCR hook (pluggable `EtchingReader`) to feed Discogs search; keep it optional with a stub until ready.
