# Discogs to Shopify – Changelog

All notable changes to this project will be documented in this file.

The format is:

- Version
- Date (2025-11-23)
- Summary of changes

---

## v1.0.0 – 2025-11-23

- Created the first tracked version of `discogs_to_shopify.py`.
- Implemented Discogs → Shopify pipeline (no GUI).
- Input:
  - Reads CSV/XLSX.
  - Uses the following key columns:
    - `Artist`
    - `Title`
    - `Reference Price`
    - `Country`
    - `Catalog`
    - `Center label photo`
    - `Media Condition`
    - `Sleeve Condition`
- Discogs:
  - Searches Discogs releases using artist/title (+ optional catalog/country).
  - Automatically chooses the top search result.
  - Fetches full release details (label, year, genres, styles, formats,
    images, tracklist, etc.).
- Pricing:
  - Reads `Reference Price`.
  - Strips currency symbols and junk (e.g. `$`, `USD`, commas).
  - Rounds to the nearest $0.25.
  - Enforces minimum price of $2.50.
- Artist normalization:
  - If artist starts with `"The "`, moves it to the end:
    - `"The Beatles"` → `"Beatles, The"`.
- Weight:
  - Estimates weight in grams from the number of discs:
    - 1 disc → 300 g
    - 2 discs → 500 g
    - 3 discs → 700 g
    - 4+ discs → 300 g + 200 g per extra disc
  - Converts grams to pounds for a helper column.
- Shopify CSV mapping:
  - Fields aligned with `product_template_csv_unit_price.csv`, including:
    - `Title`
    - `URL handle`
    - `Description`
    - `Vendor`
    - `Product category`
    - `Type`
    - `Tags`
    - `Published on online store`
    - `Status`
    - `Option1 name` / `Option1 value`
    - `Price`
    - `Compare-at price`
    - `Cost per item`
    - `Charge tax`
    - `SKU`
    - `Barcode`
    - `Inventory tracker`
    - `Inventory quantity`
    - `Continue selling when out of stock` (FALSE)
    - `Weight value (grams)`
    - `Weight unit for display` (g)
    - `Requires shipping`
    - `Fulfillment service`
    - `Product image URL`
    - `Image position`
    - `Image alt text`
    - `Variant image URL`
    - `Gift card`
    - `SEO title`
    - `SEO description`
- Images:
  - First image: Discogs cover image (`Image position` = 1).
  - Second image: `Center label photo` from the input sheet
    - Creates a second row with the same `URL handle` and `Title`.
    - Sets `Product image URL` to the center label photo.
    - Sets `Image position` = 2 and `Image alt text` accordingly.
- Shop Signage:
  - Uses Discogs `genre` + `styles` to assign signage category with this
    priority:
    1. Stage and Sound (also matches Soundtrack in genre/styles)
    2. Christmas
    3. Gospel
    4. Religious
    5. Bluegrass
    6. Country
    7. Metal
    8. Reggae
    9. Latin
    10. Folk
    11. Children's
    12. Comedy
    13. New Age
    14. Spoken Word
    15. Rock
    16. Jazz
    17. Blues
    18. Soul/Funk
    19. Classical
    20. Electronic
    21. Hip-Hop/Rap
    22. Default: raw Discogs genre
- Metafields:
  - Writes the following product metafields:
    - `Metafield: custom.album_cover_condition [single_line_text_field]`
    - `Metafield: custom.album_condition [single_line_text_field]` = `"Used"`
    - `Metafield: custom.shop_signage [single_line_text_field]` = signage bucket.
