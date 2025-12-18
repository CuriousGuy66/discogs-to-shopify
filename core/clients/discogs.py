from __future__ import annotations

import os
from typing import List, Optional

import discogs_client as legacy_discogs

from core.models import DiscogsResult, RecordInput


class DiscogsClient:
    """Wrapper for Discogs API interactions (search and release detail)."""

    def __init__(self, token: Optional[str] = None) -> None:
        # Uses DISCOGS_TOKEN if not explicitly provided.
        self.token = (token or os.getenv("DISCOGS_TOKEN", "")).strip()

    def _tracklist_to_html(self, tracklist: List[dict]) -> str:
        """Render a simple HTML list from Discogs tracklist entries."""
        if not tracklist:
            return ""
        lines: List[str] = []
        for t in tracklist:
            pos = t.get("position") or ""
            title = t.get("title") or ""
            duration = t.get("duration") or ""
            suffix = f" ({duration})" if duration else ""
            if pos:
                lines.append(f"{pos}. {title}{suffix}")
            else:
                lines.append(f"{title}{suffix}")
        return "<br>".join(lines)

    def _labels_to_name(self, labels: List[dict]) -> str:
        for lbl in labels or []:
            name = lbl.get("name")
            if name:
                return name
        return ""

    def _formats_to_names(self, formats: List[dict]) -> List[str]:
        names: List[str] = []
        for fmt in formats or []:
            if fmt.get("name"):
                names.append(fmt["name"])
            for desc in fmt.get("descriptions") or []:
                names.append(desc)
        return names

    def search(self, record: RecordInput) -> Optional[DiscogsResult]:
        """
        Return the best match for the given record, or None.
        Uses legacy discogs_client wrapper for retry/throttle behavior.
        """
        search_obj = legacy_discogs.search_release(
            token=self.token,
            artist=record.artist,
            title=record.title,
            catalog=record.catalog,
        )
        if not search_obj:
            return None

        release_id = search_obj.get("id")
        details = legacy_discogs.get_release_details(self.token, release_id) if release_id else None

        title = (details or {}).get("title") or search_obj.get("title") or ""
        artist = ""
        if details and details.get("artists"):
            artist = details["artists"][0].get("name", "") or ""
        else:
            artist = search_obj.get("artist") or search_obj.get("label", [""])[0] if search_obj.get("label") else ""

        label = self._labels_to_name((details or {}).get("labels") or search_obj.get("label") or [])
        year = (details or {}).get("year") or search_obj.get("year")
        genres = (details or {}).get("genres") or search_obj.get("genre") or []
        styles = (details or {}).get("styles") or search_obj.get("style") or []
        formats = self._formats_to_names((details or {}).get("formats") or [])
        images = [img.get("uri") for img in (details or {}).get("images") or [] if img.get("uri")]
        if not images and search_obj.get("cover_image"):
            images = [search_obj["cover_image"]]
        tracklist_html = self._tracklist_to_html((details or {}).get("tracklist") or [])

        return DiscogsResult(
            release_id=int(release_id) if release_id is not None else -1,
            title=title,
            artist=artist,
            label=label,
            year=str(year) if year is not None else None,
            genres=list(genres),
            styles=list(styles),
            formats=list(formats),
            images=list(images),
            tracklist_html=tracklist_html,
        )

    def get_release(self, release_id: int) -> DiscogsResult:
        """Fetch detailed release data by ID."""
        details = legacy_discogs.get_release_details(self.token, release_id)
        if not details:
            raise RuntimeError(f"Discogs release {release_id} not found or failed to fetch.")

        title = details.get("title") or ""
        artist = ""
        if details.get("artists"):
            artist = details["artists"][0].get("name", "") or ""

        label = self._labels_to_name(details.get("labels") or [])
        year = details.get("year")
        genres = details.get("genres") or []
        styles = details.get("styles") or []
        formats = self._formats_to_names(details.get("formats") or [])
        images = [img.get("uri") for img in details.get("images") or [] if img.get("uri")]
        tracklist_html = self._tracklist_to_html(details.get("tracklist") or [])

        return DiscogsResult(
            release_id=release_id,
            title=title,
            artist=artist,
            label=label,
            year=str(year) if year is not None else None,
            genres=list(genres),
            styles=list(styles),
            formats=list(formats),
            images=list(images),
            tracklist_html=tracklist_html,
        )

    def get_marketplace_stats(self, release_id: int):
        """Fetch Discogs marketplace stats for a release ID."""
        return legacy_discogs.get_marketplace_stats(self.token, release_id)

    def get_price_suggestions(self, release_id: int):
        """Fetch Discogs price suggestions for a release ID."""
        return legacy_discogs.get_price_suggestions(self.token, release_id)
