"""
Inquiry Management Router
Handles creation and management of creditor inquiries
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

from app.database import get_db
from app.models import CreditorInquiry
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/inquiries", tags=["inquiries"])


class CreateInquiryRequest(BaseModel):
    """
    Request model for creating a creditor inquiry
    Matches MongoDB creditor data structure
    """
    # Client information
    client_name: str = Field(..., description="Client full name")
    client_reference_number: Optional[str] = Field(None, description="Client Aktenzeichen")

    # Creditor information
    creditor_name: str = Field(..., description="Creditor company name (sender_name)")
    creditor_email: str = Field(..., description="Creditor email (sender_email)")
    creditor_address: Optional[str] = Field(None, description="Creditor address")

    # Debt information
    debt_amount: Optional[float] = Field(None, description="Claim amount")
    reference_numbers: List[str] = Field(default_factory=list, description="Reference numbers from creditor")

    # Zendesk tracking
    zendesk_ticket_id: str = Field(..., description="Main Zendesk ticket ID (main_zendesk_ticket_id)")
    zendesk_side_conversation_id: Optional[str] = Field(None, description="Side conversation ID")

    # Timing
    sent_at: Optional[datetime] = Field(None, description="When email was sent (email_sent_at)")

    # Additional metadata
    contact_status: Optional[str] = Field(None, description="Contact status from MongoDB")
    document_url: Optional[str] = Field(None, description="First round document URL")
    notes: Optional[str] = Field(None, description="Additional notes")


class InquiryResponse(BaseModel):
    """Response model for inquiry operations"""
    id: int
    client_name: str
    creditor_name: str
    creditor_email: str
    zendesk_ticket_id: str
    zendesk_side_conversation_id: Optional[str]
    sent_at: datetime
    response_received: bool
    status: str

    class Config:
        from_attributes = True


@router.post("/", response_model=InquiryResponse, status_code=201)
async def create_inquiry(
    inquiry: CreateInquiryRequest,
    db: Session = Depends(get_db)
):
    """
    Create a new creditor inquiry

    This endpoint is called when a creditor inquiry is sent out via Zendesk.
    It stores the inquiry details so that when a creditor replies, the system
    can match the response to the original inquiry.

    Args:
        inquiry: Inquiry data from MongoDB
        db: Database session

    Returns:
        Created inquiry details
    """
    logger.info(
        f"Creating inquiry - Client: {inquiry.client_name}, "
        f"Creditor: {inquiry.creditor_name}, Ticket: {inquiry.zendesk_ticket_id}"
    )

    # Check for duplicate
    existing = db.query(CreditorInquiry).filter(
        CreditorInquiry.zendesk_ticket_id == inquiry.zendesk_ticket_id,
        CreditorInquiry.creditor_email == inquiry.creditor_email
    ).first()

    if existing:
        logger.warning(f"Inquiry already exists for ticket {inquiry.zendesk_ticket_id} and creditor {inquiry.creditor_email}")
        raise HTTPException(status_code=409, detail="Inquiry already exists")

    # Build notes from contact status and document URL
    notes_parts = []
    if inquiry.contact_status:
        notes_parts.append(f"Status: {inquiry.contact_status}")
    if inquiry.document_url:
        notes_parts.append(f"Document: {inquiry.document_url}")
    if inquiry.notes:
        notes_parts.append(inquiry.notes)
    notes = " | ".join(notes_parts) if notes_parts else None

    # Convert reference_numbers array to single string (first one)
    reference_number = inquiry.reference_numbers[0] if inquiry.reference_numbers else None

    # Create inquiry (only use fields that exist in the model)
    db_inquiry = CreditorInquiry(
        client_name=inquiry.client_name,
        client_name_normalized=inquiry.client_name.lower().strip(),
        creditor_name=inquiry.creditor_name,
        creditor_name_normalized=inquiry.creditor_name.lower().strip(),
        creditor_email=inquiry.creditor_email.lower().strip(),
        debt_amount=inquiry.debt_amount,
        reference_number=reference_number,
        zendesk_ticket_id=inquiry.zendesk_ticket_id,
        zendesk_side_conversation_id=inquiry.zendesk_side_conversation_id,
        sent_at=inquiry.sent_at or datetime.utcnow(),
        response_received=False,
        status="sent"
    )

    db.add(db_inquiry)
    db.commit()
    db.refresh(db_inquiry)

    logger.info(f"Inquiry created successfully - ID: {db_inquiry.id}")

    return db_inquiry


@router.post("/bulk", status_code=201)
async def create_bulk_inquiries(
    inquiries: List[CreateInquiryRequest],
    db: Session = Depends(get_db)
):
    """
    Bulk create multiple creditor inquiries

    Useful for importing existing inquiries from MongoDB in batch.

    Args:
        inquiries: List of inquiry data
        db: Database session

    Returns:
        Summary of created inquiries
    """
    logger.info(f"Bulk creating {len(inquiries)} inquiries")

    created_count = 0
    skipped_count = 0
    errors = []

    for inquiry in inquiries:
        try:
            # Check for duplicate
            existing = db.query(CreditorInquiry).filter(
                CreditorInquiry.zendesk_ticket_id == inquiry.zendesk_ticket_id,
                CreditorInquiry.creditor_email == inquiry.creditor_email
            ).first()

            if existing:
                skipped_count += 1
                continue

            # Build notes
            notes_parts = []
            if inquiry.contact_status:
                notes_parts.append(f"Status: {inquiry.contact_status}")
            if inquiry.document_url:
                notes_parts.append(f"Document: {inquiry.document_url}")
            if inquiry.notes:
                notes_parts.append(inquiry.notes)
            notes = " | ".join(notes_parts) if notes_parts else None

            # Convert reference_numbers array to single string
            reference_number = inquiry.reference_numbers[0] if inquiry.reference_numbers else None

            # Create inquiry (only use fields that exist in the model)
            db_inquiry = CreditorInquiry(
                client_name=inquiry.client_name,
                client_name_normalized=inquiry.client_name.lower().strip(),
                creditor_name=inquiry.creditor_name,
                creditor_name_normalized=inquiry.creditor_name.lower().strip(),
                creditor_email=inquiry.creditor_email.lower().strip(),
                debt_amount=inquiry.debt_amount,
                reference_number=reference_number,
                zendesk_ticket_id=inquiry.zendesk_ticket_id,
                zendesk_side_conversation_id=inquiry.zendesk_side_conversation_id,
                sent_at=inquiry.sent_at or datetime.utcnow(),
                response_received=False,
                status="sent"
            )

            db.add(db_inquiry)
            created_count += 1

        except Exception as e:
            logger.error(f"Error creating inquiry: {e}")
            errors.append({
                "client": inquiry.client_name,
                "creditor": inquiry.creditor_name,
                "error": str(e)
            })

    db.commit()

    logger.info(f"Bulk creation complete - Created: {created_count}, Skipped: {skipped_count}, Errors: {len(errors)}")

    return {
        "created": created_count,
        "skipped": skipped_count,
        "errors": errors,
        "total": len(inquiries)
    }


@router.get("/{inquiry_id}", response_model=InquiryResponse)
async def get_inquiry(inquiry_id: int, db: Session = Depends(get_db)):
    """Get inquiry by ID"""
    inquiry = db.query(CreditorInquiry).filter(CreditorInquiry.id == inquiry_id).first()
    if not inquiry:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return inquiry


@router.get("/", response_model=List[InquiryResponse])
async def list_inquiries(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    client_name: Optional[str] = None,
    creditor_email: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    List inquiries with optional filters

    Args:
        skip: Number of records to skip (pagination)
        limit: Maximum number of records to return
        status: Filter by status (sent, replied, etc.)
        client_name: Filter by client name (partial match)
        creditor_email: Filter by creditor email
        db: Database session

    Returns:
        List of inquiries
    """
    query = db.query(CreditorInquiry)

    if status:
        query = query.filter(CreditorInquiry.status == status)
    if client_name:
        query = query.filter(CreditorInquiry.client_name.ilike(f"%{client_name}%"))
    if creditor_email:
        query = query.filter(CreditorInquiry.creditor_email == creditor_email.lower().strip())

    inquiries = query.order_by(CreditorInquiry.sent_at.desc()).offset(skip).limit(limit).all()
    return inquiries


@router.delete("/{inquiry_id}", status_code=204)
async def delete_inquiry(inquiry_id: int, db: Session = Depends(get_db)):
    """Delete an inquiry by ID"""
    inquiry = db.query(CreditorInquiry).filter(CreditorInquiry.id == inquiry_id).first()
    if not inquiry:
        raise HTTPException(status_code=404, detail="Inquiry not found")

    db.delete(inquiry)
    db.commit()
    logger.info(f"Inquiry {inquiry_id} deleted")
