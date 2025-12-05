
---

## 2) `docs/Album_Listing_App_Appendix.md`

```markdown
# Album Listing App – Technical Appendix & History

This appendix contains the deeper implementation notes, historical decisions, pricing strategy drafts, OCR design details, and roadmap items that support the main **Album Listing App – Master Overview** document.

---

## 1. Background and goals

The Album Listing App was built to solve a specific workflow for Unusual Finds:

- Large, evolving **vinyl inventory** (LPs, 45s, compilations, etc.).
- Need to enrich records with **Discogs metadata** (catalog numbers, labels, years, genres, barcodes, tracklists, images).
- Desire to eventually use **market pricing data** (primarily eBay sold listings) to drive in-store and online prices.
- Requirement to export a **Shopify-ready CSV** with:
  - Correct product types and categories.
  - Variants configured.
  - Metafields populated.
  - SEO tags and handles generated correctly.
  - Inventory and fulfillment settings consistent.

The app is intended as a **long-term tool** that can be extended with new pricing strategies, OCR improvements, image rules, and Shopify metafields without rewriting the entire codebase.

---

## 2. Detailed pipeline breakdown

### 2.1 Input expectations

The app expects an input spreadsheet (XLSX or CSV) with at least the following types of fields (column names may vary depending on configuration):

- **Artist**
- **Title**
- **Label**
- **Year**
- **Country** (for matching pressings)
- **Format / Type** (LP, 45, Compilation, Mono/Stereo, etc.)
- **Media Condition**
- **Sleeve Condition**
- **Price** (base price from spreadsheet)
- **Notes** (optional)
- **Image / Center label filename** (when OCR is used)
- Optional **Catalog** column for manual catalog numbers when available.

The core engine maps these columns into internal metadata for each album row.

---

### 2.2 Discogs search & match logic (detailed)

Discogs integration is handled by `discogs_client.py` and used by `discogs_to_shopify.py`.

Key ideas:

1. **Search query construction**
   - Start from normalized artist + title:
     - If artist starts with `"The "`, transform:
       - `The Who` → `Who, The`
     - Use this normalized artist for both Discogs queries and Shopify display where appropriate.
   - Include label and year when available:
     - `q = "<artist> <title>"`, with optional label/year filters.
   - Use `type=release` and `per_page` limit (e.g., 5).

2. **Filtering & scoring results**
   - Prefer matches where:
     - Title matches (case-insensitive, ignoring punctuation).
     - Label matches input label when available.
     - Country matches input.
     - Format matches (LP vs 45, Mono vs Stereo, etc.).
   - Additional bonus points when OCR provides a catalog number that appears in the Discogs result’s catalog field.

3. **Fallback behavior**
   - If catalog column is present in the spreadsheet but Discogs search still fails, logic can:
     - Retry using only catalog number and label.
     - Or fall back to spreadsheet-only data without Discogs enrichment.
   - Some of this behavior has evolved over time and may still exist in the legacy versions under `legacy_versions/`.

4. **Data extracted from Discogs**
   For a successful match, the app extracts:
   - Catalog Number
   - Release Year
   - Label
   - Genre and Style
   - Format details (LP, 45, Mono, Stereo, Compilation, etc.)
   - Barcode(s) when present
   - Tracklist (converted to HTML)
   - Discogs Release ID and URL
   - Primary image URL

---

### 2.3 OCR design (`label_ocr.py`)

OCR is used to assist in identifying catalog numbers and verifying label text directly from center-label photos.

**High-level flow:**

1. For each row, if a center-label image path is available:
   - Construct the full file path (often relative to an `input/` or images directory).
   - Load the image using Pillow (PIL).
2. Preprocess the image (if implemented):
   - Convert to grayscale.
   - Resize or crop to focus on label region.
   - Adjust contrast/sharpness/thresholding.
3. Run Tesseract via `pytesseract.image_to_string()`.
4. Parse the resulting text for patterns that look like catalog numbers:
   - E.g., alphanumeric strings with dashes: `LS-1234`, `ABC1234`, etc.
   - Clean possible OCR noise (replace common misreads: `S` ↔ `5`, `O` ↔ `0`, etc.).
5. Return:
   - Best guess at a catalog number (if any).
   - Additional text snippets that might help the Discogs query (e.g., label name, side indicators, track titles).

**Current status / issues:**

- OCR is **active** in the current pipeline.
- There is a flag or configuration intended to disable OCR (so rows would be processed without OCR), but the flag is not fully wired; in practice, `label_ocr.py` is still called.
- Catalog number extraction is often imperfect:
  - Sometimes Tesseract misses the catalog number entirely.
  - Sometimes it misreads characters in a way that reduces Discogs match quality.
- Future work includes:
  - Improving preprocessing and regex matching.
  - Allowing user to toggle OCR from the GUI and actually skip OCR calls when off.
  - Logging OCR extractions per row for debugging.

---

### 2.4 Pricing logic (`pricing.py` and `ebay_search.py`)

The pricing design has two conceptual levels: **current simple pricing** and **planned eBay-based dynamic pricing**.

#### 2.4.1 Current (active) pricing

At the moment, pricing is largely driven by:

- The **original spreadsheet price**, possibly adjusted by:
  - Rounding rules (e.g., round to nearest quarter).
  - Minimum price floor (e.g., not below a certain dollar amount).
- The final price is written directly into the Shopify CSV’s price fields.

Any integration with `pricing.py` is minimal and does **not** yet rely on eBay.

#### 2.4.2 Planned eBay-based pricing (not active yet)

The future intent (partially prototyped in `pricing.py` and `ebay_search.py`) is:

1. For each album (or for selected albums):
   - Build a search query for eBay sold listings using artist, title, format, and possibly catalog number.
2. Use `ebay_search.py` to:
   - Fetch sold listings within a recent time window (e.g., last 90 days).
   - Normalize for shipping (e.g., assume $5 shipping where missing).
   - Filter by condition (to approximate “Very Good”, etc.).
3. Compute a baseline price:
   - Example: median of sold prices, or mean with outlier trimming.
   - Apply a margin (e.g., 10% under eBay median for in-store pricing).
   - Apply a floor (e.g., never below $5).
   - Round to the nearest quarter.
4. Store:
   - Final price used.
   - A “Pricing Strategy Key” indicating which logic was applied.
   - Optional “Pricing Notes” (e.g., “No sold data; used active listings instead”).

Because this is **not yet wired into the core pipeline**, the app currently behaves as if only the spreadsheet price and basic transformations are used.

---

### 2.5 Shopify mapping details

The app builds a single Shopify row per album using Discogs + spreadsheet metadata. Important aspects:

1. **Handle generation**
   - Typically uses `artist + title + year` normalized and slugified.
   - Ensures uniqueness even for duplicate titles (e.g., adding suffixes if necessary).
   - Artist “The” rule is honored before handle creation:
     - `The Doors` → `Doors, The`.

2. **Title and Body (HTML)**
   - Title often follows a pattern like:
     - `Artist – Title (Year, Label)` or similar.
   - Body HTML includes:
     - Artist, Album Title, Label, Year, Format, Genre, Catalog number.
     - Media and Sleeve condition.
     - Barcode, where available.
     - Discogs link.
     - Tracklist rendered as HTML (e.g., `<ul><li>Track</li>...</ul>`).

3. **Images**
   - Primary image URL from Discogs (cover art).
   - Center-label photo from the original spreadsheet as a second image path/URL.
   - `image_watermark.py` previously added a “Discogs stock photo” watermark; going forward, the plan is to instead:
     - Use a metafield `uses_stock_photo` (boolean).
     - Stop modifying the actual image.

4. **Metafields**
   - `Album_Cover_Condition` = Sleeve Condition from the original spreadsheet.
   - `Album_Condition` = `'Used'` (for used albums).
   - Future metafields can record:
     - Whether the image is stock vs original.
     - Pricing strategy key.
     - Shop signage category.
     - OCR success/failure flags.

5. **Shop Signage / Category logic**
   - Genre from Discogs or spreadsheet is simplified into internal “Shop Signage” buckets.
   - Examples:
     - Any genre mentioning “Stage & Sound” → `Stage & Sound`.
     - Others mapped to categories like `Bluegrass`, `Gospel`, `Christmas`, etc.
   - This field can be used:
     - For physical shop signage.
     - For Shopify tags and filtering.

6. **Publishing & sales channels**
   - Product Type: `Vinyl Record`.
   - Product Category: `Media > Music > Vinyl Record`.
   - Published across: Pinterest, Google & YouTube, Inbox, Facebook & Instagram, Shop, Point of Sale, Online Store.
   - Variant is single (`Default Title`), with inventory quantity 1 and policy `deny`.

---

## 3. GUI design and behavior (`discogs_to_shopify_gui.py`)

The GUI script is the primary entry point for the user.

### 3.1 Typical elements

- Input file selector (XLSX/CSV).
- Output file selector (Shopify CSV).
- Possibly:
  - Log file location.
  - Settings/preferences (OCR on/off, Discogs options, etc.).
- “Run” button to start processing.
- Progress reporting (status messages, logs).

### 3.2 Settings persistence

- The GUI uses `discogs_to_shopify_settings.json` to remember:
  - Last used input/output paths.
  - Some runtime options.
- This allows the user to reopen the app and quickly re-run with similar parameters.

### 3.3 EXE builds

- The repo is structured so `discogs_to_shopify_gui.py` can be bundled into an EXE (e.g., via PyInstaller or a GitHub Actions workflow under `.github/`).
- The `.ico` file (`unusual_finds_icon_new.ico`) is used as the app icon.

---

## 4. Logging and troubleshooting

`uf_logging.py` centralizes log configuration, which is used by:

- `discogs_to_shopify.py`
- `discogs_client.py`
- `label_ocr.py`
- Possibly `pricing.py` / `ebay_search.py`

### 4.1 Log contents

Logs typically record:

- Start/stop times.
- File paths for input/output.
- Number of rows loaded.
- For each row:
  - Discogs search query.
  - Matched Discogs release ID.
  - Warnings for ambiguous or failed matches.
  - OCR results (where present).
- Errors and stack traces.

### 4.2 Common troubleshooting steps

- If **no rows** appear in the output CSV:
  - Check logs to see if the engine is skipping rows due to errors.
  - Verify the input column names and that header mapping is correct.
- If Discogs matching fails frequently:
  - Verify Discogs API credentials.
  - Check that artist/title normalization isn’t stripping too much.
  - Review OCR results to see if catalog numbers are too noisy.
- If OCR should be off but is still running:
  - Inspect where the OCR flag is read and ensure calls to `label_ocr.py` are gated behind that flag.

---

## 5. Versioning and legacy files

The repo uses:

- `changelog.md` – human-readable list of version changes.
- `Change_Version.md` – notes on how to bump versions or what changed between releases.
- `legacy_versions/` – house older GUI scripts:
  - `discogs_to_shopify_gui_original.py`
  - `discogs_to_shopify_gui_patched1.py`
  - `discogs_to_shopify_gui_v1_2_0.py`
  - `discogs_to_shopify_gui_v1_2_2.py`
  - `discogs_to_shopify_v1_1_1.py`
  - `discogs_to_shopify_V1.1.2.py`
- Current active GUI: `discogs_to_shopify_gui.py`.

Guidelines:

- Do not delete `legacy_versions/` lightly; they serve as a historical backup.
- When making significant changes:
  - Update `changelog.md`.
  - Consider copying the old GUI into `legacy_versions/` with a new versioned filename.
  - Update this appendix and the master overview if behavior changes in a user-visible way.

---

## 6. Roadmap / future work

1. **Fix OCR flag behavior**
   - Ensure GUI and engine honor the “Use OCR” setting.
   - When OCR is off, avoid calling `label_ocr.py` entirely.

2. **Stabilize catalog number extraction**
   - Improve preprocessing in `label_ocr.py`.
   - Add clearer logging for:
     - `image_path`
     - raw OCR text
     - parsed catalog number(s)
   - Consider manual override in the GUI for failed rows.

3. **Integrate eBay pricing**
   - Wire `pricing.py` into the main pipeline.
   - Decide on:
     - Sold listings window.
     - Shipping assumptions.
     - Condition matching logic.
     - Markup/markdown strategy vs eBay median.
   - Add:
     - “Pricing Strategy Key” column.
     - “Pricing Notes” column.
     - Optional metafields for pricing strategy.

4. **Replace image watermarking with metafield**
   - Remove all active calls to `image_watermark.py`.
   - Add a boolean metafield `uses_stock_photo` and set it when the primary image is from Discogs rather than a custom photo.
   - Optionally reflect this in the Shopify front-end (badge, label, or styling).

5. **Improve configuration management**
   - Consolidate configuration into:
     - `.env` for secrets (API keys).
     - `discogs_to_shopify_settings.json` for user preferences.
   - Document configuration fields clearly.

6. **Documentation updates**
   - Keep `Album_Listing_App_Master_Overview.md` short and current.
   - Use this appendix for:
     - Detailed notes.
     - Pricing/OCR experiments.
     - Edge case handling decisions.

---

## 7. How to use this appendix

- Treat this file as the **deep technical reference** for the Album Listing App.
- When you change:
  - Discogs search strategy.
  - OCR behavior.
  - Pricing logic.
  - Shopify mapping or metafields.
- …update this appendix with:
  - What changed.
  - Why you changed it.
  - Any migration considerations.

This ensures that future you—or anyone else working on the repo—can quickly understand the full history and reasoning behind the current behavior of the app.


## 8. OCR: Runout / Matrix MVP (spec)

Goal: capture runout/matrix strings when a dedicated runout photo is provided, while keeping label OCR unchanged.

Scope (MVP):
- Input: dedicated runout close-up photo (user-provided). Label OCR remains as-is.
- Preprocess: grayscale, high-contrast threshold, denoise; run rotations (0/90/180/270); optional strip-based crops (top/bottom/left/right slices).
- OCR: Tesseract with tight charset (`A-Z0-9-./+`) and `--psm 6/7`; run across rotations/crops and merge candidates.
- Post-process: normalize (strip spaces/double-dashes), score candidates by length (target 6?20 chars), presence of side markers (A/B/1/2), and uniqueness; deduplicate and keep top 1?2 candidates.
- Output: store candidates on the row (e.g., `Runout_A`, `Runout_B`, `Runout_Raw`) and log them for review; do not auto-use in search yet.
- UX: add a runout image slot in the GUI; note that runout images should be close-ups; future option for manual crop and user selection of the best candidate.

Future (beyond MVP):
- Curve/arc handling: remap the runout arc to a straight line before OCR.
- Condition-aware hints: detect etched vs stamped (contrast/relief cues) to adjust preprocessing.
- Use runout in search: feed selected runout candidate into Discogs search (optional toggle).
