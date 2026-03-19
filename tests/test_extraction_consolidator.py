"""
Tests for the Extraction Consolidator edge cases.

Validates that the consolidator correctly handles missing amounts, deduplication,
and the original bug scenario (430 EUR existing + no extraction → None, not 100).
"""

import pytest

from app.models.extraction_result import (
    SourceExtractionResult,
    ExtractedAmount,
    ConsolidatedExtractionResult,
)
from app.services.extraction.consolidator import ExtractionConsolidator


@pytest.fixture
def consolidator():
    return ExtractionConsolidator()


class TestConsolidatorEdgeCases:
    def test_no_sources_returns_none(self, consolidator):
        """No sources → gesamtforderung is None."""
        result = consolidator.consolidate([])
        assert result.gesamtforderung is None
        assert result.confidence == "LOW"
        assert result.extraction_method_final == "none"
        assert result.extraction_reason == "no_sources_provided"
        assert result.raw_candidates == []

    def test_all_sources_no_amounts(self, consolidator):
        """All sources present but none have amounts → gesamtforderung is None."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=None,
            ),
            SourceExtractionResult(
                source_type="pdf",
                source_name="invoice.pdf",
                extraction_method="pymupdf",
                gesamtforderung=None,
            ),
        ]
        result = consolidator.consolidate(sources)
        assert result.gesamtforderung is None
        assert result.confidence == "LOW"
        assert result.extraction_method_final == "none"
        assert result.extraction_reason == "no_amounts_found_in_any_source"
        assert result.raw_candidates == []

    def test_one_source_with_amount(self, consolidator):
        """Single source with an amount → uses that amount."""
        sources = [
            SourceExtractionResult(
                source_type="pdf",
                source_name="forderung.pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=430.0, source="pdf", confidence="HIGH"
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        assert result.gesamtforderung == 430.0
        assert result.confidence == "HIGH"
        assert result.extraction_method_final == "ai_primary"
        assert result.raw_candidates == [430.0]

    def test_multiple_sources_highest_wins(self, consolidator):
        """Multiple sources → highest amount wins."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=200.0, source="email_body", confidence="MEDIUM"
                ),
            ),
            SourceExtractionResult(
                source_type="pdf",
                source_name="forderung.pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=500.0, source="pdf", confidence="HIGH"
                ),
            ),
            SourceExtractionResult(
                source_type="docx",
                source_name="details.docx",
                extraction_method="python_docx",
                gesamtforderung=ExtractedAmount(
                    value=350.0, source="docx", confidence="MEDIUM"
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        assert result.gesamtforderung == 500.0
        assert result.sources_with_amount == 3
        assert len(result.raw_candidates) == 3

    def test_deduplication_within_1eur(self, consolidator):
        """Amounts within 1 EUR of each other are deduplicated."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=430.50, source="email_body", confidence="MEDIUM"
                ),
            ),
            SourceExtractionResult(
                source_type="pdf",
                source_name="forderung.pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=430.00, source="pdf", confidence="HIGH"
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        # Both are within 1 EUR, highest (430.50) wins
        assert result.gesamtforderung == 430.50
        # raw_candidates includes all pre-dedup values
        assert len(result.raw_candidates) == 2

    def test_original_bug_scenario(self, consolidator):
        """Original bug: 430 EUR existing + no extraction → must return None, not 100."""
        # Simulate the email with no clear amount
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=None,  # No amount found in email
            ),
        ]
        result = consolidator.consolidate(sources)

        # Critical assertion: must be None, NOT 100.0
        assert result.gesamtforderung is None
        assert result.extraction_method_final == "none"
        assert result.extraction_reason == "no_amounts_found_in_any_source"

    def test_weakest_link_confidence(self, consolidator):
        """Final confidence is weakest link across all sources."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=500.0, source="email_body", confidence="LOW"
                ),
            ),
            SourceExtractionResult(
                source_type="pdf",
                source_name="forderung.pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=500.0, source="pdf", confidence="HIGH"
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        assert result.confidence == "LOW"

    def test_email_body_only_is_regex_fallback(self, consolidator):
        """Amount found only in email_body → extraction_method_final is regex_fallback."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=300.0, source="email_body", confidence="MEDIUM"
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        assert result.extraction_method_final == "regex_fallback"


class TestTierAwareConsolidation:
    """Tests for tier-aware amount selection (Summe/insgesamt > catch-all)."""

    def test_tier1_beats_tier3_even_when_lower(self, consolidator):
        """Badenova bug: Summe 182.30 (tier 1) must beat catch-all 761 (tier 3)."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=182.30, source="email_body", confidence="HIGH", tier=1
                ),
            ),
            SourceExtractionResult(
                source_type="pdf",
                source_name="kontoinformation.pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=761.0, source="pdf", confidence="MEDIUM", tier=3
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        assert result.gesamtforderung == 182.30
        assert "tier_1" in result.extraction_reason

    def test_tier2_beats_tier3(self, consolidator):
        """Specific keyword (tier 2) beats catch-all (tier 3)."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=500.0, source="email_body", confidence="MEDIUM", tier=2
                ),
            ),
            SourceExtractionResult(
                source_type="pdf",
                source_name="invoice.pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=900.0, source="pdf", confidence="MEDIUM", tier=3
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        assert result.gesamtforderung == 500.0

    def test_same_tier_highest_wins(self, consolidator):
        """Within same tier, highest amount wins (backwards compatible)."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=200.0, source="email_body", confidence="MEDIUM", tier=2
                ),
            ),
            SourceExtractionResult(
                source_type="pdf",
                source_name="forderung.pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=500.0, source="pdf", confidence="HIGH", tier=2
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        assert result.gesamtforderung == 500.0

    def test_default_tier3_backwards_compatible(self, consolidator):
        """Amounts without explicit tier default to 3 (backwards compatible)."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=200.0, source="email_body", confidence="MEDIUM"
                ),
            ),
            SourceExtractionResult(
                source_type="pdf",
                source_name="forderung.pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=500.0, source="pdf", confidence="HIGH"
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        # Both default to tier 3, so highest wins (backwards compatible)
        assert result.gesamtforderung == 500.0

    def test_dedup_keeps_better_tier(self, consolidator):
        """When amounts are within 1 EUR, keep the one with better tier."""
        sources = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=182.30, source="email_body", confidence="HIGH", tier=1
                ),
            ),
            SourceExtractionResult(
                source_type="pdf",
                source_name="doc.pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=182.50, source="pdf", confidence="MEDIUM", tier=3
                ),
            ),
        ]
        result = consolidator.consolidate(sources)
        # Within 1 EUR, tier 1 version kept
        assert result.gesamtforderung == 182.30
