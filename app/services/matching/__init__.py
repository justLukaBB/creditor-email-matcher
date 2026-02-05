"""
Matching Engine Service Package

Provides signal scorers, matching strategies, threshold management,
and explainability builders for matching creditor answers to inquiries.
"""

from app.services.matching.signals import score_client_name, score_reference_numbers
from app.services.matching.explainability import ExplainabilityBuilder
from app.services.matching.thresholds import ThresholdManager
from app.services.matching.strategies import (
    MatchingStrategy,
    ExactMatchStrategy,
    FuzzyMatchStrategy,
    CombinedStrategy,
    StrategyResult,
)

__all__ = [
    # Signal scorers
    "score_client_name",
    "score_reference_numbers",
    # Explainability
    "ExplainabilityBuilder",
    # Threshold management
    "ThresholdManager",
    # Strategies
    "MatchingStrategy",
    "ExactMatchStrategy",
    "FuzzyMatchStrategy",
    "CombinedStrategy",
    "StrategyResult",
]
