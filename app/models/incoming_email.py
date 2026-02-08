"""
IncomingEmail Model
Stores incoming creditor responses received via Zendesk webhook
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.database import Base


class IncomingEmail(Base):
    """
    Represents an incoming email from a creditor

    Stores both raw email data and extracted/cleaned versions
    for matching and processing.
    """
    __tablename__ = "incoming_emails"

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Email Reference (originally Zendesk, now also Resend)
    zendesk_ticket_id = Column(String(255), nullable=False, index=True)  # Email message_id or reference
    zendesk_webhook_id = Column(String(100), nullable=True, unique=True)  # For deduplication

    # Email Metadata
    from_email = Column(String(255), nullable=False, index=True)
    from_name = Column(String(255), nullable=True)
    subject = Column(String(500), nullable=True)

    # Email Content (Raw)
    raw_body_html = Column(Text, nullable=True)
    raw_body_text = Column(Text, nullable=True)

    # Email Content (Cleaned)
    cleaned_body = Column(Text, nullable=True)  # After removing HTML, signatures, etc.
    token_count_before = Column(Integer, nullable=True)
    token_count_after = Column(Integer, nullable=True)

    # LLM Extraction Results
    extracted_data = Column(JSON, nullable=True)  # Structured data from LLM
    """
    Example extracted_data structure:
    {
        "is_creditor_reply": true,
        "client_name": "Mustermann, Max",
        "creditor_name": "Sparkasse Bochum",
        "debt_amount": 1234.56,
        "reference_numbers": ["AZ-123", "KL-456"],
        "confidence": 0.85
    }
    """

    # Processing Status
    # State machine: received -> queued -> processing -> completed | failed
    # received: webhook validated and email stored
    # queued: enqueued to Dramatiq for async processing
    # processing: worker picked up and is processing
    # completed: successfully finished
    # failed: permanently failed (all retries exhausted)
    processing_status = Column(String(50), default="received")
    processing_error = Column(Text, nullable=True)

    # Matching Information (populated after matching)
    matched_inquiry_id = Column(Integer, nullable=True, index=True)
    match_confidence = Column(Integer, nullable=True)  # 0-100
    match_status = Column(String(50), nullable=True)
    # Statuses: auto_matched, needs_review, manual_queue, no_match

    # Confidence Scoring (Phase 7: Confidence Scoring & Calibration)
    extraction_confidence = Column(Integer, nullable=True)  # 0-100, extraction dimension
    # Note: match_confidence already exists (from Phase 6)
    overall_confidence = Column(Integer, nullable=True)  # 0-100, min(extraction, match)
    confidence_route = Column(String(20), nullable=True)  # high, medium, low
    # Documents the routing decision made for this email

    # Timestamps
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    matched_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Job State Machine (Phase 2: Async Job Queue Infrastructure)
    started_at = Column(DateTime(timezone=True), nullable=True)  # when worker began processing
    completed_at = Column(DateTime(timezone=True), nullable=True)  # when processing finished
    retry_count = Column(Integer, default=0, nullable=False)  # how many times Dramatiq has retried this job
    attachment_urls = Column(JSON, nullable=True)  # list of attachment URLs from Zendesk webhook
    # Example: [{"url": "https://...", "filename": "rechnung.pdf", "content_type": "application/pdf", "size": 12345}]

    # MongoDB Sync Tracking (Phase 1: Dual-Database Audit & Consistency)
    sync_status = Column(String(50), default='pending', nullable=False)
    # Statuses: pending, synced, failed, not_applicable
    sync_error = Column(Text, nullable=True)
    sync_retry_count = Column(Integer, default=0, nullable=False)
    idempotency_key = Column(String(255), nullable=True, unique=True)

    # Multi-Agent Pipeline Checkpoints (Phase 5: Multi-Agent Pipeline Validation)
    agent_checkpoints = Column(JSONB, nullable=True)
    """
    Stores intermediate results from multi-agent pipeline processing.

    Structure:
    {
        "agent_1_intent": {
            "intent": "debt_statement",
            "confidence": 0.85,
            "method": "claude_haiku",
            "timestamp": "2026-02-05T10:30:00Z",
            "validation_status": "passed"
        },
        "agent_2_extraction": {
            "sources_processed": 3,
            "gesamtforderung": 1500.0,
            "timestamp": "2026-02-05T10:30:15Z",
            "validation_status": "passed"
        },
        "agent_3_consolidation": {
            "final_amount": 1500.0,
            "conflicts_detected": 0,
            "timestamp": "2026-02-05T10:30:30Z",
            "validation_status": "passed"
        }
    }
    """

    def __repr__(self):
        return f"<IncomingEmail(id={self.id}, from='{self.from_email}', status='{self.processing_status}', sync='{self.sync_status}')>"
