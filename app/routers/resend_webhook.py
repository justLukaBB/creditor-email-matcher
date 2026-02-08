"""
Resend Inbound Webhook Router
Handles incoming emails received via Resend

Flow:
1. Receive webhook from Resend (contains metadata only, no body)
2. Verify Svix signature
3. Fetch full email content via Resend API
4. Save to PostgreSQL with RECEIVED status
5. Enqueue Dramatiq job for async processing
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional, List
import httpx
import hmac
import hashlib
import base64
import structlog
from pydantic import BaseModel, Field
from asgi_correlation_id.context import correlation_id

from app.database import get_db
from app.config import settings
from app.models.webhook_schemas import WebhookResponse
from app.models import IncomingEmail

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/resend", tags=["resend-webhook"])


# --- Pydantic Schemas for Resend ---

class ResendAttachment(BaseModel):
    """Attachment metadata from Resend webhook"""
    id: str
    filename: str
    content_type: str
    content_disposition: Optional[str] = None
    content_id: Optional[str] = None


class ResendEmailData(BaseModel):
    """Email data from Resend webhook"""
    email_id: str
    created_at: str
    from_: str = Field(..., alias="from")
    to: List[str]
    cc: Optional[List[str]] = []
    bcc: Optional[List[str]] = []
    subject: Optional[str] = None
    message_id: Optional[str] = None
    attachments: Optional[List[ResendAttachment]] = []

    class Config:
        populate_by_name = True


class ResendWebhookPayload(BaseModel):
    """Full Resend webhook payload"""
    type: str  # "email.received"
    created_at: str
    data: ResendEmailData

    class Config:
        extra = "allow"


# --- Svix Signature Verification ---

def verify_svix_signature(
    payload: bytes,
    svix_id: Optional[str],
    svix_timestamp: Optional[str],
    svix_signature: Optional[str],
    secret: str
) -> bool:
    """
    Verify Resend webhook signature using Svix protocol.

    The signature is computed as:
    Base64(HMAC-SHA256(secret, "{svix_id}.{svix_timestamp}.{payload}"))
    """
    if not all([svix_id, svix_timestamp, svix_signature, secret]):
        return False

    try:
        # Svix secret format: "whsec_<base64_key>"
        if secret.startswith("whsec_"):
            secret_bytes = base64.b64decode(secret[6:])
        else:
            secret_bytes = base64.b64decode(secret)

        # Build signed content
        signed_content = f"{svix_id}.{svix_timestamp}.{payload.decode('utf-8')}"

        # Compute expected signature
        expected_sig = base64.b64encode(
            hmac.new(secret_bytes, signed_content.encode('utf-8'), hashlib.sha256).digest()
        ).decode('utf-8')

        # Svix signature header can contain multiple signatures separated by space
        # Format: "v1,<sig1> v1,<sig2>"
        signatures = svix_signature.split(' ')
        for sig in signatures:
            if ',' in sig:
                version, sig_value = sig.split(',', 1)
                if version == 'v1' and hmac.compare_digest(sig_value, expected_sig):
                    return True

        return False

    except Exception as e:
        logger.error("svix_signature_verification_error", error=str(e))
        return False


async def fetch_email_content(email_id: str) -> dict:
    """
    Fetch full email content from Resend API.

    The webhook only contains metadata, so we need to call the API
    to get the actual email body (html/text).
    """
    if not settings.resend_api_key:
        raise HTTPException(
            status_code=500,
            detail="RESEND_API_KEY not configured"
        )

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.resend.com/emails/{email_id}",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}"
            },
            timeout=30.0
        )

        if response.status_code != 200:
            logger.error(
                "resend_api_error",
                email_id=email_id,
                status_code=response.status_code,
                response=response.text
            )
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch email from Resend API: {response.status_code}"
            )

        return response.json()


@router.post("/webhook", response_model=WebhookResponse)
async def receive_resend_webhook(
    request: Request,
    svix_id: Optional[str] = Header(None, alias="svix-id"),
    svix_timestamp: Optional[str] = Header(None, alias="svix-timestamp"),
    svix_signature: Optional[str] = Header(None, alias="svix-signature"),
    db: Session = Depends(get_db)
):
    """
    Receive incoming email webhook from Resend.

    Resend webhooks contain metadata only - we fetch the full email
    content via the Resend API before processing.

    Headers:
        svix-id: Webhook message ID
        svix-timestamp: Unix timestamp
        svix-signature: HMAC signature for verification
    """
    # Get raw body for signature verification
    body = await request.body()

    logger.info("resend_webhook_received", svix_id=svix_id)

    # Step 1: Verify Svix signature (if configured)
    if settings.resend_webhook_secret:
        if not verify_svix_signature(
            body, svix_id, svix_timestamp, svix_signature,
            settings.resend_webhook_secret
        ):
            logger.warning("invalid_resend_webhook_signature", svix_id=svix_id)
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Step 2: Parse webhook payload
    try:
        webhook_data = ResendWebhookPayload.model_validate_json(body)
    except Exception as e:
        logger.error("resend_webhook_parse_error", error=str(e))
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {e}")

    # Only process email.received events
    if webhook_data.type != "email.received":
        logger.info("resend_webhook_ignored", event_type=webhook_data.type)
        return WebhookResponse(
            status="ignored",
            message=f"Event type '{webhook_data.type}' not processed"
        )

    email_data = webhook_data.data
    logger.info(
        "resend_email_received",
        email_id=email_data.email_id,
        from_email=email_data.from_,
        subject=email_data.subject
    )

    # Step 3: Check for PostgreSQL (required for async processing)
    if db is None:
        logger.warning("async_processing_requires_postgresql")
        raise HTTPException(
            status_code=503,
            detail="Async processing requires PostgreSQL. Configure DATABASE_URL."
        )

    # Step 4: Check for duplicate (using Resend email_id as webhook_id)
    existing = db.query(IncomingEmail).filter(
        IncomingEmail.zendesk_webhook_id == email_data.email_id
    ).first()
    if existing:
        logger.info("duplicate_resend_webhook_ignored",
                   email_id=email_data.email_id,
                   db_id=existing.id)
        return WebhookResponse(
            status="duplicate",
            message="Email already processed",
            email_id=existing.id
        )

    # Step 5: Fetch full email content from Resend API
    full_email = await fetch_email_content(email_data.email_id)

    # Step 6: Parse received_at timestamp
    received_at = datetime.utcnow()
    try:
        from dateutil import parser as date_parser
        received_at = date_parser.parse(email_data.created_at)
    except Exception as e:
        logger.warning("created_at_parse_failed", error=str(e))

    # Step 7: Extract sender email from "Name <email>" format
    from_email = email_data.from_
    from_name = None
    if '<' in from_email and '>' in from_email:
        # Parse "Sender Name <sender@example.com>"
        parts = from_email.split('<')
        from_name = parts[0].strip().strip('"')
        from_email = parts[1].rstrip('>')

    # Step 8: Store incoming email with RECEIVED status
    incoming_email = IncomingEmail(
        zendesk_ticket_id=email_data.message_id or email_data.email_id,  # Use message_id as reference
        zendesk_webhook_id=email_data.email_id,  # Resend email ID for dedup
        from_email=from_email.strip(),
        from_name=from_name,
        subject=email_data.subject,
        raw_body_html=full_email.get("html"),
        raw_body_text=full_email.get("text"),
        attachment_urls=[
            {"id": att.id, "filename": att.filename, "content_type": att.content_type}
            for att in (email_data.attachments or [])
        ],
        received_at=received_at,
        processing_status="received"
    )
    db.add(incoming_email)
    db.commit()
    db.refresh(incoming_email)

    logger.info("resend_email_saved_to_postgres",
               email_id=incoming_email.id,
               resend_id=email_data.email_id)

    # Step 9: Transition to QUEUED status
    incoming_email.processing_status = "queued"
    db.commit()

    # Step 10: Enqueue Dramatiq job for async processing
    current_correlation_id = correlation_id.get()
    from app.actors.email_processor import process_email
    process_email.send(email_id=incoming_email.id, correlation_id=current_correlation_id)

    logger.info("resend_email_queued_for_processing",
               email_id=incoming_email.id,
               correlation_id=current_correlation_id)

    return WebhookResponse(
        status="accepted",
        message="Email queued for processing",
        email_id=incoming_email.id
    )
