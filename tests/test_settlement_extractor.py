"""
Tests for the Settlement Extractor Service (2. Schreiben responses).
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from app.services.settlement_extractor import SettlementExtractor
from app.models.intent_classification import SettlementDecision


@patch("app.services.settlement_extractor.settings", MagicMock(anthropic_api_key="test-key"))
class TestSettlementExtractor:
    """Unit tests for SettlementExtractor."""

    def _make_mock_response(self, result_dict: dict):
        """Create a mock Anthropic API response."""
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json.dumps(result_dict))]
        mock_msg.usage = MagicMock(input_tokens=100, output_tokens=50)
        return mock_msg

    @patch("app.services.settlement_extractor.get_claude_breaker")
    @patch("app.services.settlement_extractor.Anthropic")
    def test_accepted_response(self, mock_anthropic_cls, mock_breaker_fn):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_breaker = MagicMock()
        mock_breaker_fn.return_value = mock_breaker
        mock_breaker.call.return_value = self._make_mock_response({
            "settlement_decision": "accepted",
            "counter_offer_amount": None,
            "conditions": None,
            "reference_to_proposal": "Ratenplan",
            "confidence": 0.95,
            "summary": "Gläubiger stimmt dem Schuldenbereinigungsplan zu.",
        })

        extractor = SettlementExtractor()
        result = extractor.extract(
            email_body="Wir stimmen dem Vergleichsvorschlag zu.",
            from_email="inkasso@example.de",
            subject="Re: Schuldenbereinigungsplan",
        )

        assert result.settlement_decision == SettlementDecision.accepted
        assert result.confidence == 0.95
        assert result.counter_offer_amount is None

    @patch("app.services.settlement_extractor.get_claude_breaker")
    @patch("app.services.settlement_extractor.Anthropic")
    def test_declined_response(self, mock_anthropic_cls, mock_breaker_fn):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_breaker = MagicMock()
        mock_breaker_fn.return_value = mock_breaker
        mock_breaker.call.return_value = self._make_mock_response({
            "settlement_decision": "declined",
            "counter_offer_amount": None,
            "conditions": None,
            "reference_to_proposal": "Nullplan",
            "confidence": 0.88,
            "summary": "Gläubiger lehnt den Schuldenbereinigungsplan ab.",
        })

        extractor = SettlementExtractor()
        result = extractor.extract(
            email_body="Wir lehnen den Vergleichsvorschlag ab.",
            from_email="inkasso@example.de",
        )

        assert result.settlement_decision == SettlementDecision.declined
        assert result.confidence == 0.88

    @patch("app.services.settlement_extractor.get_claude_breaker")
    @patch("app.services.settlement_extractor.Anthropic")
    def test_counter_offer_response(self, mock_anthropic_cls, mock_breaker_fn):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_breaker = MagicMock()
        mock_breaker_fn.return_value = mock_breaker
        mock_breaker.call.return_value = self._make_mock_response({
            "settlement_decision": "counter_offer",
            "counter_offer_amount": 2500.00,
            "conditions": "Einmalzahlung innerhalb 30 Tagen",
            "reference_to_proposal": "Ratenplan",
            "confidence": 0.82,
            "summary": "Gläubiger macht Gegenvorschlag: 2500 EUR Einmalzahlung.",
        })

        extractor = SettlementExtractor()
        result = extractor.extract(
            email_body="Wir bieten 2500 EUR als Einmalzahlung an.",
            from_email="inkasso@example.de",
        )

        assert result.settlement_decision == SettlementDecision.counter_offer
        assert result.counter_offer_amount == 2500.00
        assert result.conditions == "Einmalzahlung innerhalb 30 Tagen"

    @patch("app.services.settlement_extractor.get_claude_breaker")
    @patch("app.services.settlement_extractor.Anthropic")
    def test_low_confidence_needs_review(self, mock_anthropic_cls, mock_breaker_fn):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_breaker = MagicMock()
        mock_breaker_fn.return_value = mock_breaker
        mock_breaker.call.return_value = self._make_mock_response({
            "settlement_decision": "accepted",
            "counter_offer_amount": None,
            "conditions": None,
            "reference_to_proposal": None,
            "confidence": 0.55,
            "summary": "Antwort unklar.",
        })

        extractor = SettlementExtractor()
        result = extractor.extract(
            email_body="Danke für Ihr Schreiben.",
            from_email="inkasso@example.de",
        )

        assert result.confidence < 0.70

    @patch("app.services.settlement_extractor.get_claude_breaker")
    @patch("app.services.settlement_extractor.Anthropic")
    def test_no_clear_response_needs_review(self, mock_anthropic_cls, mock_breaker_fn):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_breaker = MagicMock()
        mock_breaker_fn.return_value = mock_breaker
        mock_breaker.call.return_value = self._make_mock_response({
            "settlement_decision": "no_clear_response",
            "counter_offer_amount": None,
            "conditions": None,
            "reference_to_proposal": None,
            "confidence": 0.40,
            "summary": "Email enthält keine klare Stellungnahme.",
        })

        extractor = SettlementExtractor()
        result = extractor.extract(
            email_body="Wir melden uns.",
            from_email="inkasso@example.de",
        )

        assert result.settlement_decision == SettlementDecision.no_clear_response

    def test_no_api_key_returns_fallback(self):
        with patch("app.services.settlement_extractor.settings") as mock_settings:
            mock_settings.anthropic_api_key = None
            extractor = SettlementExtractor()
            result = extractor.extract(
                email_body="test", from_email="test@example.de"
            )
            assert result.settlement_decision == SettlementDecision.no_clear_response
            assert result.confidence == 0.0

    @patch("app.services.settlement_extractor.get_claude_breaker")
    @patch("app.services.settlement_extractor.Anthropic")
    def test_malformed_json_returns_fallback(self, mock_anthropic_cls, mock_breaker_fn):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_breaker = MagicMock()
        mock_breaker_fn.return_value = mock_breaker
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="not valid json {{{")]
        mock_breaker.call.return_value = mock_msg

        extractor = SettlementExtractor()
        result = extractor.extract(
            email_body="test", from_email="test@example.de"
        )
        assert result.settlement_decision == SettlementDecision.no_clear_response
        assert result.confidence == 0.0
