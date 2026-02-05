"""
Confidence-Based Router
Routes emails to appropriate handling based on confidence thresholds

REQ-CONFIDENCE-03: Confidence-based routing
- High (>0.85): auto-update database, log only, no notification
- Medium (0.6-0.85): write to database, notify review team for verification
- Low (<0.6): route to manual review queue, 7-day expiration
"""

from enum import Enum
from typing import Optional
from dataclasses import dataclass
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


class ConfidenceLevel(str, Enum):
    """Confidence tier for routing decisions"""
    HIGH = "high"      # > 0.85 (configurable)
    MEDIUM = "medium"  # 0.6 - 0.85
    LOW = "low"        # < 0.6


class RoutingAction(str, Enum):
    """What action to take based on confidence"""
    AUTO_UPDATE = "auto_update"           # HIGH: write + log only
    UPDATE_AND_NOTIFY = "update_and_notify"  # MEDIUM: write + notify review team
    MANUAL_REVIEW = "manual_review"       # LOW: enqueue for human review


@dataclass
class ConfidenceRoute:
    """Routing decision with full context"""
    level: ConfidenceLevel
    action: RoutingAction
    confidence: float
    high_threshold: float
    low_threshold: float
    reason: str  # Human-readable explanation


def route_by_confidence(
    overall_confidence: float,
    high_threshold: Optional[float] = None,
    low_threshold: Optional[float] = None
) -> ConfidenceRoute:
    """
    Determine routing based on overall confidence score.

    USER DECISIONS from CONTEXT.md:
    - HIGH (>0.85): Write to database + log entry only, no notification
    - MEDIUM (0.6-0.85): Write immediately to database, notify dedicated review team
    - LOW (<0.6): Route to manual review queue, expire after 7 days

    Args:
        overall_confidence: Combined confidence score 0.0-1.0
        high_threshold: Override for high threshold (default from settings)
        low_threshold: Override for low threshold (default from settings)

    Returns:
        ConfidenceRoute with level, action, and context
    """
    # Use settings thresholds or overrides
    high_t = high_threshold if high_threshold is not None else settings.confidence_high_threshold
    low_t = low_threshold if low_threshold is not None else settings.confidence_low_threshold

    # Determine confidence level
    if overall_confidence >= high_t:
        level = ConfidenceLevel.HIGH
        action = RoutingAction.AUTO_UPDATE
        reason = f"Confidence {overall_confidence:.2f} >= {high_t:.2f} (high threshold)"
    elif overall_confidence >= low_t:
        level = ConfidenceLevel.MEDIUM
        action = RoutingAction.UPDATE_AND_NOTIFY
        reason = f"Confidence {overall_confidence:.2f} between {low_t:.2f} and {high_t:.2f}"
    else:
        level = ConfidenceLevel.LOW
        action = RoutingAction.MANUAL_REVIEW
        reason = f"Confidence {overall_confidence:.2f} < {low_t:.2f} (low threshold)"

    route = ConfidenceRoute(
        level=level,
        action=action,
        confidence=overall_confidence,
        high_threshold=high_t,
        low_threshold=low_t,
        reason=reason
    )

    logger.info(
        "confidence_route_determined",
        level=route.level.value,
        action=route.action.value,
        confidence=overall_confidence,
        high_threshold=high_t,
        low_threshold=low_t
    )

    return route


def get_review_expiration_days(level: ConfidenceLevel) -> Optional[int]:
    """
    Get expiration days for manual review items.

    USER DECISION: Low-confidence items expire after 7 days if not processed,
    then flag for batch review.

    Args:
        level: Confidence level

    Returns:
        Days until expiration, or None if no expiration
    """
    if level == ConfidenceLevel.LOW:
        return 7  # USER DECISION: 7-day expiration for low confidence
    return None  # MEDIUM and HIGH don't expire in queue
