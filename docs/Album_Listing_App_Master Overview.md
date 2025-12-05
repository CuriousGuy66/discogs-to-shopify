# Album Listing App – Master Overview

_Last updated: (fill in date when you paste this)_

## 1. What the app does (high level)

The **Album Listing App** is a desktop tool (Python + Tkinter GUI) that:

1. Takes an **input spreadsheet** of vinyl inventory (artist, title, label, year, condition, price, etc.).
2. For each row, searches **Discogs** for the best matching **Release**.
3. Optionally uses **OCR on center-label photos** to help identify catalog numbers.
4. Merges spreadsheet + Discogs + OCR + pricing into a single, enriched record.
5. Outputs a **Shopify-ready CSV** for import as products to the Unusual Finds online store.

The app is designed to be **repeatable, configurable, and versioned**, so the logic can evolve without constantly rewriting the core.

---

## 2. Current status snapshot

- **Primary entry point:** `discogs_to_shopify_gui.py` (Tkinter GUI).
- **Core engine:** `discogs_to_shopify.py`.
- **Discogs API client:** `discogs_client.py`.
- **OCR:** `label_ocr.py` is **in use**. There is a flag intended to skip OCR, but currently the flag logic is not fully respected, so OCR still runs – this is a known issue.
- **Pricing:** `pricing.py` and `ebay_search.py` exist, but **eBay-based dynamic pricing is not currently active** in the live pipeline.
- **Image watermarking:** `image_watermark.py` exists but is treated as **legacy**; the current design is moving toward a `uses_stock_photo` metafield instead of watermarking Discogs images.
- **Logging:** Shared logging utilities in `uf_logging.py`.
- **Configuration:** `discogs_to_shopify_settings.json` stores runtime settings (paths, tokens, options).
- **Legacy GUI versions:** stored under `legacy_versions/` for reference.

### What's new in v1.3.0

- Default base folder under Documents/UnusualFindsAlbumApp with fixed `input/`, `output/`, `logs/`, `cache/`, processed input moves to `input/processed/`, and a settings gear to change the base. Outputs are timestamped; quick-open buttons for input/output/logs.
- Added product metafield `product.metafields.custom.inventory_date` (YYYY-MM-DD).
- Label OCR now grabs a matrix/runout token as a Discogs search hint; matrix is logged/exported.
- Discogs pricing suggestions wired as a condition-based fallback (`DSUG`); reference price can override lower computed prices.
- Discogs calls more resilient: longer timeouts/backoff, small per-call delays, and a second retry pass for release details to reduce skips under throttle/latency.

---

## 3. High-level pipeline

1. **User launches GUI** via `discogs_to_shopify_gui.py`.
2. GUI lets user choose:
   - Input spreadsheet (XLSX/CSV) from `input/` or any path.
   - Output Shopify CSV path.
   - Optional settings (e.g., whether to run OCR, Discogs search options, etc.—some not fully wired yet).
3. GUI calls **`discogs_to_shopify.py`** with those parameters.
4. `discogs_to_shopify.py`:
   - Loads the input rows.
   - For each row:
     1. Normalizes artist names (e.g., `The Beatles` → `Beatles, The`).
     2. Builds and sends a **Discogs search** using `discogs_client.py`.
     3. Optionally runs **OCR** via `label_ocr.py` on center-label photos to extract catalog numbers/other hints.
     4. Chooses the best matching Discogs release using scoring rules (title, label, year, country, format).
     5. Pulls Discogs metadata (catalog number, release year, label, genre, barcode, tracklist HTML, images, etc.).
     6. Applies **pricing logic** (currently simple spreadsheet-based rules; eBay pricing is not yet integrated).
     7. Builds a single **Shopify product row** (one variant per album).
   - Writes all rows to the **Shopify CSV output**.

---

## 4. Key files and what they do

**Top-level Python files:**

- `discogs_to_shopify_gui.py`  
  - Tkinter GUI front-end.
  - Lets the user choose input/output paths and run the pipeline.
  - Calls `discogs_to_shopify.py` with arguments (and may read/write `discogs_to_shopify_settings.json`).

- `discogs_to_shopify.py`  
  - Core engine for reading the inventory spreadsheet, calling Discogs, OCR, pricing, and writing the Shopify CSV.

- `discogs_client.py`  
  - Handles low-level communication with the Discogs API.
  - Builds search queries and release detail requests.
  - Likely uses environment variables or settings for Discogs tokens.

- `label_ocr.py`  
  - Runs Tesseract OCR on center-label images.
  - Intended to provide catalog numbers and track hints for better Discogs matching.
  - Currently **active**, and the flag meant to disable it is not fully honored.

- `pricing.py`  
  - Contains pricing logic hooks.
  - Planned to include/extend eBay sold-listing–based pricing.
  - At the moment, **eBay pricing is not wired into the main workflow**, so pricing defaults to spreadsheet or simple rules.

- `ebay_search.py`  
  - Implementation for calling eBay (API or other method).
  - Present but not yet integrated into the active pricing pipeline.

- `image_watermark.py`  
  - Previously used to watermark Discogs stock photos.
  - The current design is moving away from watermarking toward a boolean metafield like `uses_stock_photo`.
  - Treat this as **legacy**; main script should no longer call it.

- `uf_logging.py`  
  - Shared logging configuration.
  - Provides consistent log formatting and file/console handling across scripts.

**Other important files:**

- `discogs_to_shopify_settings.json` – stores user/config settings (paths, default options, possibly tokens).
- `requirements.txt` – Python dependencies (Discogs client, Tkinter, Tesseract bindings, etc.).
- `README.md` – basic project overview (to be kept in sync with this master overview).
- `Change_Version.md` – describes version-change steps or processes.
- `changelog.md` – version history log of changes.
- `unusual_finds_icon_new.ico` – app icon for GUI/EXE builds.
- `legacy_versions/` – archived versions of GUI scripts (e.g. `discogs_to_shopify_gui_v1_2_2.py`), kept for fallback/reference.

**Folders:**

- `input/` – default location for input spreadsheets and possibly images.
- `cloudflare-worker/` – code related to Cloudflare integration (separate concern from the GUI app).
- `.github/` – GitHub Actions / CI config (e.g. EXE builds, deployments).
- `.vscode/` – workspace settings for VS Code.

---

## 5. Data and business rules (high-level)

### 5.1 Artist name transformation

- If an artist name starts with **“The ”**, the app transforms it to:  
  - `The Beatles` → `Beatles, The`  
  - This rule is applied globally wherever artist names are normalized for Discogs search and Shopify title/handle generation.

### 5.2 Discogs matching preferences

- Use **Discogs Release search** (not Master, unless explicitly changed).
- Prefer:
  - Matching **country** to the spreadsheet’s country when available.
  - Matching **format** (LP vs Compilation, Stereo vs Mono, etc.).
  - Matching **label** and **year** when possible.
- Use OCR-extracted **catalog numbers** as an additional hint when available.

### 5.3 Shopify CSV mapping (summary)

- Product Type: `Vinyl Record`
- Product Category: `Media > Music > Vinyl Record`
- Variant:
  - Single variant per product (`Option1 Name = "Title"`, `Option1 Value = "Default Title"`).
  - `Variant Inventory Quantity = 1`
  - `Variant Inventory Policy = deny`
  - `Variant Requires Shipping = TRUE`
  - `Variant Taxable = TRUE`
  - `Variant Fulfillment Service = manual`
- Pricing:
  - Derived primarily from the spreadsheet (with rounding and floors), eBay pricing planned but not yet active.
- Handles:
  - Generated uniquely from artist, title, and year.
  - Ensured to be unique even for identical albums added later.
- Metafields:
  - `Album_Cover_Condition` ← Sleeve Condition from original spreadsheet.
  - `Album_Condition` ← `'Used'`.
- Publishing:
  - Published to Pinterest, Google & YouTube, Inbox, Facebook & Instagram, Shop, Point of Sale, Online Store.

---

## 6. Running the app (typical workflow)

1. Activate Python environment and install dependencies:

   ```bash
   pip install -r requirements.txt
