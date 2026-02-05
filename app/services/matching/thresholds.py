"""
ThresholdManager for runtime threshold lookup.

Provides database-driven threshold configuration with category-based overrides.
Enables runtime threshold tuning without code deployment.

Design from CONTEXT.MD:
- Thresholds stored in PostgreSQL for runtime changes
- Developers manage via direct database access (no admin API needed)
- Fallback to hardcoded defaults if database empty
"""

from typing import Dict, Optional
from sqlalchemy.orm import Session
import structlog

from app.models.matching_config import MatchingThreshold

logger = structlog.get_logger(__name__)


class ThresholdManager:
    """
    Runtime threshold lookup with category-based overrides.

    CONTEXT.MD: Thresholds stored in PostgreSQL for runtime changes without deployment.
    Developers manage via direct database access (no admin API needed).
    """

    # Hardcoded fallbacks if database has no config
    DEFAULT_MIN_MATCH = 0.70
    DEFAULT_GAP_THRESHOLD = 0.15
    DEFAULT_WEIGHTS = {"client_name": 0.40, "reference_number": 0.60}

    def __init__(self, db: Session):
        self.db = db
        self._cache: Dict[str, any] = {}  # Optional performance cache

    def get_threshold(self, creditor_category: str, threshold_type: str) -> float:
        """
        Get threshold with category override fallback to default.

        Args:
            creditor_category: "bank", "inkasso", "agency", etc.
            threshold_type: "min_match", "gap_threshold"

        Returns:
            Threshold value (0.0-1.0)
        """
        # Try category-specific first
        threshold = self.db.query(MatchingThreshold).filter(
            MatchingThreshold.category == creditor_category,
            MatchingThreshold.threshold_type == threshold_type
        ).first()

        if threshold:
            logger.debug("threshold_found_category",
                        category=creditor_category,
                        threshold_type=threshold_type,
                        value=float(threshold.threshold_value))
            return float(threshold.threshold_value)

        # Fallback to default category
        default = self.db.query(MatchingThreshold).filter(
            MatchingThreshold.category == "default",
            MatchingThreshold.threshold_type == threshold_type
        ).first()

        if default:
            logger.debug("threshold_fallback_default",
                        category=creditor_category,
                        threshold_type=threshold_type,
                        value=float(default.threshold_value))
            return float(default.threshold_value)

        # Hardcoded fallback if database empty
        hardcoded = self.DEFAULT_MIN_MATCH if threshold_type == "min_match" else self.DEFAULT_GAP_THRESHOLD
        logger.warning("threshold_hardcoded_fallback",
                      category=creditor_category,
                      threshold_type=threshold_type,
                      value=hardcoded)
        return hardcoded

    def get_weights(self, creditor_category: str) -> Dict[str, float]:
        """
        Get signal weights for category.

        CONTEXT.MD: 40% name, 60% reference suggested for default.

        Returns:
            Dict with weight_name -> weight_value
        """
        weights = self.db.query(MatchingThreshold).filter(
            MatchingThreshold.category == creditor_category,
            MatchingThreshold.weight_name.isnot(None)
        ).all()

        if not weights:
            # Fallback to default category
            weights = self.db.query(MatchingThreshold).filter(
                MatchingThreshold.category == "default",
                MatchingThreshold.weight_name.isnot(None)
            ).all()

        if weights:
            result = {w.weight_name: float(w.weight_value) for w in weights}
            logger.debug("weights_loaded", category=creditor_category, weights=result)
            return result

        # Hardcoded fallback
        logger.warning("weights_hardcoded_fallback", category=creditor_category)
        return self.DEFAULT_WEIGHTS.copy()

    def get_min_match(self, creditor_category: str = "default") -> float:
        """Convenience method for min_match threshold."""
        return self.get_threshold(creditor_category, "min_match")

    def get_gap_threshold(self, creditor_category: str = "default") -> float:
        """Convenience method for gap_threshold."""
        return self.get_threshold(creditor_category, "gap_threshold")
