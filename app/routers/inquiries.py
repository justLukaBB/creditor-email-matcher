"""
Creditor Inquiries Router

Receives outbound inquiry data from Node.js mandanten-portal
and creates CreditorInquiry records for matching.
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
import structlog

from app.database import get_db
from app.models import CreditorInquiry

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/inquiries", tags=["inquiries"])


class InquiryCreate(BaseModel):
    """Schema matching Node.js sync_inquiry_to_matcher.js payload."""

    # Client information
    client_name: str
    client_reference_number: Optional[str] = None

    # Creditor information
    creditor_name: str
    creditor_email: EmailStr
    creditor_address: Optional[str] = None

    # Debt information
    debt_amount: Optional[float] = None
    reference_numbers: Optional[List[str]] = None

    # Zendesk tracking
    zendesk_ticket_id: Optional[str] = None
    zendesk_side_conversation_id: Optional[str] = None

    # Resend tracking
    resend_email_id: Optional[str] = None
    resend_message_id: Optional[str] = None  # Message-ID header (for Stage 2 In-Reply-To matching)
    email_provider: str = "resend"

    # Timing
    sent_at: Optional[datetime] = None

    # Letter type (first = 1. Schreiben, second = 2. Schreiben/Schuldenbereinigungsplan)
    letter_type: str = "first"

    # Additional metadata
    contact_status: Optional[str] = None
    document_url: Optional[str] = None
    notes: Optional[str] = None

    # Deterministic routing (Phase 3 + V2)
    routing_id: Optional[str] = None
    routing_id_version: Optional[str] = None  # 'v1' | 'v2'
    kanzlei_id: Optional[str] = None
    kanzlei_prefix: Optional[str] = None
    # V2: Snapshot of creditor array index (anti-reorder protection)
    creditor_idx_snapshot: Optional[int] = None
    # V2: Client-AZ hash segment for fast lookup
    client_hash: Optional[str] = None


class InquiryResponse(BaseModel):
    """Response schema for created inquiry."""
    id: int
    client_name: str
    creditor_name: str
    creditor_email: str
    letter_type: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


def normalize_name(name: str) -> str:
    """Normalize name for fuzzy matching."""
    if not name:
        return ""
    # Lowercase, remove extra spaces
    normalized = " ".join(name.lower().split())
    # Remove common suffixes
    for suffix in [" gmbh", " ag", " kg", " ohg", " ug", " e.v.", " mbh"]:
        normalized = normalized.replace(suffix, "")
    return normalized.strip()


@router.post("/", response_model=InquiryResponse, status_code=201)
async def create_inquiry(
    inquiry: InquiryCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new creditor inquiry record.

    Called by Node.js mandanten-portal when sending emails to creditors.
    This enables the matching engine to find incoming replies.
    """
    log = logger.bind(
        client_name=inquiry.client_name,
        creditor_email=inquiry.creditor_email
    )

    # Check for duplicates using normalized name to catch format variants
    # ("Mariana Machado" vs "Machado, Mariana")
    normalized_input = normalize_name(inquiry.client_name)
    existing = db.query(CreditorInquiry).filter(
        CreditorInquiry.creditor_email == inquiry.creditor_email.lower(),
        CreditorInquiry.client_name_normalized == normalized_input,
        CreditorInquiry.letter_type == inquiry.letter_type,
    ).first()

    # Also check reversed name order ("Last, First" -> "First Last")
    if not existing and "," in inquiry.client_name:
        parts = [p.strip() for p in inquiry.client_name.split(",", 1)]
        reversed_name = normalize_name(f"{parts[1]} {parts[0]}")
        existing = db.query(CreditorInquiry).filter(
            CreditorInquiry.creditor_email == inquiry.creditor_email.lower(),
            CreditorInquiry.client_name_normalized == reversed_name,
            CreditorInquiry.letter_type == inquiry.letter_type,
        ).first()

    if existing and inquiry.resend_email_id:
        # Check if it's the same Resend email
        if existing.resend_email_id == inquiry.resend_email_id:
            log.info("inquiry_duplicate_skipped", existing_id=existing.id)
            raise HTTPException(
                status_code=409,
                detail=f"Inquiry already exists with ID {existing.id}"
            )

    # Create new inquiry
    db_inquiry = CreditorInquiry(
        # Client
        client_name=inquiry.client_name,
        client_name_normalized=normalize_name(inquiry.client_name),

        # Creditor
        creditor_name=inquiry.creditor_name,
        creditor_email=inquiry.creditor_email.lower(),
        creditor_name_normalized=normalize_name(inquiry.creditor_name),

        # Debt
        debt_amount=inquiry.debt_amount,
        reference_number=inquiry.reference_numbers[0] if inquiry.reference_numbers else None,

        # Zendesk
        zendesk_ticket_id=inquiry.zendesk_ticket_id or "resend-only",
        zendesk_side_conversation_id=inquiry.zendesk_side_conversation_id,

        # Resend
        resend_email_id=inquiry.resend_email_id,
        resend_message_id=inquiry.resend_message_id,
        email_provider=inquiry.email_provider,

        # Letter type
        letter_type=inquiry.letter_type,

        # Deterministic routing (Phase 3 + V2)
        routing_id=inquiry.routing_id,
        routing_id_version=inquiry.routing_id_version,
        kanzlei_id=inquiry.kanzlei_id,
        kanzlei_prefix=inquiry.kanzlei_prefix,
        creditor_idx_snapshot=inquiry.creditor_idx_snapshot,
        client_hash=inquiry.client_hash,

        # Timing
        sent_at=inquiry.sent_at or datetime.utcnow(),

        # Status
        status="sent"
    )

    db.add(db_inquiry)
    db.commit()
    db.refresh(db_inquiry)

    log.info("inquiry_created", inquiry_id=db_inquiry.id)

    return db_inquiry


@router.get("/", response_model=List[InquiryResponse])
async def list_inquiries(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """List recent creditor inquiries."""
    inquiries = db.query(CreditorInquiry).order_by(
        CreditorInquiry.sent_at.desc()
    ).offset(skip).limit(limit).all()

    return inquiries


@router.get("/{inquiry_id}", response_model=InquiryResponse)
async def get_inquiry(
    inquiry_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific inquiry by ID."""
    inquiry = db.query(CreditorInquiry).filter(
        CreditorInquiry.id == inquiry_id
    ).first()

    if not inquiry:
        raise HTTPException(status_code=404, detail="Inquiry not found")

    return inquiry
