"""
IncomingEmail Model
Stores incoming creditor responses received via Zendesk webhook
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON
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

    # Zendesk Information
    zendesk_ticket_id = Column(String(50), nullable=False, index=True)
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

    def __repr__(self):
        return f"<IncomingEmail(id={self.id}, from='{self.from_email}', status='{self.processing_status}', sync='{self.sync_status}')>"
