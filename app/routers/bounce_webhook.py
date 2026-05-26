"""
Bounce Webhook Router — Phase 4: VERP Bounce Handling

Handles bounced emails from bounce.insocore.de (VERP addresses).
The VERP_PATTERN below also accepts bounce.rasolv.ai (legacy) and
{prefix}.insocore.de (per-kanzlei subdomains) for backwards compat.

Parses bounce-{routingId}@bounce.insocore.de to identify which outbound email
bounced, then updates the creditor inquiry and outbound email status.

Flow:
1. Receive Resend inbound webhook for bounce.insocore.de
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
    Receive bounce notification via Resend inbound webhook on bounce.insocore.de.

    VERP format: bounce-{routingId}@bounce.insocore.de
    (Pattern also accepts bounce.rasolv.ai legacy and {prefix}.insocore.de per-kanzlei.)
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

    # Phase 5.4 — notify the portal so OutboundEmail.delivery_status='bounced'
    # and the cascade (final_creditor_list[].contact_status='email_failed') fires.
    # Fire-and-forget: if the portal is unreachable, we still acknowledge the
    # bounce locally above and surface a warning log.
    try:
        from app.services.portal_notifier import notify_portal_bounce

        # bounce_type from _classify_bounce is one of {'hard_bounce', 'soft_bounce', 'unknown_bounce'}.
        # The portal handler accepts the bare 'hard'/'soft' form, so we normalise.
        portal_bounce_type = "hard" if bounce_type == "hard_bounce" else "soft"

        notify_portal_bounce(
            resend_email_id=inquiry.resend_email_id,
            bounce_type=portal_bounce_type,
            bounce_reason=(email_data.subject or "").strip() or "matcher_classified",
            routing_id=routing_id,
            kanzlei_id=inquiry.kanzlei_id,
            event_id=f"matcher-bounce:{email_data.email_id}",
        )
    except Exception as e:
        # Never block the bounce-classification flow on a portal hiccup.
        logger.warning("portal_bounce_notify_failed", extra={"error": str(e), "routing_id": routing_id})

    return {
        "status": "processed",
        "routing_id": routing_id,
        "inquiry_id": inquiry.id,
        "bounce_type": bounce_type,
    }
