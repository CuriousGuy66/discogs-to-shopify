# Future Plan: Discogs to Shopify Project

## Overview
Roadmap to evolve from the current CSV/GUI app to a multi-channel, API-first, camera-driven workflow with optional database persistence.

## Phases
1) Direct Shopify API output  
   - Introduce a Shopify API exporter that takes normalized `ShopifyDraft` objects and creates products (Admin REST/GraphQL), handling rate limits, retries, and idempotency.  
   - Keep the CSV exporter for fallback.

2) Database layer (SQLite first, Postgres/MySQL later)  
   - Add persistence for jobs, products, unmatched rows, images, and logs.  
   - Use SQLite locally/desktop; make schema portable to Postgres/MySQL for server use.  
   - Track idempotency keys and sync status for future multi-marketplace pushes.

3) Mobile/Flutter camera app  
   - Expose the core pipeline via a small HTTP API (e.g., FastAPI) that accepts images + metadata and returns drafts/unmatched + logs.  
   - Flutter client captures photos (cover/label/runout) and calls the API.  
   - Keep user-writable data in `%APPDATA%`/`%LOCALAPPDATA%` (desktop) or service storage (server).

4) Runout/etching capture and OCR  
   - Add a pluggable `EtchingReader` to extract matrix/etching text from runout photos.  
   - Use the etching text as an additional Discogs search hint.  
   - Make it optional; fall back gracefully when OCR is unavailable.

## Architectural Notes
- Modularize: core processing + models + exporters + clients; UI/CLI/API are thin layers.  
- External clients: wrap Discogs/Shopify/eBay in dedicated client modules.  
- Persistence: optional layer receiving events (`job_started`, `product_created`, `unmatched_found`, `job_finished`).  
- Logging: structured logs and a `ProcessSummary` object for UI/API responses.  
- Security: store tokens in env/secrets; avoid embedding credentials in binaries or mobile apps.  
- Signing/packaging: continue `--noconsole` PyInstaller build; Inno Setup installer targeting Program Files; code-sign when available.

## Status / Next Actions
- Shopify API integration completed (draft create with images and payload logging).  
- Category/taxonomy still not persisted by Shopify despite sending `category` GID and standardized product type; support ticket needed and revisit once resolved.  
