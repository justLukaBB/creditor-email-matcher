"""
CreditorInquiry Model
Stores original inquiries sent to creditors via Zendesk
"""

from sqlalchemy import Column, Integer, String, DateTime, Numeric, Text, Boolean
from sqlalchemy.sql import func
from app.database import Base


class CreditorInquiry(Base):
    """
    Represents an inquiry sent to a creditor about a client's debt

    This is the "source of truth" for matching incoming emails.
    When we send an inquiry, we store it here with all relevant details.
    """
    __tablename__ = "creditor_inquiries"

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Client Information
    client_name = Column(String(255), nullable=False, index=True)
    client_name_normalized = Column(String(255), index=True)  # For fuzzy matching

    # Creditor Information
    creditor_name = Column(String(255), nullable=False, index=True)
    creditor_email = Column(String(255), nullable=False, index=True)
    creditor_name_normalized = Column(String(255), index=True)

    # Debt Information
    debt_amount = Column(Numeric(10, 2), nullable=True)
    reference_number = Column(String(100), nullable=True, index=True)  # AZ, Kundennummer, etc.

    # Zendesk Information
    zendesk_ticket_id = Column(String(50), nullable=False, index=True)
    zendesk_side_conversation_id = Column(String(50), nullable=True, index=True)

    # Resend Email Provider (alternative to Zendesk Side Conversations)
    resend_email_id = Column(String(100), nullable=True, index=True)
    email_provider = Column(String(20), default="zendesk")  # 'zendesk' or 'resend'

    # Email Content (for reference)
    email_subject = Column(String(500), nullable=True)
    email_body = Column(Text, nullable=True)

    # Letter Type (1. Schreiben vs 2. Schreiben/Schuldenbereinigungsplan)
    letter_type = Column(String(20), default="first", nullable=False, server_default="first")

    # Deterministic Routing (Phase 3)
    # routing_id: V1 "SC-A1221-42" or V2 "SC-00-1-a3f2-k7p"
    routing_id = Column(String(40), nullable=True, index=True)
    routing_id_version = Column(String(4), nullable=True, index=True)  # 'v1' or 'v2'
    resend_message_id = Column(String(500), nullable=True, index=True)  # Message-ID header for In-Reply-To matching
    kanzlei_id = Column(String(50), nullable=True, index=True)
    kanzlei_prefix = Column(String(3), nullable=True)
    # Snapshot of creditor array index at inquiry creation time.
    # Protects against MongoDB array reorder — matcher joins on this, not final_creditor_list order.
    creditor_idx_snapshot = Column(Integer, nullable=True, index=True)
    # Client-AZ hash segment from V2 routing ID (4 base36 chars) — redundant identifier for fast lookups
    client_hash = Column(String(4), nullable=True, index=True)

    # Status Tracking
    status = Column(String(50), default="sent")  # sent, replied, no_response, etc.
    response_received = Column(Boolean, default=False)

    # Timestamps
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<CreditorInquiry(id={self.id}, client='{self.client_name}', creditor='{self.creditor_name}')>"
