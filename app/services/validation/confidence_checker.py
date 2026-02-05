"""
Confidence Checker
Validates confidence scores against thresholds for review flagging
"""

from typing import Dict, Any
import structlog

logger = structlog.get_logger(__name__)


def check_confidence_threshold(confidence: float, threshold: float = 0.7) -> Dict[str, Any]:
    """
    Check if confidence meets threshold.
    USER DECISION: < 0.7 = needs_review flag, don't block pipeline.

    Confidence below threshold doesn't stop processing - it flags the item
    for manual review while allowing the pipeline to continue.

    Args:
        confidence: Confidence score from 0.0 to 1.0
        threshold: Minimum acceptable confidence (default 0.7)

    Returns:
        {
            "passes": bool,  # True if confidence >= threshold
            "needs_review": bool,  # True if confidence < threshold
            "confidence": float,  # Original confidence value
            "threshold": float  # Threshold used for comparison
        }

    Example:
        >>> check_confidence_threshold(0.85)
        {"passes": True, "needs_review": False, "confidence": 0.85, "threshold": 0.7}

        >>> check_confidence_threshold(0.5)
        {"passes": False, "needs_review": True, "confidence": 0.5, "threshold": 0.7}
    """
    passes = confidence >= threshold
    needs_review = not passes

    if needs_review:
        logger.warning(
            "confidence_below_threshold",
            confidence=confidence,
            threshold=threshold,
            difference=threshold - confidence
        )
    else:
        logger.info(
            "confidence_meets_threshold",
            confidence=confidence,
            threshold=threshold
        )

    return {
        "passes": passes,
        "needs_review": needs_review,
        "confidence": confidence,
        "threshold": threshold
    }
