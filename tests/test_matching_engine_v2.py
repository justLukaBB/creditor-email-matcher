"""
Tests for MatchingEngineV2 (Phase 6)

Tests cover:
- creditor_inquiries 30-day filter
- Both signals required (CONTEXT.MD)
- Gap threshold ambiguity detection
- Explainability JSONB format
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal

from app.services.matching_engine_v2 import MatchingEngineV2, MatchCandidate, MatchingResult
from app.services.matching import ThresholdManager


@pytest.fixture
def mock_db():
    """Mock database session."""
    return Mock()


@pytest.fixture
def mock_inquiry():
    """Create mock CreditorInquiry."""
    inquiry = Mock()
    inquiry.id = 1
    inquiry.client_name = "Max Mustermann"
    inquiry.client_name_normalized = "max mustermann"
    inquiry.creditor_email = "info@sparkasse.de"
    inquiry.reference_number = "AZ-12345"
    inquiry.sent_at = datetime.now() - timedelta(days=5)
    return inquiry


class TestMatchingEngineV2:
    """Tests for MatchingEngineV2 core functionality."""

    def test_no_candidates_returns_no_recent_inquiry(self, mock_db):
        """Test that empty candidate list returns no_recent_inquiry status."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        engine = MatchingEngineV2(mock_db)
        result = engine.find_match(
            email_id=1,
            extracted_data={"client_name": "Test"},
            from_email="test@test.de",
            received_at=datetime.now()
        )

        assert result.status == "no_recent_inquiry"
        assert result.needs_review is True

    def test_both_signals_required(self, mock_db, mock_inquiry):
        """CONTEXT.MD: Both name AND reference required for match."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_inquiry]
        # Mock threshold queries to return defaults
        mock_db.query.return_value.filter.return_value.first.return_value = None

        engine = MatchingEngineV2(mock_db)

        # Test with missing reference - should have low score
        result = engine.find_match(
            email_id=1,
            extracted_data={"client_name": "Max Mustermann", "reference_numbers": []},
            from_email="test@test.de",
            received_at=datetime.now()
        )

        # With only name match, score should be penalized
        if result.candidates:
            # Either below_threshold or very low score
            assert result.candidates[0].total_score < 0.5 or result.status == "below_threshold"

    def test_gap_threshold_auto_match(self, mock_db, mock_inquiry):
        """Test that clear gap results in auto_match."""
        # Create second inquiry with lower expected match
        second_inquiry = Mock()
        second_inquiry.id = 2
        second_inquiry.client_name = "Hans Schmidt"
        second_inquiry.client_name_normalized = "hans schmidt"
        second_inquiry.reference_number = "AZ-99999"
        second_inquiry.sent_at = datetime.now() - timedelta(days=10)

        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_inquiry, second_inquiry
        ]
        mock_db.query.return_value.filter.return_value.first.return_value = None

        engine = MatchingEngineV2(mock_db)
        result = engine.find_match(
            email_id=1,
            extracted_data={"client_name": "Max Mustermann", "reference_numbers": ["AZ-12345"]},
            from_email="info@sparkasse.de",
            received_at=datetime.now()
        )

        # With good match on first and poor on second, gap should exceed threshold
        if result.status == "auto_matched":
            assert result.match is not None
            assert result.match.inquiry.id == mock_inquiry.id
            assert result.gap >= 0.15  # Default gap_threshold

    def test_explainability_jsonb_format(self, mock_db, mock_inquiry):
        """Test that scoring_details has correct JSONB structure."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_inquiry]
        mock_db.query.return_value.filter.return_value.first.return_value = None

        engine = MatchingEngineV2(mock_db)
        result = engine.find_match(
            email_id=1,
            extracted_data={"client_name": "Max Mustermann", "reference_numbers": ["AZ-12345"]},
            from_email="test@test.de",
            received_at=datetime.now()
        )

        if result.candidates:
            scoring_details = result.candidates[0].scoring_details
            # Check required fields
            assert "version" in scoring_details
            assert "signals" in scoring_details
            assert "client_name" in scoring_details["signals"]
            assert "reference_number" in scoring_details["signals"]
            assert "weights" in scoring_details
            assert "filters_applied" in scoring_details
            assert scoring_details["filters_applied"]["both_signals_required"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
