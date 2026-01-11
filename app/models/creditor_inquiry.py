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

    # Email Content (for reference)
    email_subject = Column(String(500), nullable=True)
    email_body = Column(Text, nullable=True)

    # Status Tracking
    status = Column(String(50), default="sent")  # sent, replied, no_response, etc.
    response_received = Column(Boolean, default=False)

    # Timestamps
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<CreditorInquiry(id={self.id}, client='{self.client_name}', creditor='{self.creditor_name}')>"
