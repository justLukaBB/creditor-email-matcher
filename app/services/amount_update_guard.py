"""
Amount Update Guard

Prevents silent data downgrades by checking whether a newly extracted amount
should overwrite the existing database value.

The guard enforces three safety rules:
1. Never write None amounts to the database
2. Never write low-confidence extractions
3. Never downgrade an existing amount to a lower value
"""

from typing import Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

CONFIDENCE_THRESHOLD = 0.75


def should_update_amount(
    existing_amount: Optional[float],
    new_amount: Optional[float],
    confidence: float,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> Tuple[bool, str]:
    """
    Determine whether a new extracted amount should overwrite the existing DB value.

    We only write when the extraction is clearly better than what we already have.
    This prevents silent data downgrades from low-quality extractions or emails
    without clear amounts.

    Args:
        existing_amount: Current amount in the database (None if no existing value)
        new_amount: Newly extracted amount (None if extraction found nothing)
        confidence: Extraction confidence as a float (0.0 - 1.0)
        confidence_threshold: Minimum confidence required to write

    Returns:
        Tuple of (should_update: bool, reason: str)
    """
    if new_amount is None:
        decision, reason = False, "extraction_returned_none"
    elif confidence < confidence_threshold:
        decision, reason = False, "low_extraction_confidence"
    elif existing_amount is not None and new_amount < existing_amount:
        decision, reason = False, "amount_downgrade_prevented"
    else:
        decision, reason = True, "amount_update_approved"

    logger.info(
        "amount_update_guard",
        existing_amount=existing_amount,
        new_amount=new_amount,
        confidence=confidence,
        decision="UPDATE" if decision else "SKIP",
        reason=reason,
    )

    return decision, reason
