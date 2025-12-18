#!/usr/bin/env python3
"""
Ad-hoc harness to test MusicBrainz->Discogs fallback matching.

Usage:
    python lookup_fallback_harness.py "Artist" "Title" [catalog] [barcode] [label] [country] [year]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Ensure repository root is on sys.path for core imports when running from this subfolder.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.clients.discogs import DiscogsClient
from core.clients.musicbrainz import MusicBrainzClient
from core.lookup import find_release_with_fallback
from core.models import RecordInput
from uf_logging import setup_logging


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "Usage: python lookup_fallback_harness.py "
            "\"Artist\" \"Title\" [catalog] [barcode] [label] [country] [year]"
        )
        return 1

    artist = argv[0]
    title = argv[1]
    catalog: Optional[str] = argv[2] if len(argv) > 2 and argv[2] else None
    barcode: Optional[str] = argv[3] if len(argv) > 3 and argv[3] else None
    label: Optional[str] = argv[4] if len(argv) > 4 and argv[4] else None
    country: Optional[str] = argv[5] if len(argv) > 5 and argv[5] else None
    year: Optional[int] = None
    if len(argv) > 6 and argv[6]:
        try:
            year = int(argv[6])
        except Exception:
            year = None

    log_file = setup_logging()
    print(f"Logging to: {log_file}")

    mb = MusicBrainzClient(
        user_agent="discogs-to-shopify/1.0 (contact: neal@unusualfinds.net)",
        prefer_ipv4=True,
    )
    discogs = DiscogsClient()

    record = RecordInput(
        artist=artist,
        title=title,
        catalog=catalog,
        barcode=barcode,
        label=label,
        country=country,
        year=year,
    )
    match = find_release_with_fallback(record, mb, discogs)

    if not match:
        print("No matches found in MusicBrainz or Discogs.")
        return 0

    print(f"Matched via {match.source}:")
    print(f" - release_id: {match.release_id}")
    print(f" - title: {match.title}")
    print(f" - artist: {match.artist}")
    print(f" - year: {match.year}")
    print(f" - url: {match.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
