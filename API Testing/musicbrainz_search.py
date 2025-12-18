#!/usr/bin/env python3
"""
Simple MusicBrainz search test.

Usage:
    python musicbrainz_search.py "Artist Name" "Release Title" [label] [country] [year] [limit]
"""

from __future__ import annotations

import sys
from typing import List
from pathlib import Path
import sys

# Ensure repository root is on sys.path for core imports when running from this subfolder.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.clients.discogs import DiscogsClient
from core.clients.musicbrainz import MusicBrainzClient
from core.lookup import find_release_with_fallback
from core.models import RecordInput


def main(args: List[str]) -> int:
    artist = args[0] if len(args) > 0 else ""
    title = args[1] if len(args) > 1 else ""
    label = args[2] if len(args) > 2 and args[2] else None
    country = args[3] if len(args) > 3 and args[3] else None
    year = None
    if len(args) > 4 and args[4]:
        try:
            year = int(args[4])
        except Exception:
            year = None
    limit_str = args[5] if len(args) > 5 else "3"

    try:
        limit = max(1, min(int(limit_str), 25))
    except Exception:
        limit = 3

    mb = MusicBrainzClient(
        user_agent="discogs-to-shopify/1.0 (contact: neal@unusualfinds.net)",
        prefer_ipv4=True,
    )
    discogs = DiscogsClient()

    print(f"Searching releases for artist={artist!r} title={title!r} limit={limit}")
    record = RecordInput(
        artist=artist,
        title=title,
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
