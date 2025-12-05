#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discogs_client.py
===============================================================================
Thin, safe wrapper around the Discogs API with retry + basic throttling.

All network calls to Discogs should go through this module instead of calling
`requests` directly from the GUI or other modules.
===============================================================================
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests

from uf_logging import get_logger

logger = get_logger(__name__)

DISCOGS_API_BASE = "https://api.discogs.com"
USER_AGENT = "UnusualFindsDiscogsShopify/1.0 +https://unusualfinds.com"


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _build_headers(token: str) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
    }
    token = (token or "").strip()
    if token:
        headers["Authorization"] = f"Discogs token={token}"
    else:
        logger.warning("Discogs token is empty; unauthenticated requests may be rate-limited.")
    return headers


def _safe_get(
    path: str,
    token: str,
    params: Optional[Dict[str, Any]] = None,
    max_retries: int = 5,
    timeout: int = 40,
) -> Optional[requests.Response]:
    """
    Perform a GET with retry and simple backoff.
    Returns a Response on success, or None if all retries fail.
    """
    url = f"{DISCOGS_API_BASE}{path}"
    headers = _build_headers(token)
    params = params or {}

    backoff = 1.0
    base_delay = 0.5  # small delay between attempts to ease rate limits
    time.sleep(base_delay)

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.RequestException as e:
            logger.warning(
                "Discogs GET failed (attempt %d/%d) %s: %s",
                attempt,
                max_retries,
                url,
                e,
            )
            if attempt == max_retries:
                return None
            time.sleep(backoff)
            backoff = min(backoff * 2, 10.0)
            continue

        # Basic rate-limit handling
        remaining = resp.headers.get("X-Discogs-Ratelimit-Remaining")
        try:
            if remaining is not None and int(remaining) < 5:
                time.sleep(1.0)
        except ValueError:
            pass

        if resp.status_code == 429:
            # Rate limited — back off and retry
            logger.warning("Discogs rate limit hit on %s (attempt %d/%d)", url, attempt, max_retries)
            if attempt == max_retries:
                return None
            time.sleep(max(backoff, 3.0))
            backoff = min(backoff * 2, 10.0)
            continue

        if 500 <= resp.status_code < 600:
            # Transient server error
            logger.warning(
                "Discogs server error %s on %s (attempt %d/%d)",
                resp.status_code,
                url,
                attempt,
                max_retries,
            )
            if attempt == max_retries:
                return None
            time.sleep(backoff)
            backoff = min(backoff * 2, 10.0)
            continue

        # For all other statuses, return the response (caller can check .ok)
        return resp

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_release(
    token: str,
    artist: str,
    title: str,
    country: Optional[str] = None,
    catalog: Optional[str] = None,
    year: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Search Discogs for a release and return the FIRST result, or None.
    Mirrors the old GUI behavior but with retries.
    """
    query = f"{artist} {title}".strip()
    params: Dict[str, Any] = {
        "q": query,
        "type": "release",
        "per_page": 5,
        "page": 1,
    }
    if country:
        params["country"] = country
    if year:
        params["year"] = year
    if catalog:
        params["catno"] = catalog

    logger.info("Discogs search params: %s", params)

    resp = _safe_get("/database/search", token, params=params)
    if resp is None:
        logger.warning("Discogs search failed (no response) for query %r", query)
        return None

    if not resp.ok:
        logger.warning("Discogs search HTTP %s: %s", resp.status_code, resp.text)
        return None

    try:
        data = resp.json()
    except Exception as e:
        logger.warning("Discogs search JSON parse failed: %s", e)
        return None

    results = data.get("results") or []
    if not results:
        return None

    return results[0]


def get_release_details(token: str, release_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetch /releases/{id}.
    """
    resp = _safe_get(f"/releases/{release_id}", token)
    if resp is None or not resp.ok:
        logger.warning(
            "Discogs release fetch failed for %s (resp=%s)",
            release_id,
            getattr(resp, "status_code", None),
        )
        return None

    try:
        return resp.json()
    except Exception as e:
        logger.warning("Discogs release JSON parse failed for %s: %s", release_id, e)
        return None


def get_marketplace_stats(token: str, release_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetch /marketplace/stats/{release_id}.
    """
    resp = _safe_get(f"/marketplace/stats/{release_id}", token)
    if resp is None or not resp.ok:
        logger.warning(
            "Discogs marketplace stats fetch failed for %s (resp=%s)",
            release_id,
            getattr(resp, "status_code", None),
        )
        return None

    try:
        return resp.json()
    except Exception as e:
        logger.warning("Discogs stats JSON parse failed for %s: %s", release_id, e)
        return None


def get_price_suggestions(token: str, release_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetch /marketplace/price_suggestions/{release_id}.
    Returns a dict keyed by condition name with {"value": float, "currency": "..."}.
    """
    resp = _safe_get(f"/marketplace/price_suggestions/{release_id}", token)
    if resp is None or not resp.ok:
        logger.warning(
            "Discogs price suggestions fetch failed for %s (resp=%s)",
            release_id,
            getattr(resp, "status_code", None),
        )
        return None

    try:
        return resp.json()
    except Exception as e:
        logger.warning(
            "Discogs price suggestions JSON parse failed for %s: %s", release_id, e
        )
        return None
