"""
Processing Report Model
Per-email processing audit trail for operational visibility (REQ-OPS-06)
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSON
from app.database import Base


class ProcessingReport(Base):
    """
    Per-email processing audit trail.

    Captures extraction results, confidence scores, and pipeline metadata
    to provide visibility into what was extracted and what's missing.
    """

    __tablename__ = "processing_reports"

    id = Column(Integer, primary_key=True)
    email_id = Column(
        Integer,
        ForeignKey("incoming_emails.id"),
        nullable=False,
        unique=True,
        comment="Foreign key to incoming_emails table"
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="When this report was created"
    )

    # Extraction results (what was extracted)
    extracted_fields = Column(
        JSON,
        nullable=False,
        comment="Extracted fields with per-field confidence and source"
    )
    # Structure: {
    #     "client_name": {"value": "...", "confidence": 0.9, "source": "email_body"},
    #     "creditor_name": {...},
    #     "debt_amount": {...},
    #     "reference_numbers": {...}
    # }

    # Missing fields (what's missing)
    missing_fields = Column(
        JSON,
        nullable=True,
        comment="List of required fields that couldn't be extracted"
    )
    # Example: ["reference_numbers", "debt_amount"]

    # Overall assessment
    overall_confidence = Column(
        Float,
        nullable=False,
        comment="Overall confidence score (0.0-1.0)"
    )
    confidence_route = Column(
        String(20),
        nullable=False,
        comment="Confidence-based routing: high, medium, low"
    )
    needs_review = Column(
        Boolean,
        default=False,
        comment="Whether this email was routed to manual review"
    )
    review_reason = Column(
        String(100),
        nullable=True,
        comment="Reason for manual review routing"
    )

    # Pipeline metadata
    intent = Column(
        String(50),
        nullable=True,
        comment="Email intent classification result"
    )
    sources_processed = Column(
        Integer,
        default=1,
        comment="Number of content sources processed (email body + attachments)"
    )
    total_tokens_used = Column(
        Integer,
        default=0,
        comment="Total tokens consumed during extraction"
    )
    processing_time_ms = Column(
        Integer,
        nullable=True,
        comment="Total processing time in milliseconds"
    )

    __table_args__ = (
        UniqueConstraint("email_id", name="uq_processing_report_email"),
        Index("idx_processing_report_created", "created_at"),
        Index("idx_processing_report_needs_review", "needs_review"),
    )
