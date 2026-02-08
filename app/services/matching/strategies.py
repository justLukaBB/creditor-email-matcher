"""
Matching Strategy Implementations

Implements REQ-MATCH-05: Multiple matching strategies (exact, fuzzy, combined).

Design decisions from CONTEXT.MD:
- Both name AND reference signals required for match
- Combined strategy recommended for production (exact first, fuzzy fallback)
- Signal scorers from signals.py provide core matching logic
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional, Any, TYPE_CHECKING
from dataclasses import dataclass
import structlog

from app.services.matching.signals import score_client_name, score_reference_numbers

if TYPE_CHECKING:
    from app.models.creditor_inquiry import CreditorInquiry

logger = structlog.get_logger(__name__)


@dataclass
class StrategyResult:
    """Result from a matching strategy evaluation."""
    score: float  # 0.0 to 1.0
    component_scores: Dict[str, float]  # Raw scores before weighting
    signal_details: Dict[str, Dict]  # Details from signal scorers
    strategy_used: str  # "exact", "fuzzy", "combined"


class MatchingStrategy(ABC):
    """Base class for matching strategies (REQ-MATCH-05)."""

    @abstractmethod
    def evaluate(
        self,
        inquiry: "CreditorInquiry",
        extracted_data: Dict[str, Any],
        weights: Dict[str, float]
    ) -> StrategyResult:
        """
        Evaluate match between inquiry and extracted data.

        Args:
            inquiry: CreditorInquiry object from database
            extracted_data: Extracted data from creditor answer
            weights: Signal weights (e.g., {"client_name": 0.4, "reference_number": 0.6})

        Returns:
            StrategyResult with score and scoring details
        """
        pass


class ExactMatchStrategy(MatchingStrategy):
    """
    Exact matching strategy.
    Returns 1.0 only if both name AND reference match exactly (case-insensitive).
    """

    def evaluate(
        self,
        inquiry: "CreditorInquiry",
        extracted_data: Dict[str, Any],
        weights: Dict[str, float]
    ) -> StrategyResult:
        extracted_name = extracted_data.get("client_name", "")
        extracted_refs = extracted_data.get("reference_numbers", [])
        inquiry_ref = inquiry.reference_number or ""

        # Exact name match (case-insensitive, normalized)
        inquiry_name = (inquiry.client_name_normalized or inquiry.client_name or "").lower().strip()
        name_match = extracted_name.lower().strip() == inquiry_name if extracted_name else False

        # Exact reference match
        ref_match = any(
            ref.lower().strip() == inquiry_ref.lower().strip()
            for ref in extracted_refs
        ) if extracted_refs and inquiry_ref else False

        # CONTEXT.MD: Both signals required
        if name_match and ref_match:
            score = 1.0
        elif name_match or ref_match:
            score = 0.5  # Partial match
        else:
            score = 0.0

        return StrategyResult(
            score=score,
            component_scores={
                "client_name": 1.0 if name_match else 0.0,
                "reference": 1.0 if ref_match else 0.0
            },
            signal_details={
                "client_name": {"algorithm": "exact", "matched": name_match},
                "reference": {"algorithm": "exact", "matched": ref_match}
            },
            strategy_used="exact"
        )


class FuzzyMatchStrategy(MatchingStrategy):
    """
    Fuzzy matching strategy using RapidFuzz.
    Uses signal scorers from signals.py for sophisticated fuzzy matching.
    """

    def evaluate(
        self,
        inquiry: "CreditorInquiry",
        extracted_data: Dict[str, Any],
        weights: Dict[str, float]
    ) -> StrategyResult:
        extracted_name = extracted_data.get("client_name")
        extracted_refs = extracted_data.get("reference_numbers", [])

        # Score name using signal scorer
        name_score, name_details = score_client_name(
            inquiry.client_name,
            inquiry.client_name_normalized,
            extracted_name
        )

        # Score reference using signal scorer
        ref_score, ref_details = score_reference_numbers(
            inquiry.reference_number,
            extracted_refs
        )

        # Matching logic:
        # 1. If name_score is 0, no match possible
        # 2. If name_score is very high (>= 0.85), allow name-only matching
        #    (creditor's reference number often not known at inquiry creation)
        # 3. Otherwise, use weighted average requiring both signals
        if name_score == 0:
            total_score = 0.0
        elif name_score >= 0.85 and ref_score == 0:
            # Strong name match without reference - allow with reduced confidence
            # This handles cases where creditor's Aktenzeichen wasn't known initially
            total_score = name_score * 0.7  # Penalty for missing reference
            logger.debug("name_only_match_allowed",
                        name_score=name_score,
                        ref_score=ref_score,
                        total_score=total_score)
        elif ref_score == 0:
            # Weak name match without reference - no match
            total_score = 0.0
        else:
            # Both signals available - weighted average
            name_weight = weights.get("client_name", 0.4)
            ref_weight = weights.get("reference_number", 0.6)
            total_score = (name_score * name_weight) + (ref_score * ref_weight)

        return StrategyResult(
            score=total_score,
            component_scores={
                "client_name": name_score,
                "reference": ref_score
            },
            signal_details={
                "client_name": name_details,
                "reference": ref_details
            },
            strategy_used="fuzzy"
        )


class CombinedStrategy(MatchingStrategy):
    """
    Combined strategy: try exact first, fall back to fuzzy.
    Provides best of both: fast exact matches, robust fuzzy fallback.
    """

    def __init__(self):
        self.exact = ExactMatchStrategy()
        self.fuzzy = FuzzyMatchStrategy()

    def evaluate(
        self,
        inquiry: "CreditorInquiry",
        extracted_data: Dict[str, Any],
        weights: Dict[str, float]
    ) -> StrategyResult:
        # Try exact match first
        exact_result = self.exact.evaluate(inquiry, extracted_data, weights)

        if exact_result.score == 1.0:
            logger.debug("combined_strategy_exact_match",
                        inquiry_id=inquiry.id,
                        score=exact_result.score)
            exact_result.strategy_used = "combined_exact"
            return exact_result

        # Fall back to fuzzy
        fuzzy_result = self.fuzzy.evaluate(inquiry, extracted_data, weights)
        fuzzy_result.strategy_used = "combined_fuzzy"

        logger.debug("combined_strategy_fuzzy_fallback",
                    inquiry_id=inquiry.id,
                    exact_score=exact_result.score,
                    fuzzy_score=fuzzy_result.score)

        return fuzzy_result
