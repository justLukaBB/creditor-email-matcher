"""
Manual Review Queue API Router
Provides REST endpoints for human review workflow
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from pydantic import BaseModel
import structlog

from app.database import get_db
from app.models.manual_review import ManualReviewQueue
from app.models.incoming_email import IncomingEmail

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/reviews", tags=["manual-review"])


class ClaimRequest(BaseModel):
    """Request body for claiming a review item"""
    reviewer: str  # Email or username of reviewer


class ResolveRequest(BaseModel):
    """Request body for resolving a review item"""
    resolution: str  # approved, rejected, corrected, escalated, spam
    notes: Optional[str] = None


@router.get("")
async def list_pending_reviews(
    priority_min: Optional[int] = Query(None, ge=1, le=10, description="Minimum priority (1=highest)"),
    priority_max: Optional[int] = Query(None, ge=1, le=10, description="Maximum priority (10=lowest)"),
    reason: Optional[str] = Query(None, description="Filter by review_reason"),
    claimed: Optional[bool] = Query(None, description="Filter claimed (true) or unclaimed (false)"),
    limit: int = Query(50, ge=1, le=200, description="Maximum items to return"),
    db: Session = Depends(get_db)
):
    """
    List pending review items

    Returns items sorted by:
    1. Priority (ascending: 1=highest, 10=lowest)
    2. Created time (ascending: oldest first)

    Args:
        priority_min: Filter items with priority >= this value
        priority_max: Filter items with priority <= this value
        reason: Filter by specific review_reason
        claimed: Filter by claim status (true=claimed, false=unclaimed, null=all)
        limit: Maximum number of items to return (1-200, default 50)
        db: Database session

    Returns:
        dict with total count and list of pending review items
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    # Build base query - only unresolved items
    query = db.query(ManualReviewQueue).filter(ManualReviewQueue.resolved_at.is_(None))

    # Apply filters
    if priority_min is not None:
        query = query.filter(ManualReviewQueue.priority >= priority_min)
    if priority_max is not None:
        query = query.filter(ManualReviewQueue.priority <= priority_max)
    if reason:
        query = query.filter(ManualReviewQueue.review_reason == reason)
    if claimed is not None:
        if claimed:
            query = query.filter(ManualReviewQueue.claimed_at.isnot(None))
        else:
            query = query.filter(ManualReviewQueue.claimed_at.is_(None))

    # Get total count
    total = query.count()

    # Get items ordered by priority, then created_at
    items = query.order_by(
        ManualReviewQueue.priority.asc(),
        ManualReviewQueue.created_at.asc()
    ).limit(limit).all()

    # Build response
    review_list = []
    for item in items:
        review_list.append({
            "id": item.id,
            "email_id": item.email_id,
            "review_reason": item.review_reason,
            "review_details": item.review_details,
            "priority": item.priority,
            "claimed_at": item.claimed_at.isoformat() if item.claimed_at else None,
            "claimed_by": item.claimed_by,
            "created_at": item.created_at.isoformat() if item.created_at else None
        })

    logger.info("reviews_listed", total=total, returned=len(review_list),
                priority_min=priority_min, priority_max=priority_max,
                reason=reason, claimed=claimed)

    return {
        "total": total,
        "reviews": review_list
    }


@router.get("/stats")
async def get_queue_stats(
    db: Session = Depends(get_db)
):
    """
    Get queue statistics

    Returns breakdown by:
    - Review reason
    - Priority level
    - Claim status
    - Resolution status

    Args:
        db: Database session

    Returns:
        dict with various queue statistics
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    # Total counts
    total_pending = db.query(ManualReviewQueue).filter(
        ManualReviewQueue.resolved_at.is_(None)
    ).count()

    total_claimed = db.query(ManualReviewQueue).filter(
        ManualReviewQueue.resolved_at.is_(None),
        ManualReviewQueue.claimed_at.isnot(None)
    ).count()

    total_resolved = db.query(ManualReviewQueue).filter(
        ManualReviewQueue.resolved_at.isnot(None)
    ).count()

    # Breakdown by reason (pending only)
    by_reason = db.query(
        ManualReviewQueue.review_reason,
        func.count(ManualReviewQueue.id).label('count')
    ).filter(
        ManualReviewQueue.resolved_at.is_(None)
    ).group_by(ManualReviewQueue.review_reason).all()

    reason_counts = {reason: count for reason, count in by_reason}

    # Breakdown by priority (pending only)
    by_priority = db.query(
        ManualReviewQueue.priority,
        func.count(ManualReviewQueue.id).label('count')
    ).filter(
        ManualReviewQueue.resolved_at.is_(None)
    ).group_by(ManualReviewQueue.priority).order_by(ManualReviewQueue.priority).all()

    priority_counts = {priority: count for priority, count in by_priority}

    logger.info("queue_stats_retrieved", total_pending=total_pending,
                total_claimed=total_claimed, total_resolved=total_resolved)

    return {
        "total_pending": total_pending,
        "total_claimed": total_claimed,
        "total_resolved": total_resolved,
        "by_reason": reason_counts,
        "by_priority": priority_counts
    }


@router.post("/{review_id}/claim")
async def claim_review(
    review_id: int,
    request: ClaimRequest,
    db: Session = Depends(get_db)
):
    """
    Claim a review item for processing

    Uses FOR UPDATE SKIP LOCKED to prevent concurrent claims.
    Only unclaimed items can be claimed.

    Args:
        review_id: ManualReviewQueue ID
        request: Claim request with reviewer identifier
        db: Database session

    Returns:
        Updated review item

    Raises:
        404: Review item not found
        409: Item already claimed or resolved
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    # Use FOR UPDATE SKIP LOCKED for concurrency control
    item = db.query(ManualReviewQueue).filter(
        ManualReviewQueue.id == review_id
    ).with_for_update(skip_locked=True).first()

    if not item:
        raise HTTPException(status_code=404, detail="Review item not found or already locked")

    # Check if already claimed
    if item.claimed_at is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Item already claimed by {item.claimed_by} at {item.claimed_at.isoformat()}"
        )

    # Check if already resolved
    if item.resolved_at is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Item already resolved with status: {item.resolution}"
        )

    # Claim the item
    item.claimed_at = func.now()
    item.claimed_by = request.reviewer
    db.commit()
    db.refresh(item)

    logger.info("review_claimed", review_id=review_id, claimed_by=request.reviewer)

    return {
        "id": item.id,
        "email_id": item.email_id,
        "review_reason": item.review_reason,
        "review_details": item.review_details,
        "priority": item.priority,
        "claimed_at": item.claimed_at.isoformat() if item.claimed_at else None,
        "claimed_by": item.claimed_by
    }


@router.post("/claim-next")
async def claim_next_review(
    request: ClaimRequest,
    priority_max: Optional[int] = Query(None, ge=1, le=10, description="Only claim items with priority <= this"),
    db: Session = Depends(get_db)
):
    """
    Claim the next available unclaimed review item

    Uses FOR UPDATE SKIP LOCKED to prevent concurrent claims.
    Returns highest priority (lowest number) unclaimed item.

    Args:
        request: Claim request with reviewer identifier
        priority_max: Only claim items with priority <= this value
        db: Database session

    Returns:
        Claimed review item

    Raises:
        404: No unclaimed items available
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    # Build query for unclaimed, unresolved items
    query = db.query(ManualReviewQueue).filter(
        ManualReviewQueue.resolved_at.is_(None),
        ManualReviewQueue.claimed_at.is_(None)
    )

    # Apply priority filter if provided
    if priority_max is not None:
        query = query.filter(ManualReviewQueue.priority <= priority_max)

    # Get highest priority item with FOR UPDATE SKIP LOCKED
    item = query.order_by(
        ManualReviewQueue.priority.asc(),
        ManualReviewQueue.created_at.asc()
    ).with_for_update(skip_locked=True).first()

    if not item:
        raise HTTPException(status_code=404, detail="No unclaimed review items available")

    # Claim the item
    item.claimed_at = func.now()
    item.claimed_by = request.reviewer
    db.commit()
    db.refresh(item)

    logger.info("next_review_claimed", review_id=item.id, claimed_by=request.reviewer,
                priority=item.priority, reason=item.review_reason)

    return {
        "id": item.id,
        "email_id": item.email_id,
        "review_reason": item.review_reason,
        "review_details": item.review_details,
        "priority": item.priority,
        "claimed_at": item.claimed_at.isoformat() if item.claimed_at else None,
        "claimed_by": item.claimed_by
    }


@router.post("/{review_id}/resolve")
async def resolve_review(
    review_id: int,
    request: ResolveRequest,
    db: Session = Depends(get_db)
):
    """
    Resolve a review item

    Only claimed items can be resolved.
    Valid resolutions: approved, rejected, corrected, escalated, spam

    Args:
        review_id: ManualReviewQueue ID
        request: Resolution details
        db: Database session

    Returns:
        Resolved review item

    Raises:
        404: Review item not found
        400: Invalid resolution status or item not claimed
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    item = db.query(ManualReviewQueue).filter(ManualReviewQueue.id == review_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Review item not found")

    # Validate resolution status
    valid_resolutions = ["approved", "rejected", "corrected", "escalated", "spam"]
    if request.resolution not in valid_resolutions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid resolution. Must be one of: {', '.join(valid_resolutions)}"
        )

    # Check if already resolved
    if item.resolved_at is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Item already resolved at {item.resolved_at.isoformat()} with status: {item.resolution}"
        )

    # Resolve the item
    item.resolved_at = func.now()
    item.resolution = request.resolution
    item.resolution_notes = request.notes
    db.commit()
    db.refresh(item)

    logger.info("review_resolved", review_id=review_id, resolution=request.resolution,
                claimed_by=item.claimed_by, reason=item.review_reason)

    return {
        "id": item.id,
        "email_id": item.email_id,
        "review_reason": item.review_reason,
        "priority": item.priority,
        "claimed_at": item.claimed_at.isoformat() if item.claimed_at else None,
        "claimed_by": item.claimed_by,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "resolution": item.resolution,
        "resolution_notes": item.resolution_notes
    }


@router.get("/{review_id}/email")
async def get_review_email_details(
    review_id: int,
    db: Session = Depends(get_db)
):
    """
    Get email details for a review item

    Returns full email information including extracted data
    to help reviewer make decisions.

    Args:
        review_id: ManualReviewQueue ID
        db: Database session

    Returns:
        Combined review item and email details

    Raises:
        404: Review item or email not found
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    # Get review item
    item = db.query(ManualReviewQueue).filter(ManualReviewQueue.id == review_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Review item not found")

    # Get associated email
    email = db.query(IncomingEmail).filter(IncomingEmail.id == item.email_id).first()

    if not email:
        raise HTTPException(status_code=404, detail="Associated email not found")

    logger.info("review_email_details_retrieved", review_id=review_id, email_id=email.id)

    return {
        "review": {
            "id": item.id,
            "review_reason": item.review_reason,
            "review_details": item.review_details,
            "priority": item.priority,
            "claimed_at": item.claimed_at.isoformat() if item.claimed_at else None,
            "claimed_by": item.claimed_by,
            "created_at": item.created_at.isoformat() if item.created_at else None
        },
        "email": {
            "id": email.id,
            "from_email": email.from_email,
            "from_name": email.from_name,
            "subject": email.subject,
            "cleaned_body": email.cleaned_body,
            "extracted_data": email.extracted_data,
            "agent_checkpoints": email.agent_checkpoints,
            "processing_status": email.processing_status,
            "match_status": email.match_status,
            "match_confidence": email.match_confidence,
            "received_at": email.received_at.isoformat() if email.received_at else None,
            "attachment_urls": email.attachment_urls
        }
    }
