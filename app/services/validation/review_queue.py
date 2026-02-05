"""
Manual Review Queue Service
Helper functions to enqueue items for human review
"""

from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
import structlog

from app.models.manual_review import ManualReviewQueue
from app.models.incoming_email import IncomingEmail

logger = structlog.get_logger()


# Priority mapping by review reason
PRIORITY_MAP = {
    "manual_escalation": 1,      # Highest priority - explicitly escalated
    "validation_failed": 2,       # Critical - validation errors
    "conflict_detected": 3,       # High - data conflicts found
    "extraction_error": 4,        # Medium-high - extraction issues
    "low_confidence": 5,          # Medium - confidence below threshold
    "missing_data": 6,            # Medium-low - required fields missing
    "duplicate_suspected": 7,     # Low - possible duplicate
    "default": 5                  # Default to medium priority
}


def get_priority_for_reason(reason: str) -> int:
    """
    Get priority level for a review reason

    Args:
        reason: Review reason string

    Returns:
        Priority level (1=highest, 10=lowest)
    """
    return PRIORITY_MAP.get(reason, PRIORITY_MAP["default"])


def enqueue_for_review(
    db: Session,
    email_id: int,
    reason: str,
    details: Optional[Dict[str, Any]] = None,
    priority: Optional[int] = None,
    expiration_days: Optional[int] = None
) -> ManualReviewQueue:
    """
    Add an item to the manual review queue

    Checks for duplicates to avoid re-adding the same email multiple times.
    If item already exists and is unresolved, returns existing item.

    Args:
        db: Database session
        email_id: IncomingEmail ID to review
        reason: Review reason (low_confidence, conflict_detected, etc.)
        details: Optional JSONB details for reviewer context
        priority: Optional explicit priority (1-10), defaults to reason-based priority
        expiration_days: Optional days until item expires (for low-confidence routing)

    Returns:
        ManualReviewQueue item (new or existing)

    Raises:
        ValueError: If email_id doesn't exist
    """
    # Verify email exists
    email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()
    if not email:
        raise ValueError(f"Email ID {email_id} not found")

    # Check for duplicate - existing unresolved item for this email
    existing = db.query(ManualReviewQueue).filter(
        ManualReviewQueue.email_id == email_id,
        ManualReviewQueue.resolved_at.is_(None)
    ).first()

    if existing:
        logger.info("review_queue_duplicate_skipped",
                    email_id=email_id,
                    existing_review_id=existing.id,
                    reason=reason)
        return existing

    # Determine priority
    if priority is None:
        priority = get_priority_for_reason(reason)

    # Add expiration info to details if provided
    if expiration_days is not None:
        if details is None:
            details = {}
        details["expiration_days"] = expiration_days

    # Create new review item
    review_item = ManualReviewQueue(
        email_id=email_id,
        review_reason=reason,
        review_details=details,
        priority=priority
    )

    db.add(review_item)
    db.commit()
    db.refresh(review_item)

    logger.info("review_queue_item_added",
                review_id=review_item.id,
                email_id=email_id,
                reason=reason,
                priority=priority,
                expiration_days=expiration_days)

    return review_item


def bulk_enqueue_for_review(
    db: Session,
    items: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Add multiple items to the review queue in bulk

    Efficient batch insertion with duplicate detection.

    Args:
        db: Database session
        items: List of dicts with keys:
            - email_id: int (required)
            - reason: str (required)
            - details: dict (optional)
            - priority: int (optional)

    Returns:
        dict with summary:
            - added: count of new items added
            - skipped: count of duplicates skipped
            - failed: count of failures
            - review_ids: list of IDs created
    """
    added = 0
    skipped = 0
    failed = 0
    review_ids = []

    for item in items:
        try:
            email_id = item.get("email_id")
            reason = item.get("reason")

            if not email_id or not reason:
                logger.warning("bulk_enqueue_missing_required_fields", item=item)
                failed += 1
                continue

            review_item = enqueue_for_review(
                db=db,
                email_id=email_id,
                reason=reason,
                details=item.get("details"),
                priority=item.get("priority")
            )

            # Check if this was a duplicate (ID would already exist in review_ids)
            if review_item.id in review_ids:
                skipped += 1
            else:
                added += 1
                review_ids.append(review_item.id)

        except ValueError as e:
            logger.warning("bulk_enqueue_item_failed", item=item, error=str(e))
            failed += 1
        except Exception as e:
            logger.error("bulk_enqueue_unexpected_error", item=item, error=str(e))
            failed += 1

    logger.info("bulk_enqueue_completed",
                total=len(items),
                added=added,
                skipped=skipped,
                failed=failed)

    return {
        "total": len(items),
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "review_ids": review_ids
    }


def enqueue_low_confidence_items(
    db: Session,
    confidence_threshold: float = 0.7,
    lookback_hours: int = 24
) -> Dict[str, Any]:
    """
    Automatically enqueue items with low confidence from recent emails

    Useful for batch processing of items that need review.

    Args:
        db: Database session
        confidence_threshold: Confidence below this triggers review (default 0.7)
        lookback_hours: How far back to check (default 24 hours)

    Returns:
        dict with summary of items enqueued
    """
    from datetime import datetime, timedelta

    since = datetime.utcnow() - timedelta(hours=lookback_hours)

    # Find emails with needs_review flag or low confidence
    candidates = db.query(IncomingEmail).filter(
        IncomingEmail.received_at >= since,
        IncomingEmail.processing_status == "completed"
    ).all()

    items_to_enqueue = []

    for email in candidates:
        # Check agent_checkpoints for confidence issues
        if email.agent_checkpoints:
            # Check if needs_review flag is set anywhere
            needs_review = False
            low_conf_agent = None
            min_confidence = 1.0

            for agent_name, checkpoint in email.agent_checkpoints.items():
                if checkpoint.get("validation_status") == "needs_review":
                    needs_review = True

                conf = checkpoint.get("confidence", 1.0)
                if conf < min_confidence:
                    min_confidence = conf
                    low_conf_agent = agent_name

            if needs_review or min_confidence < confidence_threshold:
                items_to_enqueue.append({
                    "email_id": email.id,
                    "reason": "low_confidence",
                    "details": {
                        "confidence": min_confidence,
                        "agent": low_conf_agent,
                        "threshold": confidence_threshold,
                        "needs_review_flag": needs_review
                    }
                })

    # Bulk enqueue
    if items_to_enqueue:
        result = bulk_enqueue_for_review(db, items_to_enqueue)
        logger.info("auto_enqueue_low_confidence_completed",
                    lookback_hours=lookback_hours,
                    threshold=confidence_threshold,
                    **result)
        return result
    else:
        logger.info("auto_enqueue_no_items_found",
                    lookback_hours=lookback_hours,
                    threshold=confidence_threshold)
        return {
            "total": 0,
            "added": 0,
            "skipped": 0,
            "failed": 0,
            "review_ids": []
        }
