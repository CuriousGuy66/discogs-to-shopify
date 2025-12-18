#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pricing.py  
================================================================================
FULL PRICING ENGINE MODULE - UNUSUAL FINDS VINYL PRICING SYSTEM
================================================================================

This module centralizes ALL pricing logic for the Discogs -> Shopify pipeline.

What this file provides:
------------------------
    • PricingContext  (input data)
    • PricingResult   (output data)
    • compute_price() (main function)
    • enrich_row_with_pricing() (adds pricing fields to output row)

This is the ONLY place you will ever modify pricing rules.

Pricing logic implemented:
--------------------------
    ✓ eBay SOLD listings (90-day window assumed by caller)
    ✓ eBay ACTIVE listings (used only if SOLD is empty)
    ✓ Condition normalization and tolerance expansion
    ✓ Condition-distance adjustments
    ✓ $5 shipping assumption rule
    ✓ 10% competitive discount (eBay pricing only)
    ✓ Median, trimmed mean (10%), composite average
    ✓ Discogs fallback (median -> last -> low)
    ✓ Spreadsheet reference fallback
    ✓ Comparable fallback
    ✓ Global floor = $5.00
    ✓ Round to nearest $0.25
    ✓ Strategy codes (EB1, EBM, EBT, EBC, EBA, DMED, DLST, DLOW, REF, CMP, FLR)
    ✓ Pricing notes (ASCII only to prevent Excel mojibake)

This module is completely standalone and can be replaced in the future without
touching the GUI script or Shopify exporter.

================================================================================
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from statistics import median
from typing import Any as _Any  # avoid circulars for helper signatures

# Optional typing for upstream matches without hard dependency.
try:
    from core.models import ReleaseMatch  # type: ignore
except Exception:  # pragma: no cover
    ReleaseMatch = _Any  # type: ignore


# ====================================================================
# CONFIG CONSTANTS
# ====================================================================
GLOBAL_PRICE_FLOOR = 5.00
COMPETITIVE_DISCOUNT = 0.10     # 10% off for eBay-based prices
TRIM_PERCENT = 0.10             # 10% top/bottom trimming


# ====================================================================
# CONDITION NORMALIZATION
# ====================================================================
CONDITION_LADDER = [
    "M",
    "NM",
    "VG+",
    "VG",
    "G+",
    "G",
    "F/P",
]

def normalize_condition(cond: Optional[str]) -> Optional[str]:
    """Normalize raw text condition (Discogs/eBay) to CONDITION_LADDER values."""
    if not cond:
        return None
    t = cond.strip().lower()

    if "mint (m)" in t and "near" not in t:
        return "M"
    if "near mint" in t or "(nm or m-)" in t or t == "nm" or "m-" in t:
        return "NM"
    if "vg+" in t or "very good plus" in t or "excellent" in t or t == "ex":
        return "VG+"
    if t == "vg" or "very good" in t:
        return "VG"
    if "g+" in t or "good plus" in t:
        return "G+"
    if t == "g" or t == "good":
        return "G"
    if "fair" in t or "poor" in t:
        return "F/P"

    # Fallback keyword heuristics
    if "great shape" in t:
        return "VG+"
    if "surface noise" in t or "scratches" in t:
        return "VG"
    if "heavy wear" in t:
        return "G"

    return None

def condition_distance(a: Optional[str], b: Optional[str]) -> Optional[int]:
    if not a or not b:
        return None
    try:
        return abs(CONDITION_LADDER.index(a) - CONDITION_LADDER.index(b))
    except ValueError:
        return None

def compare_condition(listing_cond: str, your_cond: str) -> int:
    """
    Return +1 if listing_cond is better than your_cond
           -1 if worse
            0 if equal
    """
    return (CONDITION_LADDER.index(your_cond) - CONDITION_LADDER.index(listing_cond)) * -1


# ====================================================================
# EBAY LISTING MODEL
# ====================================================================
@dataclass
class EbayListing:
    price: float
    shipping: float
    condition_raw: str


# ====================================================================
# PRICING CONTEXT
# ====================================================================
@dataclass
class PricingContext:
    format_type: str
    media_condition: Optional[str] = None

    reference_price: Optional[float] = None

    discogs_high: Optional[float] = None
    discogs_suggested: Optional[float] = None
    discogs_median: Optional[float] = None
    discogs_last: Optional[float] = None
    discogs_low: Optional[float] = None

    comparable_price: Optional[float] = None

    ebay_sold: List[EbayListing] = field(default_factory=list)
    ebay_active: List[EbayListing] = field(default_factory=list)


# ====================================================================
# PRICING RESULT
# ====================================================================
@dataclass
class PricingResult:
    final_price: float
    strategy_code: str
    notes: str


# ====================================================================
# NUMERIC HELPERS
# ====================================================================
def trimmed_mean(values: List[float], trim_percent: float = TRIM_PERCENT) -> float:
    if not values:
        raise ValueError("Cannot compute trimmed mean of empty list")

    n = len(values)
    if n < 3:
        return sum(values) / n

    sorted_vals = sorted(values)
    k = int(n * trim_percent)

    if k == 0 or k * 2 >= n:
        return sum(sorted_vals) / n

    trimmed = sorted_vals[k:-k]
    if not trimmed:
        trimmed = sorted_vals

    return sum(trimmed) / len(trimmed)

def round_quarter(value: float) -> float:
    return round(value * 4) / 4.0


# ====================================================================
# SHIPPING NORMALIZATION
# ====================================================================
def compute_effective_price(listing: EbayListing) -> float:
    shipping_effective = 5.0 if listing.shipping > 0 else 0.0
    return listing.price + shipping_effective


# ====================================================================
# CONDITION-ADJUSTED PRICE
# ====================================================================
def adjust_price_for_condition(base_price: float, your_cond: Optional[str], listing_cond: str) -> float:
    your = normalize_condition(your_cond)
    theirs = normalize_condition(listing_cond)

    if not your or not theirs:
        return base_price

    dist = condition_distance(your, theirs)
    if dist is None or dist == 0:
        return base_price

    direction = compare_condition(theirs, your)

    if dist == 1:
        if direction > 0:
            return base_price * 0.90
        else:
            return base_price * 1.10

    # distance 2+
    if direction > 0:
        return base_price * 0.80
    else:
        return base_price * 1.20


# ====================================================================
# EBAY SOLD / ACTIVE PRICING ENGINE
# ====================================================================
def compute_ebay_price(
    listings: List[EbayListing],
    your_condition: Optional[str],
    code_single: str,
    code_multi: str,
) -> Optional[PricingResult]:

    if not listings:
        return None

    adjusted_prices = []
    for lst in listings:
        base = compute_effective_price(lst)
        price_adj = adjust_price_for_condition(base, your_condition, lst.condition_raw)
        adjusted_prices.append(price_adj)

    if len(adjusted_prices) == 1:
        price = adjusted_prices[0]
        strategy = code_single
        note_source = "single eBay listing"
    else:
        med = median(adjusted_prices)
        trim = trimmed_mean(adjusted_prices)
        price = (med + trim) / 2
        strategy = code_multi
        note_source = f"{len(adjusted_prices)} eBay listings (median+trimmed composite)"

    price *= (1 - COMPETITIVE_DISCOUNT)

    price = round_quarter(price)
    price = max(price, GLOBAL_PRICE_FLOOR)

    notes = (
        f"{strategy} - {note_source}, "
        "condition-adjusted, $5 shipping rule, "
        "10% competitive reduction, rounded"
    )

    return PricingResult(price, strategy, notes)


# ====================================================================
# DISCOGS FALLBACK
# ====================================================================
def discogs_fallback(ctx: PricingContext) -> Optional[PricingResult]:
    if ctx.discogs_high:
        price = ctx.discogs_high
        code = "DHIG"
        notes = "DHIG - Discogs high marketplace price"
    elif ctx.discogs_suggested:
        price = ctx.discogs_suggested
        code = "DSUG"
        notes = "DSUG - Discogs price suggestion (condition-based)"
    elif ctx.discogs_median:
        price = ctx.discogs_median
        code = "DMED"
        notes = "DMED - Discogs median sold price"
    elif ctx.discogs_last:
        price = ctx.discogs_last
        code = "DLST"
        notes = "DLST - Discogs last sold price"
    elif ctx.discogs_low:
        price = ctx.discogs_low
        code = "DLOW"
        notes = "DLOW - Discogs low sold price"
    else:
        return None

    price = round_quarter(price)
    price = max(price, GLOBAL_PRICE_FLOOR)

    return PricingResult(price, code, notes)


# ====================================================================
# REFERENCE / COMPARABLE / FLOOR
# ====================================================================
def ref_or_comparable(ctx: PricingContext) -> Optional[PricingResult]:
    if ctx.reference_price:
        price = ctx.reference_price
        code = "REF"
        notes = "REF - Spreadsheet reference"
    elif ctx.comparable_price:
        price = ctx.comparable_price
        code = "CMP"
        notes = "CMP - Comparable"
    else:
        return None

    price = round_quarter(price)
    price = max(price, GLOBAL_PRICE_FLOOR)
    return PricingResult(price, code, notes)

def floor_result() -> PricingResult:
    return PricingResult(GLOBAL_PRICE_FLOOR, "FLR", "FLR - Price floor applied")


def maybe_override_with_reference(res: PricingResult, ctx: PricingContext) -> PricingResult:
    """
    If a reference price exists and is higher than the current result, override with REF.
    """
    if ctx.reference_price is None:
        return res

    try:
        ref_val = float(ctx.reference_price)
    except (TypeError, ValueError):
        return res

    ref_val = round_quarter(ref_val)
    ref_val = max(ref_val, GLOBAL_PRICE_FLOOR)

    if ref_val > res.final_price:
        return PricingResult(ref_val, "REF", "REF - Spreadsheet reference (overrides lower price)")
    return res


# ====================================================================
# MAIN PRICING DECISION ENGINE
# ====================================================================
def compute_price(ctx: PricingContext) -> PricingResult:

    sold_res = compute_ebay_price(ctx.ebay_sold, ctx.media_condition, "EB1", "EBC")
    if sold_res:
        return maybe_override_with_reference(sold_res, ctx)

    active_res = compute_ebay_price(ctx.ebay_active, ctx.media_condition, "EBA", "EBA")
    if active_res:
        return maybe_override_with_reference(active_res, ctx)

    disc_res = discogs_fallback(ctx)
    if disc_res:
        return maybe_override_with_reference(disc_res, ctx)

    ref_res = ref_or_comparable(ctx)
    if ref_res:
        return ref_res

    floor_res = floor_result()
    return maybe_override_with_reference(floor_res, ctx)


# ====================================================================
# ROW ENRICHMENT FOR SHOPIFY CSV
# ====================================================================
def enrich_row_with_pricing(row: Dict[str, Any], ctx: PricingContext) -> Dict[str, Any]:
    res = compute_price(ctx)

    row["Price"] = f"{res.final_price:.2f}"
    row["Pricing Strategy Used"] = res.strategy_code
    row["Pricing Notes"] = res.notes

    return row


# ====================================================================
# MATCH-DRIVEN PRICING HELPERS (e.g., MusicBrainz hit with Discogs url-rel)
# ====================================================================
def _extract_price_value(stats: Optional[dict], key: str) -> Optional[float]:
    if not stats or key not in stats:
        return None
    val = stats.get(key)
    if isinstance(val, dict):
        try:
            if val.get("value") is not None:
                return float(val["value"])
        except (TypeError, ValueError):
            return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def pricing_context_from_match(
    match: _Any,
    media_condition: Optional[str],
    reference_price: Optional[float],
    format_type: str = "LP",
) -> PricingContext:
    """
    Build a PricingContext using any Discogs stats/suggestions already attached
    to a ReleaseMatch (e.g., from a MusicBrainz url-rel), avoiding a new Discogs lookup.
    """
    stats = getattr(match, "discogs_marketplace_stats", None) or {}
    suggestions = getattr(match, "discogs_price_suggestions", None) or {}

    discogs_high = _extract_price_value(stats, "highest_price")
    discogs_median = _extract_price_value(stats, "median")
    discogs_last = _extract_price_value(stats, "last")
    discogs_low = _extract_price_value(stats, "lowest_price")

    discogs_suggested = None
    if suggestions:
        try:
            discogs_suggested = discogs_price_from_suggestions(media_condition or "", suggestions)
        except Exception:
            discogs_suggested = None

    return PricingContext(
        format_type=format_type,
        media_condition=media_condition,
        reference_price=reference_price,
        discogs_high=discogs_high,
        discogs_suggested=discogs_suggested,
        discogs_median=discogs_median,
        discogs_last=discogs_last,
        discogs_low=discogs_low,
        comparable_price=None,
        ebay_sold=[],
        ebay_active=[],
    )


def compute_price_from_match(
    match: _Any,
    media_condition: Optional[str],
    reference_price: Optional[float],
    format_type: str = "LP",
) -> PricingResult:
    """
    Convenience wrapper: build a PricingContext from a match carrying Discogs
    stats/suggestions (e.g., MusicBrainz hit with Discogs url-rel) and compute price.
    """
    ctx = pricing_context_from_match(match, media_condition, reference_price, format_type)
    return compute_price(ctx)
