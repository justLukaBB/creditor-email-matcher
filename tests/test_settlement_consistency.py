"""
Tests for validate_consistency() — cross-field sanity checks on
settlement extraction results.
"""

from decimal import Decimal

import pytest

from app.models.intent_classification import (
    SettlementDecision,
    SettlementExtractionResult,
)
from app.services.settlement_extractor import validate_consistency


def _result(
    decision: SettlementDecision,
    *,
    counter_offer_amount=None,
    conditions=None,
    confidence: float = 0.9,
) -> SettlementExtractionResult:
    return SettlementExtractionResult(
        settlement_decision=decision,
        counter_offer_amount=counter_offer_amount,
        conditions=conditions,
        confidence=confidence,
    )


class TestValidateConsistency:
    def test_clean_accepted_is_consistent(self):
        result = _result(SettlementDecision.accepted, conditions=None)
        inconsistent, warnings = validate_consistency(result, original_debt=5000.0)
        assert inconsistent is False
        assert warnings == []

    def test_clean_declined_is_consistent(self):
        result = _result(SettlementDecision.declined)
        inconsistent, warnings = validate_consistency(result, original_debt=5000.0)
        assert inconsistent is False
        assert warnings == []

    def test_counter_offer_with_valid_amount(self):
        result = _result(
            SettlementDecision.counter_offer,
            counter_offer_amount=3500.0,
        )
        inconsistent, warnings = validate_consistency(result, original_debt=5000.0)
        assert inconsistent is False
        assert warnings == []

    def test_counter_offer_without_amount_is_inconsistent(self):
        result = _result(SettlementDecision.counter_offer, counter_offer_amount=None)
        inconsistent, warnings = validate_consistency(result, original_debt=5000.0)
        assert inconsistent is True
        assert "counter_offer_without_amount" in warnings

    def test_accepted_with_conditional_phrasing_is_inconsistent(self):
        result = _result(
            SettlementDecision.accepted,
            conditions="Wir stimmen nur wenn Einmalzahlung erfolgt",
        )
        inconsistent, warnings = validate_consistency(result, original_debt=5000.0)
        assert inconsistent is True
        assert "accepted_with_conditional_phrasing" in warnings

    def test_accepted_with_neutral_conditions_is_consistent(self):
        result = _result(
            SettlementDecision.accepted,
            conditions="Bitte Aktenzeichen in Verwendungszweck angeben",
        )
        inconsistent, warnings = validate_consistency(result, original_debt=5000.0)
        assert inconsistent is False
        assert warnings == []

    def test_counter_offer_exceeds_2x_original(self):
        result = _result(
            SettlementDecision.counter_offer,
            counter_offer_amount=15000.0,
        )
        inconsistent, warnings = validate_consistency(result, original_debt=5000.0)
        assert inconsistent is True
        assert "counter_offer_exceeds_2x_original" in warnings

    def test_negative_counter_offer(self):
        result = _result(
            SettlementDecision.counter_offer,
            counter_offer_amount=-100.0,
        )
        inconsistent, warnings = validate_consistency(result, original_debt=5000.0)
        assert inconsistent is True
        assert "negative_counter_offer" in warnings

    def test_no_original_debt_skips_ratio_check(self):
        # Without original_debt, the >2x check must NOT trigger.
        result = _result(
            SettlementDecision.counter_offer,
            counter_offer_amount=999999.0,
        )
        inconsistent, warnings = validate_consistency(result, original_debt=None)
        assert "counter_offer_exceeds_2x_original" not in warnings
        # other checks pass — should be consistent
        assert inconsistent is False

    def test_decimal_original_debt_handled(self):
        # SQLAlchemy Numeric columns return Decimal; must coerce safely.
        result = _result(
            SettlementDecision.counter_offer,
            counter_offer_amount=3000.0,
        )
        inconsistent, warnings = validate_consistency(
            result, original_debt=Decimal("5000.00")
        )
        assert inconsistent is False
        assert warnings == []

    def test_multiple_warnings_collected(self):
        result = _result(
            SettlementDecision.accepted,
            counter_offer_amount=-50.0,
            conditions="nur wenn Einmalzahlung",
        )
        inconsistent, warnings = validate_consistency(result, original_debt=5000.0)
        assert inconsistent is True
        assert "accepted_with_conditional_phrasing" in warnings
        assert "negative_counter_offer" in warnings

    def test_zero_original_debt_skips_ratio_check(self):
        # Avoid divide-by-zero / nonsense ratio when debt is 0.
        result = _result(
            SettlementDecision.counter_offer,
            counter_offer_amount=100.0,
        )
        inconsistent, warnings = validate_consistency(result, original_debt=0.0)
        assert "counter_offer_exceeds_2x_original" not in warnings
        assert inconsistent is False
