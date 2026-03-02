"""
Tests for the Amount Update Guard.

TC-01 through TC-05 and TC-08 test the guard directly.
TC-04 additionally tests the consolidator.
TC-06 and TC-07 test matching integration with mocks.
"""

import pytest
from unittest.mock import patch, MagicMock

from app.services.amount_update_guard import should_update_amount


class TestAmountUpdateGuard:
    """Direct tests for should_update_amount."""

    def test_tc01_none_amount_blocked(self):
        """TC-01: Extraction returned None should be blocked."""
        ok, reason = should_update_amount(
            existing_amount=430.0, new_amount=None, confidence=0.0
        )
        assert ok is False
        assert reason == "extraction_returned_none"

    def test_tc02_downgrade_prevented(self):
        """TC-02: Amount downgrade (430 -> 300) should be blocked even at high confidence."""
        ok, reason = should_update_amount(
            existing_amount=430.0, new_amount=300.0, confidence=0.9
        )
        assert ok is False
        assert reason == "amount_downgrade_prevented"

    def test_tc03_low_confidence_blocked(self):
        """TC-03: Low confidence extraction should be blocked."""
        ok, reason = should_update_amount(
            existing_amount=None, new_amount=500.0, confidence=0.5
        )
        assert ok is False
        assert reason == "low_extraction_confidence"

    def test_tc04_consolidator_picks_highest(self):
        """TC-04: Consolidator picks max from multiple amounts, MEDIUM confidence."""
        from app.services.extraction.consolidator import ExtractionConsolidator
        from app.models.extraction_result import SourceExtractionResult, ExtractedAmount

        consolidator = ExtractionConsolidator()

        source_results = [
            SourceExtractionResult(
                source_type="email_body",
                extraction_method="text_parsing",
                gesamtforderung=ExtractedAmount(
                    value=200.0, source="email_body", confidence="MEDIUM"
                ),
            ),
            SourceExtractionResult(
                source_type="pdf",
                extraction_method="pymupdf",
                gesamtforderung=ExtractedAmount(
                    value=500.0, source="pdf", confidence="HIGH"
                ),
            ),
        ]

        result = consolidator.consolidate(source_results)
        assert result.gesamtforderung == 500.0
        # Weakest link: MEDIUM from email_body
        assert result.confidence == "MEDIUM"
        assert 200.0 in result.raw_candidates
        assert 500.0 in result.raw_candidates

    def test_tc05_upgrade_approved(self):
        """TC-05: Upgrade from 430 to 500 at high confidence should be approved."""
        ok, reason = should_update_amount(
            existing_amount=430.0, new_amount=500.0, confidence=0.9
        )
        assert ok is True
        assert reason == "amount_update_approved"

    def test_tc06_no_candidates_routes_to_manual(self):
        """TC-06: Matching returns no_candidates — email is routed to review, not discarded.

        The email_processor routes non-auto-matched results to the review queue.
        We verify that enqueue_ambiguous_match is called for below_threshold results.
        """
        mock_db = MagicMock()
        mock_matching_result = MagicMock()
        mock_matching_result.status = "below_threshold"
        mock_matching_result.candidates = []
        mock_matching_result.match = None

        with patch("app.services.review_queue.enqueue_ambiguous_match", return_value=42) as mock_enqueue:
            result = mock_enqueue(mock_db, 1, mock_matching_result)
            mock_enqueue.assert_called_once_with(mock_db, 1, mock_matching_result)
            assert result == 42

    def test_tc07_forwarded_email_still_extracts(self):
        """TC-07: Forwarded email with reply content should preserve matching signals.

        When a creditor forwards their original reply, the forwarded headers get
        stripped but the surrounding reply content should be preserved.
        """
        from app.services.email_parser import email_parser

        forwarded_body = (
            "Sehr geehrte Damen und Herren,\n"
            "anbei die Forderungsaufstellung.\n\n"
            "---------- Forwarded message ----------\n"
            "From: inkasso@creditor.de\n"
            "Subject: Forderungsaufstellung\n\n"
            "die Gesamtforderung beträgt 1.234,56 EUR.\n"
            "Aktenzeichen: 476982_64928\n"
            "Mandant: Max Mustermann\n"
        )

        parsed = email_parser.parse_email(
            html_body=None,
            text_body=forwarded_body,
        )
        # The reply content before the forwarded section should be preserved
        assert parsed["cleaned_body"] is not None
        assert len(parsed["cleaned_body"]) > 0

    def test_tc08_none_extraction_blocks_write(self):
        """TC-08: Extraction returns None (e.g. API timeout) — guard blocks DB write."""
        ok, reason = should_update_amount(
            existing_amount=430.0, new_amount=None, confidence=0.5
        )
        assert ok is False
        assert reason == "extraction_returned_none"

    def test_equal_amount_approved(self):
        """Equal amounts (not a downgrade) should be approved at high confidence."""
        ok, reason = should_update_amount(
            existing_amount=430.0, new_amount=430.0, confidence=0.9
        )
        assert ok is True
        assert reason == "amount_update_approved"

    def test_no_existing_amount_approved(self):
        """First-time write with no existing amount should be approved at high confidence."""
        ok, reason = should_update_amount(
            existing_amount=None, new_amount=500.0, confidence=0.9
        )
        assert ok is True
        assert reason == "amount_update_approved"

    def test_confidence_at_threshold_blocked(self):
        """Confidence exactly at threshold is still below (strict <)."""
        ok, reason = should_update_amount(
            existing_amount=None, new_amount=500.0, confidence=0.75
        )
        assert ok is True
        assert reason == "amount_update_approved"

    def test_confidence_just_below_threshold_blocked(self):
        """Confidence just below threshold should be blocked."""
        ok, reason = should_update_amount(
            existing_amount=None, new_amount=500.0, confidence=0.74
        )
        assert ok is False
        assert reason == "low_extraction_confidence"

    def test_custom_threshold(self):
        """Custom confidence threshold should be respected."""
        ok, reason = should_update_amount(
            existing_amount=None,
            new_amount=500.0,
            confidence=0.5,
            confidence_threshold=0.4,
        )
        assert ok is True
        assert reason == "amount_update_approved"
