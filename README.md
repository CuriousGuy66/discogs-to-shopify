# discogs-to-shopify

Automated pipeline to convert a spreadsheet of vinyl records into a **Shopify-ready Products CSV**, enriched with data from the **Discogs API**.

The script:

- Looks up each record on Discogs (release search)
- Pulls label, year, genres, styles, formats, images, and tracklist
- Builds a Shopify import CSV (including SEO, metafields, signage, and images)
- Writes unmatched items to a separate CSV for review

Current main script: **`discogs_to_shopify_v1_1_1.py`**

---

## Features

- 🔍 **Discogs integration**
  - Uses Discogs API `database/search` (type=release) and release detail endpoints
  - Auto-picks the **top search result** (preference 1A + 2A)
- 🎨 **Artist normalization**
  - Leading `"The "` is moved to the end:
    - `The Beatles` → `Beatles, The`
- 💰 **Price logic**
  - Reads **`Reference Price`** from the input sheet
  - Cleans values like `$5.00`, `5,000.00`, `5 USD`, etc.
  - Rounds to the **nearest $0.25**
  - Enforces a **minimum price of $2.50**
- ⚖️ **Weight estimation**
  - Uses Discogs `formats[].qty` to estimate disc count
  - Weight (grams):
    - 1 disc → 300 g  
    - 2 discs → 500 g  
    - 3 discs → 700 g  
    - 4+ discs → 300 g + 200 g per extra disc  
  - Converts to **pounds** in a helper column
- 🖼️ **Images**
  - Primary image: Discogs cover image
  - Secondary image (if available): `Center label photo` from the input sheet
    - Implemented as a second CSV row with the same handle and `Image position = 2`
- 🏷️ **Shopify mapping**
  - Output CSV matches Shopify’s product template structure
  - Uses proper product category and type:
    - `Product category`: `Media > Music & Sound Recordings > Records & LPs`
    - `Type`: `Vinyl Record`
  - `Vendor` = **record label** (from Discogs)
  - New column `Collection` = **`Vinyl Albums`**
- 🧾 **Description & footer**
  - HTML description includes:
    - Artist, album, label, year, format, genre, conditions, Discogs link
    - Discogs tracklist rendered as HTML
  - Appends a standard footer:

    > All albums are stored in heavy-duty protective sleeves to help preserve their condition. The first image shown is a stock photo for reference.  
    >  
    > Please note that every record we sell goes through a careful process that includes inspection, research, detailed listing, and photography. Our prices may not always be the lowest, but we take pride in accurately representing each album and providing thorough information so you can buy with confidence.

- 📚 **Metafields**
  - `Metafield: custom.album_cover_condition [single_line_text_field]` = Sleeve Condition
  - `Metafield: custom.album_condition [single_line_text_field]` = `Used`
  - `Metafield: custom.shop_signage [single_line_text_field]` = signage bucket (see below)
- 🪧 **Shop signage categories**
  - Derived from Discogs `genre` + `styles`
  - Priority order:
    1. Stage and Sound (includes Soundtrack)
    2. Christmas (Holiday, Xmas)
    3. Gospel
    4. Religious
    5. Bluegrass
    6. Country
    7. Metal
    8. Reggae
    9. Latin
    10. Folk
    11. Pop
    12. Disco
    13. Children’s
    14. Comedy
    15. New Age
    16. Spoken Word
    17. Rock
    18. Jazz
    19. Blues
    20. Soul/Funk
    21. Classical
    22. Electronic
    23. Hip-Hop/Rap
    24. Default: raw Discogs genre
- 🚫 **Unmatched tracking**
  - If a row cannot be matched or fetched from Discogs, it is:
    - Skipped from the main Shopify CSV
    - Written to a separate **unmatched CSV** with:
      - `Unmatched_Reason`
      - `Discogs_Query` (`artist=… | title=… | catalog=… | country=…`)

---

## Requirements

- **Python:** 3.11+ recommended
- **Dependencies:**
  - `pandas`
  - `requests`
  - `python-slugify`
  - `openpyxl` (for Excel input)

Install dependencies (local dev):

```bash
pip install pandas requests python-slugify openpyxl
