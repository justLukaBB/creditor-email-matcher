"""
Integration tests for 2. Schreiben pipeline branch.
Verifies settlement extraction → MongoDB write → portal notification flow.
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

from app.models.intent_classification import SettlementExtractionResult, SettlementDecision


class TestSecondRoundPipeline:
    """Tests for _process_second_round in email_processor."""

    def _make_mocks(self):
        """Create standard mock objects for the pipeline."""
        db = MagicMock()
        email = MagicMock()
        email.agent_checkpoints = {}
        email.subject = "Re: Schuldenbereinigungsplan"

        matched_inquiry = MagicMock()
        matched_inquiry.id = 42
        matched_inquiry.letter_type = "second"
        matched_inquiry.client_name = "Mustermann, Max"

        matching_result = MagicMock()
        matching_result.match.total_score = 0.92
        matching_result.match.inquiry = matched_inquiry

        confidence_result = MagicMock()
        confidence_result.overall = 0.88

        route = MagicMock()
        route.level.value = "high"

        return db, email, matched_inquiry, matching_result, confidence_result, route

    @patch("app.services.portal_notifier.notify_settlement_response")
    @patch("app.services.mongodb_client.mongodb_service")
    @patch("app.services.settlement_extractor.settlement_extractor")
    def test_accepted_settlement_full_pipeline(
        self, mock_extractor, mock_mongodb, mock_notify
    ):
        from app.actors.email_processor import _process_second_round

        db, email, inquiry, matching, confidence, route = self._make_mocks()

        mock_extractor.extract.return_value = SettlementExtractionResult(
            settlement_decision=SettlementDecision.accepted,
            confidence=0.95,
            summary="Gläubiger stimmt zu.",
        )
        mock_mongodb.update_settlement_response.return_value = True

        _process_second_round(
            db=db, email=email, email_id=1, matched_inquiry=inquiry,
            matching_result=matching, client_name="Mustermann, Max",
            client_aktenzeichen="542900", creditor_email="inkasso@example.de",
            creditor_name="Inkasso GmbH", email_body="Wir stimmen zu.",
            subject="Re: SBP", confidence_result=confidence, route=route,
        )

        # Verify settlement extractor was called
        mock_extractor.extract.assert_called_once()

        # Verify MongoDB write
        mock_mongodb.update_settlement_response.assert_called_once()
        call_kwargs = mock_mongodb.update_settlement_response.call_args.kwargs
        assert call_kwargs["settlement_decision"] == "accepted"
        assert call_kwargs["client_name"] == "Mustermann, Max"

        # Verify portal notification
        mock_notify.assert_called_once()
        notify_kwargs = mock_notify.call_args.kwargs
        assert notify_kwargs["settlement_decision"] == "accepted"

        # Verify match status
        assert email.match_status == "auto_matched"

    @patch("app.services.portal_notifier.notify_settlement_response")
    @patch("app.services.mongodb_client.mongodb_service")
    @patch("app.services.settlement_extractor.settlement_extractor")
    def test_counter_offer_saves_amount(
        self, mock_extractor, mock_mongodb, mock_notify
    ):
        from app.actors.email_processor import _process_second_round

        db, email, inquiry, matching, confidence, route = self._make_mocks()

        mock_extractor.extract.return_value = SettlementExtractionResult(
            settlement_decision=SettlementDecision.counter_offer,
            counter_offer_amount=3500.00,
            conditions="Einmalzahlung",
            confidence=0.85,
            summary="Gegenvorschlag: 3500 EUR.",
        )
        mock_mongodb.update_settlement_response.return_value = True

        _process_second_round(
            db=db, email=email, email_id=2, matched_inquiry=inquiry,
            matching_result=matching, client_name="Test User",
            client_aktenzeichen=None, creditor_email="test@example.de",
            creditor_name="Test GmbH", email_body="Wir bieten 3500 EUR.",
            subject=None, confidence_result=confidence, route=route,
        )

        call_kwargs = mock_mongodb.update_settlement_response.call_args.kwargs
        assert call_kwargs["counter_offer_amount"] == 3500.00
        assert call_kwargs["conditions"] == "Einmalzahlung"

    @patch("app.services.portal_notifier.notify_settlement_response")
    @patch("app.services.mongodb_client.mongodb_service")
    @patch("app.services.settlement_extractor.settlement_extractor")
    def test_low_confidence_triggers_needs_review(
        self, mock_extractor, mock_mongodb, mock_notify
    ):
        from app.actors.email_processor import _process_second_round

        db, email, inquiry, matching, confidence, route = self._make_mocks()

        mock_extractor.extract.return_value = SettlementExtractionResult(
            settlement_decision=SettlementDecision.accepted,
            confidence=0.55,
            summary="Unklar.",
        )
        mock_mongodb.update_settlement_response.return_value = True

        _process_second_round(
            db=db, email=email, email_id=3, matched_inquiry=inquiry,
            matching_result=matching, client_name="Test",
            client_aktenzeichen=None, creditor_email="test@example.de",
            creditor_name="Test", email_body="...", subject=None,
            confidence_result=confidence, route=route,
        )

        assert email.match_status == "needs_review"

    @patch("app.services.portal_notifier.notify_settlement_response")
    @patch("app.services.mongodb_client.mongodb_service")
    @patch("app.services.settlement_extractor.settlement_extractor")
    def test_no_clear_response_triggers_needs_review(
        self, mock_extractor, mock_mongodb, mock_notify
    ):
        from app.actors.email_processor import _process_second_round

        db, email, inquiry, matching, confidence, route = self._make_mocks()

        mock_extractor.extract.return_value = SettlementExtractionResult(
            settlement_decision=SettlementDecision.no_clear_response,
            confidence=0.80,
            summary="Keine Entscheidung.",
        )
        mock_mongodb.update_settlement_response.return_value = True

        _process_second_round(
            db=db, email=email, email_id=4, matched_inquiry=inquiry,
            matching_result=matching, client_name="Test",
            client_aktenzeichen=None, creditor_email="test@example.de",
            creditor_name="Test", email_body="...", subject=None,
            confidence_result=confidence, route=route,
        )

        # Even with high confidence, no_clear_response → needs_review
        assert email.match_status == "needs_review"

    @patch("app.services.portal_notifier.notify_settlement_response")
    @patch("app.services.mongodb_client.mongodb_service")
    @patch("app.services.settlement_extractor.settlement_extractor")
    def test_mongodb_failure_sets_no_match(
        self, mock_extractor, mock_mongodb, mock_notify
    ):
        from app.actors.email_processor import _process_second_round

        db, email, inquiry, matching, confidence, route = self._make_mocks()

        mock_extractor.extract.return_value = SettlementExtractionResult(
            settlement_decision=SettlementDecision.accepted,
            confidence=0.95,
            summary="Zustimmung.",
        )
        mock_mongodb.update_settlement_response.return_value = False

        _process_second_round(
            db=db, email=email, email_id=5, matched_inquiry=inquiry,
            matching_result=matching, client_name="Test",
            client_aktenzeichen=None, creditor_email="test@example.de",
            creditor_name="Test", email_body="...", subject=None,
            confidence_result=confidence, route=route,
        )

        assert email.match_status == "no_match"

    @patch("app.services.portal_notifier.notify_settlement_response")
    @patch("app.services.mongodb_client.mongodb_service")
    @patch("app.services.settlement_extractor.settlement_extractor")
    def test_checkpoint_stored_in_agent_checkpoints(
        self, mock_extractor, mock_mongodb, mock_notify
    ):
        from app.actors.email_processor import _process_second_round

        db, email, inquiry, matching, confidence, route = self._make_mocks()

        mock_extractor.extract.return_value = SettlementExtractionResult(
            settlement_decision=SettlementDecision.declined,
            confidence=0.90,
            summary="Ablehnung.",
        )
        mock_mongodb.update_settlement_response.return_value = True

        _process_second_round(
            db=db, email=email, email_id=6, matched_inquiry=inquiry,
            matching_result=matching, client_name="Test",
            client_aktenzeichen=None, creditor_email="test@example.de",
            creditor_name="Test", email_body="...", subject=None,
            confidence_result=confidence, route=route,
        )

        checkpoint = email.agent_checkpoints["settlement_extraction"]
        assert checkpoint["settlement_decision"] == "declined"
        assert checkpoint["confidence"] == 0.90
        assert checkpoint["needs_review"] is False
