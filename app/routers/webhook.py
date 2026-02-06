"""
Webhook Router
Handles incoming Zendesk webhooks for creditor emails

This router implements a thin validate-and-enqueue pattern:
1. Validate webhook signature
2. Save to PostgreSQL (audit trail with RECEIVED status)
3. Enqueue Dramatiq job for async processing
4. Return 200 OK

Heavy processing logic has moved to app/actors/email_processor.py
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import hashlib
import hmac
import structlog
from asgi_correlation_id.context import correlation_id

from app.database import get_db
from app.config import settings
from app.models.webhook_schemas import ZendeskWebhookEmail, WebhookResponse
from app.models import IncomingEmail

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/zendesk", tags=["webhook"])


def verify_webhook_signature(
    payload: str,
    signature: Optional[str],
    secret: str
) -> bool:
    """
    Verify Zendesk webhook signature
    """
    if not signature or not secret:
        return False

    # Calculate expected signature
    expected = hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


@router.post("/webhook", response_model=WebhookResponse)
async def receive_webhook(
    webhook_data: ZendeskWebhookEmail,
    x_zendesk_signature: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """
    Receive incoming email webhook from Zendesk

    Validate-and-enqueue pattern:
    1. Verify webhook signature (security)
    2. Check for duplicates (idempotency)
    3. Save to PostgreSQL with RECEIVED status (audit trail)
    4. Transition to QUEUED and enqueue Dramatiq job
    5. Return 200 OK immediately

    Heavy processing happens asynchronously in app/actors/email_processor.py

    Args:
        webhook_data: Email data from Zendesk
        x_zendesk_signature: Webhook signature header
        db: Database session

    Returns:
        WebhookResponse with processing status
    """
    logger.info(
        "webhook_received",
        ticket_id=webhook_data.ticket_id,
        from_email=webhook_data.from_email
    )

    # Step 1: Verify webhook signature (if configured)
    if settings.webhook_secret:
        payload_str = webhook_data.model_dump_json()
        if not verify_webhook_signature(payload_str, x_zendesk_signature, settings.webhook_secret):
            logger.warning("invalid_webhook_signature",
                          from_email=webhook_data.from_email)
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Step 2: MongoDB-only mode check (PostgreSQL required for async processing)
    if db is None:
        logger.warning("async_processing_requires_postgresql",
                      message="Async processing requires PostgreSQL. Configure DATABASE_URL.")
        raise HTTPException(
            status_code=503,
            detail="Async processing requires PostgreSQL. Configure DATABASE_URL."
        )

    # Step 3: Check for duplicate webhook
    if webhook_data.webhook_id:
        existing = db.query(IncomingEmail).filter(
            IncomingEmail.zendesk_webhook_id == webhook_data.webhook_id
        ).first()
        if existing:
            logger.info("duplicate_webhook_ignored",
                       webhook_id=webhook_data.webhook_id,
                       email_id=existing.id)
            return WebhookResponse(
                status="duplicate",
                message="Email already processed",
                email_id=existing.id
            )

    # Step 4: Parse received_at timestamp with fallback
    received_at = datetime.utcnow()  # Default to now
    if webhook_data.received_at:
        try:
            from dateutil import parser as date_parser
            received_at = date_parser.parse(webhook_data.received_at)
        except Exception as e:
            logger.warning("received_at_parse_failed",
                          received_at=webhook_data.received_at,
                          error=str(e))

    # Step 5: Store incoming email with RECEIVED status (audit trail)
    incoming_email = IncomingEmail(
        zendesk_ticket_id=webhook_data.ticket_id.strip(),
        zendesk_webhook_id=webhook_data.webhook_id,
        from_email=webhook_data.from_email.strip(),
        from_name=webhook_data.from_name.strip() if webhook_data.from_name else None,
        subject=webhook_data.subject.strip() if webhook_data.subject else None,
        raw_body_html=webhook_data.body_html,
        raw_body_text=webhook_data.body_text,
        attachment_urls=webhook_data.attachments,  # New field from Plan 02
        received_at=received_at,
        processing_status="received"
    )
    db.add(incoming_email)
    db.commit()
    db.refresh(incoming_email)

    logger.info("email_saved_to_postgres",
               email_id=incoming_email.id,
               status="received")

    # Step 6: Transition to QUEUED status
    incoming_email.processing_status = "queued"
    db.commit()

    # Step 7: Enqueue Dramatiq job for async processing
    # Capture current correlation_id and pass to actor for tracing
    current_correlation_id = correlation_id.get()
    from app.actors.email_processor import process_email
    process_email.send(email_id=incoming_email.id, correlation_id=current_correlation_id)

    logger.info("email_queued_for_processing",
               email_id=incoming_email.id,
               status="queued",
               correlation_id=current_correlation_id)

    return WebhookResponse(
        status="accepted",
        message="Email queued for processing",
        email_id=incoming_email.id
    )


@router.get("/status/{email_id}")
async def get_email_status(email_id: int, db: Session = Depends(get_db)):
    """
    Get processing status of an email

    Args:
        email_id: Incoming email ID

    Returns:
        Status information
    """
    email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    return {
        "email_id": email.id,
        "processing_status": email.processing_status,
        "match_status": email.match_status,
        "match_confidence": email.match_confidence,
        "matched_inquiry_id": email.matched_inquiry_id,
        "extracted_data": email.extracted_data,
    }
