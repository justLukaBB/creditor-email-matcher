"""
ManualReviewQueue Model
Stores items flagged for human review with low confidence or detected conflicts
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.database import Base


class ManualReviewQueue(Base):
    """
    Represents an item requiring human review

    Items are added when:
    - Low confidence extraction results (< 0.7)
    - Conflicts detected during validation
    - Manual escalation from processing pipeline

    Claim tracking with FOR UPDATE SKIP LOCKED ensures concurrent reviewers
    don't conflict.
    """
    __tablename__ = "manual_review_queue"

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Reference to email
    email_id = Column(Integer, ForeignKey("incoming_emails.id"), nullable=False, index=True)

    # Review metadata
    review_reason = Column(String(100), nullable=False)
    # Examples: low_confidence, conflict_detected, validation_failed, manual_escalation

    review_details = Column(JSONB, nullable=True)
    """
    Additional context for reviewers.

    Structure:
    {
        "confidence": 0.45,
        "conflicts": ["amount_mismatch", "name_mismatch"],
        "extracted_values": {
            "amount": 1500.0,
            "creditor_name": "Sparkasse"
        },
        "validation_errors": ["postal_code_invalid"]
    }
    """

    priority = Column(Integer, default=5, nullable=False)
    # Priority levels: 1 (highest) to 10 (lowest), default 5 (medium)
    # Priority mapping: low_confidence=5, conflict_detected=3, validation_failed=4, manual_escalation=1

    # Claim tracking (FOR UPDATE SKIP LOCKED concurrency)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    claimed_by = Column(String(255), nullable=True)  # Reviewer email/username

    # Resolution tracking
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution = Column(String(50), nullable=True)
    # Resolutions: approved, rejected, corrected, escalated, spam

    resolution_notes = Column(Text, nullable=True)
    # Freeform notes from reviewer explaining resolution

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<ManualReviewQueue(id={self.id}, email_id={self.email_id}, reason='{self.review_reason}', claimed={self.claimed_at is not None})>"


# Indexes for efficient queue queries
# Index for listing pending items sorted by priority and creation time
Index(
    'idx_manual_review_queue_pending',
    ManualReviewQueue.resolved_at,
    ManualReviewQueue.priority,
    ManualReviewQueue.created_at,
    postgresql_where=(ManualReviewQueue.resolved_at.is_(None))
)

# Index for claimed but unresolved items
Index(
    'idx_manual_review_queue_claimed',
    ManualReviewQueue.claimed_at,
    ManualReviewQueue.resolved_at,
    postgresql_where=(ManualReviewQueue.claimed_at.isnot(None) & ManualReviewQueue.resolved_at.is_(None))
)
