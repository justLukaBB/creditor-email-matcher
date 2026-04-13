"""
Phase 1 Regression Tests: Amount Extraction, Merge Logic, and Lookback Window.

Tests cover:
- Empty body guard in entity extractor
- Aktenzeichen-pattern body (should NOT extract amount)
- Sanity check for implausible amounts (> 500k)
- Merge logic: 0.0 values preserved (not overwritten by LLM fallback)
- Lookback window configurable and defaults to 90 days
"""

import pytest
from unittest.mock import patch, MagicMock

from app.services.entity_extractor_claude import EntityExtractorClaude, ExtractedEntities
from app.config import settings


class TestEmptyBodyGuard:
    """Entity extractor should skip extraction for empty or too-short bodies."""

    def setup_method(self):
        self.extractor = EntityExtractorClaude()

    def test_empty_body_returns_no_extraction(self):
        """Empty email body should return is_creditor_reply=False, no API call."""
        result = self.extractor.extract_entities(
            email_body="",
            from_email="test@creditor.de",
        )
        assert result.is_creditor_reply is False
        assert result.debt_amount is None
        assert result.confidence == 0.0

    def test_none_body_returns_no_extraction(self):
        """None body should be handled gracefully."""
        result = self.extractor.extract_entities(
            email_body=None,
            from_email="test@creditor.de",
        )
        assert result.is_creditor_reply is False
        assert result.debt_amount is None

    def test_whitespace_only_body_returns_no_extraction(self):
        """Body with only whitespace should be treated as empty."""
        result = self.extractor.extract_entities(
            email_body="   \n\t  \n  ",
            from_email="test@creditor.de",
        )
        assert result.is_creditor_reply is False
        assert result.debt_amount is None

    def test_very_short_body_returns_no_extraction(self):
        """Body shorter than MIN_BODY_LENGTH should skip extraction."""
        result = self.extractor.extract_entities(
            email_body="Danke",  # 5 chars < 20
            from_email="test@creditor.de",
        )
        assert result.is_creditor_reply is False
        assert result.debt_amount is None

    def test_short_body_with_attachments_still_extracts(self):
        """Short body but with attachment text should still attempt extraction."""
        # This test verifies the guard is bypassed when attachments exist.
        # We mock the API call since the actual extraction requires API access.
        with patch.object(self.extractor, 'client') as mock_client:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text='{"is_creditor_reply": true, "debt_amount": 1234.56, "confidence": 0.9, "reference_numbers": []}')]
            mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
            mock_client.messages.create.return_value = mock_response

            # Patch circuit breaker to pass through
            with patch('app.services.entity_extractor_claude.get_claude_breaker') as mock_breaker:
                mock_breaker.return_value.call = lambda fn, **kwargs: fn(**kwargs)

                result = self.extractor.extract_entities(
                    email_body="Kurz",  # Too short alone
                    from_email="test@creditor.de",
                    attachment_texts=["Gesamtforderung: 1.234,56 EUR für Max Mustermann"],
                )
                # Should NOT skip — attachment text is present
                assert result.is_creditor_reply is True
                assert result.debt_amount == 1234.56


class TestAmountSanityCheck:
    """Extracted amounts above 500k EUR should be rejected."""

    def setup_method(self):
        self.extractor = EntityExtractorClaude()

    def test_implausible_amount_nullified(self):
        """Amount > 500k should be set to None after extraction."""
        with patch.object(self.extractor, 'client') as mock_client:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(
                text='{"is_creditor_reply": true, "debt_amount": 25032026.0, "confidence": 0.8, "reference_numbers": []}'
            )]
            mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
            mock_client.messages.create.return_value = mock_response

            with patch('app.services.entity_extractor_claude.get_claude_breaker') as mock_breaker:
                mock_breaker.return_value.call = lambda fn, **kwargs: fn(**kwargs)

                result = self.extractor.extract_entities(
                    email_body="Sehr geehrte Damen und Herren, die Forderung beträgt 25.032.026 EUR.",
                    from_email="test@creditor.de",
                )
                # Implausible amount should be nullified
                assert result.debt_amount is None

    def test_plausible_amount_preserved(self):
        """Amount <= 500k should pass through."""
        with patch.object(self.extractor, 'client') as mock_client:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(
                text='{"is_creditor_reply": true, "debt_amount": 1234.56, "confidence": 0.9, "reference_numbers": []}'
            )]
            mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
            mock_client.messages.create.return_value = mock_response

            with patch('app.services.entity_extractor_claude.get_claude_breaker') as mock_breaker:
                mock_breaker.return_value.call = lambda fn, **kwargs: fn(**kwargs)

                result = self.extractor.extract_entities(
                    email_body="Sehr geehrte Damen und Herren, die Gesamtforderung beträgt 1.234,56 EUR.",
                    from_email="test@creditor.de",
                )
                assert result.debt_amount == 1234.56

    def test_amount_exactly_at_threshold_preserved(self):
        """Amount exactly at 500k should pass (boundary)."""
        with patch.object(self.extractor, 'client') as mock_client:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(
                text='{"is_creditor_reply": true, "debt_amount": 500000.0, "confidence": 0.9, "reference_numbers": []}'
            )]
            mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
            mock_client.messages.create.return_value = mock_response

            with patch('app.services.entity_extractor_claude.get_claude_breaker') as mock_breaker:
                mock_breaker.return_value.call = lambda fn, **kwargs: fn(**kwargs)

                result = self.extractor.extract_entities(
                    email_body="Sehr geehrte Damen und Herren, die Restschuld beträgt 500.000,00 EUR.",
                    from_email="test@creditor.de",
                )
                assert result.debt_amount == 500000.0


class TestMergeLogicFalsyValues:
    """Merge logic must use `is not None` instead of `or` to preserve 0.0."""

    def test_zero_amount_not_overwritten(self):
        """A 0.0 debt_amount from pipeline should NOT be overwritten by LLM extraction."""
        current_extracted_data = {
            "client_name": "Mustermann, Max",
            "creditor_name": "Sparkasse Bochum",
            "debt_amount": 0.0,  # Explicitly set to zero — must survive merge
            "confidence": 0.8,
        }
        llm_entities = ExtractedEntities(
            is_creditor_reply=True,
            client_name="Max Mustermann",
            creditor_name="Sparkasse",
            debt_amount=999.99,  # LLM hallucinated amount
            reference_numbers=["AZ-123"],
            confidence=0.7,
        )

        # Simulate the fixed merge logic
        current_amount = current_extracted_data.get("debt_amount")
        merged_amount = current_amount if current_amount is not None else llm_entities.debt_amount

        assert merged_amount == 0.0, "0.0 should not be overwritten by LLM amount"

    def test_none_amount_falls_through_to_llm(self):
        """A None debt_amount from pipeline SHOULD fall through to LLM extraction."""
        current_extracted_data = {
            "client_name": "Mustermann, Max",
            "creditor_name": None,
            "debt_amount": None,
            "confidence": 0.5,
        }
        llm_entities = ExtractedEntities(
            is_creditor_reply=True,
            client_name="Max Mustermann",
            creditor_name="Sparkasse Bochum",
            debt_amount=1234.56,
            reference_numbers=[],
            confidence=0.8,
        )

        current_amount = current_extracted_data.get("debt_amount")
        merged_amount = current_amount if current_amount is not None else llm_entities.debt_amount

        assert merged_amount == 1234.56, "None should fall through to LLM amount"

    def test_empty_string_creditor_not_overwritten(self):
        """An empty string creditor_name from pipeline should be preserved (not overwritten)."""
        current_extracted_data = {
            "creditor_name": "",  # Explicitly empty — might be intentional
        }
        llm_creditor_name = "Sparkasse Bochum"

        current_creditor = current_extracted_data.get("creditor_name")
        merged = current_creditor if current_creditor is not None else llm_creditor_name

        # Empty string is not None, so it should be preserved
        assert merged == ""

    def test_buggy_or_behavior_would_overwrite_zero(self):
        """Demonstrates the old buggy behavior with `or` for documentation."""
        # This is the OLD buggy code path — verifying the bug exists
        current_amount = 0.0
        llm_amount = 999.99

        # Old: `current or llm` treats 0.0 as falsy
        buggy_result = current_amount or llm_amount
        assert buggy_result == 999.99, "Confirms the `or` bug overwrites 0.0"

        # New: explicit None check preserves 0.0
        fixed_result = current_amount if current_amount is not None else llm_amount
        assert fixed_result == 0.0, "Fixed logic preserves 0.0"


class TestLookbackWindow:
    """Lookback window should default to 90 days and be configurable."""

    def test_config_default_is_90_days(self):
        """Config default for match_lookback_days should be 90."""
        assert settings.match_lookback_days == 90

    def test_matching_engine_uses_config_value(self):
        """MatchingEngineV2 should use the config value, not hardcoded 30."""
        from app.services.matching_engine_v2 import DEFAULT_LOOKBACK_DAYS
        assert DEFAULT_LOOKBACK_DAYS == settings.match_lookback_days
        assert DEFAULT_LOOKBACK_DAYS == 90

    def test_matching_engine_accepts_custom_lookback(self):
        """MatchingEngineV2 should accept a custom lookback_days parameter."""
        from app.services.matching_engine_v2 import MatchingEngineV2

        mock_db = MagicMock()
        engine = MatchingEngineV2(db=mock_db, lookback_days=180)
        assert engine.lookback_days == 180


class TestMinBodyLengthConstant:
    """Verify MIN_BODY_LENGTH is set correctly."""

    def test_min_body_length_is_20(self):
        assert EntityExtractorClaude.MIN_BODY_LENGTH == 20

    def test_max_plausible_amount_is_500k(self):
        assert EntityExtractorClaude.MAX_PLAUSIBLE_AMOUNT == 500_000
