"""
Bounce Webhook Router — Phase 4: VERP Bounce Handling

Handles bounced emails from bounce.rasolv.ai (VERP addresses).
Parses bounce-{routingId}@bounce.rasolv.ai to identify which outbound email bounced,
then updates the creditor inquiry and outbound email status.

Flow:
1. Receive Resend inbound webhook for bounce.rasolv.ai
2. Parse VERP address to extract routing ID
3. Look up CreditorInquiry by routing_id
4. Mark inquiry as bounced
5. Notify portal for admin visibility
"""

import re
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.database import get_db
from app.config import settings
from app.models.creditor_inquiry import CreditorInquiry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bounce", tags=["bounce-webhook"])

VERP_PATTERN = re.compile(
    r"bounce-([A-Z]{2,3}-[A-Za-z0-9]+-\d+)@(?:bounce\.insocore\.de|bounce\.rasolv\.ai|[a-z]{2,3}\.insocore\.de)",
    re.IGNORECASE,
)


class BounceEmailData(BaseModel):
    email_id: str
    from_: str = Field(..., alias="from")
    to: List[str]
    subject: Optional[str] = None
    text: Optional[str] = None
    html: Optional[str] = None

    class Config:
        populate_by_name = True


class BounceWebhookPayload(BaseModel):
    type: str
    created_at: str
    data: BounceEmailData

    class Config:
        extra = "allow"


def _classify_bounce(subject: Optional[str], body: Optional[str]) -> str:
    """Classify bounce as hard or soft based on content."""
    text = f"{subject or ''} {body or ''}".lower()

    hard_indicators = [
        "user unknown", "mailbox not found", "address rejected",
        "does not exist", "no such user", "undeliverable",
        "permanent", "550 ", "551 ", "552 ", "553 ", "554 ",
    ]
    for indicator in hard_indicators:
        if indicator in text:
            return "hard_bounce"

    soft_indicators = [
        "mailbox full", "over quota", "temporarily",
        "try again", "service unavailable", "421 ", "450 ", "452 ",
    ]
    for indicator in soft_indicators:
        if indicator in text:
            return "soft_bounce"

    return "unknown_bounce"


@router.post("/webhook")
async def receive_bounce_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Receive bounce notification via Resend inbound webhook on bounce.rasolv.ai.

    VERP format: bounce-{routingId}@bounce.rasolv.ai
    """
    try:
        body = await request.body()
        payload = BounceWebhookPayload.model_validate_json(body)
    except Exception as e:
        logger.error("bounce_webhook_parse_error", extra={"error": str(e)})
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    if payload.type != "email.received":
        return {"status": "ignored", "message": f"Event type '{payload.type}' not processed"}

    email_data = payload.data

    # Extract routing ID from VERP address
    routing_id = None
    for addr in email_data.to:
        match = VERP_PATTERN.search(addr)
        if match:
            routing_id = match.group(1).upper()
            break

    if not routing_id:
        logger.warning("bounce_no_verp_address", extra={
            "to": email_data.to,
            "subject": email_data.subject,
        })
        return {"status": "ignored", "message": "No VERP address found"}

    # Classify bounce type
    bounce_type = _classify_bounce(email_data.subject, email_data.text)

    logger.info("bounce_received", extra={
        "routing_id": routing_id,
        "bounce_type": bounce_type,
        "from": email_data.from_,
        "subject": email_data.subject,
    })

    # Look up creditor inquiry
    inquiry = db.query(CreditorInquiry).filter(
        CreditorInquiry.routing_id == routing_id
    ).first()

    if not inquiry:
        logger.warning("bounce_inquiry_not_found", extra={"routing_id": routing_id})
        return {"status": "not_found", "message": f"No inquiry for routing_id {routing_id}"}

    # Update inquiry status
    inquiry.status = bounce_type
    db.commit()

    logger.info("bounce_inquiry_updated", extra={
        "routing_id": routing_id,
        "inquiry_id": inquiry.id,
        "bounce_type": bounce_type,
        "creditor_email": inquiry.creditor_email,
    })

    return {
        "status": "processed",
        "routing_id": routing_id,
        "inquiry_id": inquiry.id,
        "bounce_type": bounce_type,
    }
