#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ebay_search.py
===============================================================================
Simplified eBay Browse API integration for ACTIVE listings only.

Notes:
- SOLD/Marketplace Insights requires special approval and is **disabled** here
  to avoid invalid_scope errors.
- We only request the browse.readonly scope.

Environment variables required:
    EBAY_CLIENT_ID
    EBAY_CLIENT_SECRET
===============================================================================
"""

import base64
import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests
import pricing  # for EbayListing dataclass


# ---------------------------------------------------------------------------
# Environment / constants
# ---------------------------------------------------------------------------

EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET")

EBAY_CATEGORY_VINYL = "176985"  # Vinyl Records
EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# in-memory token cache
_TOKEN: Optional[str] = None
_TOKEN_EXPIRY: float = 0.0


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _oauth_ready() -> bool:
    """Check that we have the needed env vars."""
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        logging.warning("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set; eBay disabled.")
        return False
    return True


def _get_token() -> Optional[str]:
    """
    Obtain OAuth token using client_credentials with ONLY browse.readonly scope.
    """
    global _TOKEN, _TOKEN_EXPIRY

    if not _oauth_ready():
        return None

    now = time.time()

    # Reuse valid token if not near expiry
    if _TOKEN and now < (_TOKEN_EXPIRY - 60):
        return _TOKEN

    auth = f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}"
    auth_header = base64.b64encode(auth.encode()).decode("ascii")

    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    # ONLY browse.readonly (avoids invalid_scope)
    scope = (
        "https://api.ebay.com/oauth/api_scope "
        "https://api.ebay.com/oauth/api_scope/buy.browse.readonly"
    )

    data = {
        "grant_type": "client_credentials",
        "scope": scope,
    }

    try:
        resp = requests.post(EBAY_OAUTH_URL, headers=headers, data=data, timeout=20)
    except Exception as e:
        logging.warning("eBay OAuth request failed: %s", e)
        return None

    if not resp.ok:
        logging.warning("OAuth token request failed: %s", resp.text)
        return None

    try:
        payload = resp.json()
    except ValueError:
        logging.warning("OAuth token response not JSON")
        return None

    token = payload.get("access_token")
    expires_in = payload.get("expires_in", 3600)

    if not token:
        logging.warning("OAuth token missing in response")
        return None

    _TOKEN = token
    _TOKEN_EXPIRY = now + float(expires_in)

    logging.info("Obtained new eBay OAuth token (expires_in=%s)", expires_in)
    return token


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _keywords(
    artist: Optional[str],
    title: Optional[str],
    year: Optional[str],
    label: Optional[str],
    catalog: Optional[str],
    fmt: Optional[str],
) -> str:
    parts: List[str] = []
    if artist:
        parts.append(artist)
    if title:
        parts.append(title)
    if catalog:
        parts.append(catalog)
    if label:
        parts.append(label)
    if year:
        parts.append(str(year))
    if fmt:
        parts.append(fmt)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# ACTIVE listings only
# ---------------------------------------------------------------------------

def _extract_active(json_data: Dict[str, Any]) -> List[pricing.EbayListing]:
    """Convert Browse API JSON into a list of EbayListing objects."""
    results: List[pricing.EbayListing] = []
    items = json_data.get("itemSummaries") or []

    for it in items:
        price_obj = it.get("price") or {}
        price = _safe_float(price_obj.get("value"))
        if price is None:
            continue

        shipping = 0.0
        ship_opt = it.get("shippingOptions") or []
        if ship_opt:
            sc = ship_opt[0].get("shippingCost") or {}
            sv = _safe_float(sc.get("value"))
            if sv is not None:
                shipping = sv

        cond = it.get("condition") or ""

        results.append(
            pricing.EbayListing(
                price=price,
                shipping=shipping,
                condition_raw=str(cond),
            )
        )

    return results


def search_ebay_active_vinyl(
    artist: str,
    title: str,
    year: Optional[str],
    label: Optional[str],
    catalog: Optional[str],
    fmt: Optional[str],
) -> List[pricing.EbayListing]:
    """
    Search ACTIVE eBay listings for a given record.

    Returns a list of EbayListing objects (may be empty).
    """
    token = _get_token()
    if not token:
        # If we can't get a token, quietly return no data
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }

    params = {
        "q": _keywords(artist, title, year, label, catalog, fmt),
        "category_ids": EBAY_CATEGORY_VINYL,
        "limit": "50",
        "filter": "conditionIds:{3000}",  # 3000 = Used
    }

    try:
        resp = requests.get(EBAY_BROWSE_URL, headers=headers, params=params, timeout=20)
    except Exception as e:
        logging.warning("Browse ACTIVE request failed: %s", e)
        return []

    if resp.status_code == 429:
        logging.warning("Browse ACTIVE rate limit hit: %s", resp.text)
        return []

    if not resp.ok:
        logging.warning("Browse ACTIVE error %s: %s", resp.status_code, resp.text)
        return []

    try:
        data = resp.json()
    except ValueError:
        logging.warning("Could not parse ACTIVE JSON")
        return []

    return _extract_active(data)


# ---------------------------------------------------------------------------
# SOLD listings disabled
# ---------------------------------------------------------------------------

def search_ebay_sold_vinyl(*args, **kwargs) -> List[pricing.EbayListing]:
    """
    SOLD / Marketplace Insights is not enabled for this app.
    This stub always returns an empty list so the pricing
    engine will fall back to ACTIVE listings or other inputs.
    """
    return []
