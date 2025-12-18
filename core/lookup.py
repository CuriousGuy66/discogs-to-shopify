from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from core.clients.discogs import DiscogsClient
from core.clients.musicbrainz import MusicBrainzClient
from core.models import RecordInput, ReleaseMatch

logger = logging.getLogger(__name__)


def _pick_musicbrainz_match(results: list[dict], record: RecordInput) -> Optional[ReleaseMatch]:
    """Select the best MusicBrainz match, preferring barcode or catalog matches when present."""
    if not results:
        return None

    want_vinyl = bool(record.format_hint and "vinyl" in record.format_hint.lower())

    # Prefer barcode match
    if record.barcode:
        for r in results:
            if r.get("barcode") == record.barcode:
                return _as_mb_match(r)

    # Prefer catalog number match when available in label-info list
    if record.catalog:
        for r in results:
            labels = r.get("label-info-list") or r.get("label-info") or []
            for lbl in labels:
                catno = (
                    lbl.get("catalog-number")
                    or (lbl.get("label") or {}).get("catalog-number")
                )
                if catno and catno == record.catalog:
                    return _as_mb_match(r)

    # Prefer label match when provided
    if record.label:
        want = record.label.strip().upper()
        for r in results:
            labels = r.get("label-info-list") or r.get("label-info") or []
            for lbl in labels:
                name = (lbl.get("label") or {}).get("name")
                if name and name.strip().upper() == want:
                    return _as_mb_match(r)

    # Prefer vinyl medium when hinted
    if want_vinyl:
        for r in results:
            media = r.get("media") or r.get("medium-list") or []
            if isinstance(media, list):
                for m in media:
                    fmt = (m.get("format") or "").lower()
                    if "vinyl" in fmt:
                        return _as_mb_match(r)
            elif isinstance(media, dict):
                fmt = (media.get("format") or "").lower()
                if "vinyl" in fmt:
                    return _as_mb_match(r)

    # Prefer country match
    if record.country:
        want_country = record.country.strip().upper()
        for r in results:
            if str(r.get("country") or "").strip().upper() == want_country:
                return _as_mb_match(r)

    # Prefer year match
    if record.year:
        want_year = str(record.year)
        for r in results:
            date_val = str(r.get("date") or "")
            if date_val.startswith(want_year):
                return _as_mb_match(r)

    # Fallback to first result
    return _as_mb_match(results[0])


def _as_mb_match(r: dict) -> ReleaseMatch:
    release_id = r.get("id") or ""
    title = r.get("title") or ""
    year = None
    if r.get("date"):
        year = str(r["date"]).split("-")[0]

    artist = ""
    credits = r.get("artist-credit") or []
    if credits:
        artist = credits[0].get("name") or credits[0].get("artist", {}).get("name", "")

    return ReleaseMatch(
        source="musicbrainz",
        release_id=str(release_id),
        title=title,
        artist=artist,
        year=year,
        url=f"https://musicbrainz.org/release/{release_id}" if release_id else None,
        discogs_release_id=None,
        discogs_url=None,
        raw=r,
    )


def _as_discogs_match(res) -> ReleaseMatch:
    # res is DiscogsResult dataclass
    res_dict = asdict(res)
    return ReleaseMatch(
        source="discogs",
        release_id=str(res.release_id),
        title=res.title,
        artist=res.artist,
        year=res.year,
        url=f"https://www.discogs.com/release/{res.release_id}" if res.release_id else None,
        discogs_release_id=str(res.release_id),
        discogs_url=f"https://www.discogs.com/release/{res.release_id}" if res.release_id else None,
        raw=res_dict,
    )


def _extract_discogs_release_relation(relations: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """
    Parse MusicBrainz url-rels to find a Discogs release link/ID.
    Returns (release_id, url).
    """
    for rel in relations or []:
        url = (rel.get("url") or {}).get("resource") or ""
        if not url:
            continue
        lower = url.lower()
        if "discogs.com/release/" in lower:
            parts = url.rstrip("/").split("/")
            rel_id = parts[-1] if parts else None
            if rel_id:
                return rel_id, url
        if "discogs.com/master/" in lower:
            # Master link is still useful context even without a specific release ID.
            return None, url
    return None, None


def _enrich_mb_match_with_discogs(
    match: ReleaseMatch,
    mb_client: MusicBrainzClient,
    discogs_client: Optional[DiscogsClient] = None,
) -> ReleaseMatch:
    """
    Look up url-rels on the matched MusicBrainz release to capture a Discogs
    release link/ID. This avoids a Discogs search when pricing needs Discogs data.
    """
    try:
        data = mb_client.lookup_release(match.release_id, include=["url-rels"])
    except Exception as exc:  # pragma: no cover - network/HTTP dependent
        logger.debug("MusicBrainz lookup for url-rels failed: %s", exc)
        return match

    discogs_rel_id, discogs_url = _extract_discogs_release_relation(data.get("relations") or [])
    if discogs_rel_id:
        match.discogs_release_id = discogs_rel_id
        match.discogs_url = discogs_url or f"https://www.discogs.com/release/{discogs_rel_id}"
        logger.info(
            "MusicBrainz match has Discogs release relation: id=%s url=%s",
            discogs_rel_id,
            match.discogs_url,
        )
        if discogs_client:
            try:
                match.discogs_marketplace_stats = discogs_client.get_marketplace_stats(int(discogs_rel_id))
            except Exception as exc:  # pragma: no cover - network/HTTP dependent
                logger.debug("Discogs marketplace stats fetch failed for %s: %s", discogs_rel_id, exc)
            try:
                match.discogs_price_suggestions = discogs_client.get_price_suggestions(int(discogs_rel_id))
            except Exception as exc:  # pragma: no cover - network/HTTP dependent
                logger.debug("Discogs price suggestions fetch failed for %s: %s", discogs_rel_id, exc)
    elif discogs_url:
        match.discogs_url = discogs_url
    return match


def find_release_with_fallback(
    record: RecordInput,
    mb_client: MusicBrainzClient,
    discogs_client: DiscogsClient,
) -> Optional[ReleaseMatch]:
    """
    Try MusicBrainz first; if no match, fall back to Discogs.
    """
    mb_results = mb_client.search_release(
        artist=record.artist,
        title=record.title,
        catno=record.catalog,
        barcode=record.barcode,
        label=record.label,
        country=record.country,
        year=record.year,
        format_hint=record.format_hint,
        limit=5,
    )
    mb_match = _pick_musicbrainz_match(mb_results, record)
    if mb_match:
        mb_match = _enrich_mb_match_with_discogs(mb_match, mb_client, discogs_client)
        return mb_match

    discogs_match = discogs_client.search(record)
    if discogs_match:
        return _as_discogs_match(discogs_match)

    return None
