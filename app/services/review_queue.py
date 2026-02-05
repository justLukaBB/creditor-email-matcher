"""
Review Queue Service for Matching Engine
Helper function for enqueueing ambiguous matches to ManualReviewQueue
"""

from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
import structlog

from app.models.manual_review import ManualReviewQueue
from app.services.validation.review_queue import get_priority_for_reason

logger = structlog.get_logger(__name__)


# Priority mapping for matching-related review reasons
MATCHING_PRIORITY_MAP = {
    "ambiguous_match": 3,          # High - multiple candidates with similar scores
    "no_recent_inquiry": 4,        # Medium-high - no inquiry sent in last 30 days
    "below_threshold": 5,          # Medium - top candidate below minimum threshold
}


def enqueue_ambiguous_match(
    db: Session,
    email_id: int,
    matching_result: Any
) -> Optional[int]:
    """
    Enqueue an ambiguous match result to the manual review queue.

    This function is specialized for matching engine results that need human review.
    It builds rich context for the reviewer including:
    - Top 3 candidates with scores and signal breakdown
    - Gap analysis (how close are top candidates)
    - Match status explanation
    - Review instructions based on status

    Args:
        db: Database session
        email_id: IncomingEmail ID
        matching_result: MatchingResult from MatchingEngineV2

    Returns:
        ManualReviewQueue.id if created, None if duplicate skipped

    Raises:
        ValueError: If email_id doesn't exist
    """
    from app.models.incoming_email import IncomingEmail

    # Verify email exists
    email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()
    if not email:
        raise ValueError(f"Email ID {email_id} not found")

    # Check for duplicate - skip if unresolved item exists for same email
    existing = db.query(ManualReviewQueue).filter(
        ManualReviewQueue.email_id == email_id,
        ManualReviewQueue.resolved_at.is_(None)
    ).first()

    if existing:
        logger.info("review_queue_duplicate_skipped",
                    email_id=email_id,
                    existing_review_id=existing.id,
                    reason="ambiguous_match")
        return None

    # Build candidate details for reviewer (top 3 with signal breakdown)
    top_candidates = []
    for i, candidate in enumerate(matching_result.candidates[:3], 1):
        inquiry = candidate.inquiry
        candidate_info = {
            "rank": i,
            "inquiry_id": inquiry.id,
            "client_name": inquiry.client_name,
            "reference_number": inquiry.reference_number,
            "sent_at": inquiry.sent_at.isoformat() if inquiry.sent_at else None,
            "total_score": round(candidate.total_score, 4),
            "confidence_level": candidate.confidence_level,
            "component_scores": {
                "client_name": round(candidate.component_scores.get("client_name", 0), 4),
                "reference": round(candidate.component_scores.get("reference", 0), 4),
            },
            "signal_details": candidate.signal_details,
            "strategy_used": candidate.strategy_used,
        }
        top_candidates.append(candidate_info)

    # Build review_details dict with all context
    review_details = {
        "match_status": matching_result.status,
        "gap": round(matching_result.gap, 4) if matching_result.gap is not None else None,
        "gap_threshold": matching_result.gap_threshold,
        "top_candidates": top_candidates,
        "review_reason": matching_result.review_reason,
    }

    # Add status-specific instructions
    if matching_result.status == "ambiguous":
        review_details["instructions"] = (
            f"Top {len(top_candidates)} candidates have similar scores "
            f"(gap: {matching_result.gap:.3f} < threshold: {matching_result.gap_threshold}). "
            "Review signal breakdown and select the correct match."
        )
    elif matching_result.status == "no_recent_inquiry":
        review_details["instructions"] = (
            "No creditor inquiry sent in last 30 days. "
            "This email may be unsolicited or from a different timeframe. "
            "Verify sender and context before matching."
        )
    elif matching_result.status == "below_threshold":
        top_score = matching_result.candidates[0].total_score if matching_result.candidates else 0
        review_details["instructions"] = (
            f"Top candidate score ({top_score:.3f}) below minimum threshold. "
            "Review extraction quality and candidate details. "
            "May need manual data correction or new inquiry creation."
        )
    else:
        review_details["instructions"] = "Review match candidates and select correct match or escalate."

    # Determine priority based on status
    priority = MATCHING_PRIORITY_MAP.get(matching_result.status, 5)

    # Create review item
    review_item = ManualReviewQueue(
        email_id=email_id,
        review_reason=matching_result.status,  # ambiguous_match, no_recent_inquiry, below_threshold
        review_details=review_details,
        priority=priority
    )

    db.add(review_item)
    db.flush()  # Get ID without committing (caller controls transaction)

    logger.info("ambiguous_match_enqueued",
                review_id=review_item.id,
                email_id=email_id,
                status=matching_result.status,
                candidates_count=len(top_candidates),
                priority=priority)

    return review_item.id


__all__ = ["enqueue_ambiguous_match"]
