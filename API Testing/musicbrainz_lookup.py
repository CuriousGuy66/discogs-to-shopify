#!/usr/bin/env python3
"""
Simple MusicBrainz lookup test.

Usage:
    python musicbrainz_lookup.py <mbid>
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

from core.clients.musicbrainz import MusicBrainzClient


def main(args: List[str]) -> int:
    if not args:
        print("Usage: python musicbrainz_lookup.py <mbid>")
        return 1
    mbid = args[0]
    mb = MusicBrainzClient(user_agent="discogs-to-shopify/1.0 (set-your-email@example.com)")

    print(f"Lookup release mbid={mbid}")
    data = mb.lookup_release(
        mbid,
        include=["media", "labels", "url-rels", "artist-credits", "recordings"],
    )
    print("Title:", data.get("title"))
    print("Labels:", [li.get("label", {}).get("name") for li in data.get("label-info", [])])
    print("Formats:", [m.get("format") for m in data.get("media", [])])
    print("Country:", data.get("country"))
    print("Date:", data.get("date"))
    discogs_rels = [
        rel
        for rel in data.get("relations", [])
        if rel.get("type", "").lower().startswith("discogs")
        or "discogs.com" in (rel.get("url", {}).get("resource", "") or "").lower()
    ]
    print("Discogs relations:", [rel.get("url", {}).get("resource") for rel in discogs_rels])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
