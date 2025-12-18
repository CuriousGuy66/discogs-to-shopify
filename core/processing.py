from __future__ import annotations

import logging
from typing import Optional

from core.clients.discogs import DiscogsClient
from core.clients.musicbrainz import MusicBrainzClient
from core.exporters.base import Exporter
from core.lookup import find_release_with_fallback
from core.models import ProcessSummary, RecordInput
from core.ocr.etching_reader import EtchingReader
import pricing


class Processor:
    """Coordinator for input normalization, lookup, pricing, and export."""

    def __init__(
        self,
        discogs_client: DiscogsClient,
        musicbrainz_client: MusicBrainzClient,
        exporter: Exporter,
        etching_reader: Optional[EtchingReader] = None,
    ) -> None:
        self.discogs_client = discogs_client
        self.musicbrainz_client = musicbrainz_client
        self.exporter = exporter
        self.etching_reader = etching_reader
        self.logger = logging.getLogger(__name__)

    def process_records(self, records: list[RecordInput]) -> ProcessSummary:
        """
        Process records using MusicBrainz first, then Discogs as fallback.
        Exporter integration can be layered on later; for now we log matches.
        """
        total = len(records)
        matched = 0
        unmatched = 0

        for rec in records:
            self.logger.info(
                "Processing record artist=%r title=%r catalog=%r barcode=%r",
                rec.artist,
                rec.title,
                rec.catalog,
                rec.barcode,
            )
            match = find_release_with_fallback(rec, self.musicbrainz_client, self.discogs_client)
            if match:
                matched += 1
                self.logger.info(
                    "Matched via %s: id=%s title=%r artist=%r year=%r url=%s",
                    match.source,
                    match.release_id,
                    match.title,
                    match.artist,
                    match.year,
                    match.url,
                )
                self._log_pricing_from_match(rec, match)
            else:
                unmatched += 1
                self.logger.warning("No match found for artist=%r title=%r", rec.artist, rec.title)

        # Placeholder pricing/export integration; keeps summary populated.
        summary = ProcessSummary(
            total_rows=total,
            matched_count=matched,
            unmatched_count=unmatched,
            total_final_price=0.0,
            total_reference_price=0.0,
            price_diff=0.0,
        )
        self.logger.info(
            "Processing complete: total=%d matched=%d unmatched=%d",
            total,
            matched,
            unmatched,
        )
        return summary

    def _log_pricing_from_match(self, record: RecordInput, match) -> None:
        """
        Build a pricing context from an existing match. If the match originated
        from MusicBrainz but carries Discogs stats/suggestions, this will use
        those without performing a new Discogs lookup.
        """
        ctx = pricing.pricing_context_from_match(
            match=match,
            media_condition=record.media_condition,
            reference_price=record.reference_price,
            format_type="LP",
        )

        if not any(
            [
                ctx.discogs_high,
                ctx.discogs_median,
                ctx.discogs_last,
                ctx.discogs_low,
                ctx.discogs_suggested,
            ]
        ):
            return

        res = pricing.compute_price(ctx)
        self.logger.info(
            "Pricing (Discogs-from-MB link) for %r/%r: $%.2f strategy=%s "
            "[high=%s median=%s last=%s low=%s suggested=%s]",
            record.artist,
            record.title,
            res.final_price,
            res.strategy_code,
            ctx.discogs_high,
            ctx.discogs_median,
            ctx.discogs_last,
            ctx.discogs_low,
            ctx.discogs_suggested,
        )
