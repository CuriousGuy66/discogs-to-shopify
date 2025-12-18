from __future__ import annotations

import socket
import time
from typing import Any, Dict, List, Optional

import requests
import urllib3


DEFAULT_USER_AGENT = "discogs-to-shopify/1.0 (contact: neal@unusualfinds.net)"
BASE_URL = "https://musicbrainz.org/ws/2"


class MusicBrainzClient:
    """
    Minimal MusicBrainz client for release search/lookup.

    Notes:
    - MusicBrainz asks clients to throttle to ~1 req/sec and to send a custom User-Agent.
    - Responses are JSON when `fmt=json` is provided.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
        calls_per_second: float = 1.0,
        prefer_ipv4: bool = False,
    ) -> None:
        # Some networks have broken IPv6 TLS paths to musicbrainz.org; allow opting into IPv4-only.
        if prefer_ipv4:
            urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET  # type: ignore[attr-defined]

        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.min_interval = 1.0 / max(0.1, calls_per_second)
        self._last_call_ts = 0.0

    def _sleep_for_rate_limit(self) -> None:
        now = time.time()
        elapsed = now - self._last_call_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_ts = time.time()

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._sleep_for_rate_limit()
        url = f"{BASE_URL}/{path.lstrip('/')}"
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def search_release(
        self,
        artist: str,
        title: str,
        catno: Optional[str] = None,
        barcode: Optional[str] = None,
        label: Optional[str] = None,
        country: Optional[str] = None,
        year: Optional[int] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Search releases by artist/title with optional catalog number/barcode.
        Returns a list of release dicts.
        """
        query_parts = [
            f'artist:"{artist}"' if artist else "",
            f'release:"{title}"' if title else "",
        ]
        if catno:
            query_parts.append(f'catno:"{catno}"')
        if barcode:
            query_parts.append(f'barcode:{barcode}')
        if label:
            query_parts.append(f'label:"{label}"')
        if country:
            query_parts.append(f'country:{country.strip()}')
        if year:
            query_parts.append(f'date:{year}')
        query = " AND ".join([p for p in query_parts if p])
        data = self._get(
            "release/",
            {
                "query": query,
                "fmt": "json",
                "limit": max(1, min(limit, 25)),
            },
        )
        return data.get("releases", []) or []

    def lookup_release(
        self, mbid: str, include: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Lookup a release by MBID, optionally including extra entities.
        Common include values: recordings, artists, labels, url-rels.
        """
        inc = "+".join(include) if include else ""
        params: Dict[str, Any] = {"fmt": "json"}
        if inc:
            params["inc"] = inc
        return self._get(f"release/{mbid}", params)

    def cover_art_url(self, mbid: str, size: Optional[str] = None) -> str:
        """
        Return a Cover Art Archive URL for the front image.
        size: None for full, or '250', '500' for scaled versions when available.
        """
        if size in ("250", "500"):
            return f"https://coverartarchive.org/release/{mbid}/front-{size}"
        return f"https://coverartarchive.org/release/{mbid}/front"

    def releases_for_group(self, release_group_id: str, limit: int = 25) -> List[Dict[str, Any]]:
        """
        Fetch releases under a release group (RGID) and return the releases list.
        """
        data = self._get(
            f"release-group/{release_group_id}",
            {
                "fmt": "json",
                "inc": "releases",
            },
        )
        releases = data.get("releases") or []
        if limit and len(releases) > limit:
            return releases[:limit]
        return releases
