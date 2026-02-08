"""
Resend Inbound Webhook Router
Handles incoming emails received via Resend

Flow:
1. Receive webhook from Resend (metadata only)
2. Verify Svix signature
3. Fetch full email content via Resend API
4. Save to PostgreSQL with RECEIVED status
5. Enqueue Dramatiq job for async processing
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional, List
import hmac
import hashlib
import base64
import httpx
import structlog
from pydantic import BaseModel, Field
from asgi_correlation_id.context import correlation_id

from app.database import get_db
from app.config import settings
from app.models.webhook_schemas import WebhookResponse
from app.models import IncomingEmail

logger = structlog.get_logger()


async def fetch_email_content_from_resend(email_id: str) -> dict:
    """
    Fetch full email content from Resend Receiving API.

    Inbound webhooks only contain metadata - we need to fetch
    the actual email body via the Emails.Receiving API.

    API: GET /emails/receiving/{email_id}

    Returns dict with 'html' and 'text' fields.
    """
    if not settings.resend_api_key:
        logger.warning("resend_api_key_not_configured")
        return {"html": None, "text": None}

    try:
        async with httpx.AsyncClient() as client:
            # Use the Receiving API endpoint for inbound emails
            response = await client.get(
                f"https://api.resend.com/emails/receiving/{email_id}",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json"
                },
                timeout=10.0
            )

            if response.status_code == 200:
                data = response.json()
                logger.info(
                    "resend_email_content_fetched",
                    email_id=email_id,
                    has_html=bool(data.get("html")),
                    has_text=bool(data.get("text")),
                    has_body=bool(data.get("body"))
                )
                # Resend may return body as 'body', 'text', or 'html'
                return {
                    "html": data.get("html"),
                    "text": data.get("text") or data.get("body")
                }
            else:
                logger.warning(
                    "resend_email_fetch_failed",
                    email_id=email_id,
                    status=response.status_code,
                    response=response.text[:500]
                )
                return {"html": None, "text": None}

    except Exception as e:
        logger.error("resend_email_fetch_error", email_id=email_id, error=str(e))
        return {"html": None, "text": None}


async def fetch_attachment_download_url(email_id: str, attachment_id: str) -> Optional[str]:
    """
    Fetch attachment download URL from Resend Receiving API.

    API: GET /emails/receiving/{email_id}/attachments/{attachment_id}

    Returns the download_url for the attachment, or None if fetch fails.
    """
    if not settings.resend_api_key:
        logger.warning("resend_api_key_not_configured")
        return None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.resend.com/emails/receiving/{email_id}/attachments/{attachment_id}",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )

            if response.status_code == 200:
                data = response.json()
                download_url = data.get("download_url")
                logger.info(
                    "resend_attachment_url_fetched",
                    email_id=email_id,
                    attachment_id=attachment_id,
                    filename=data.get("filename"),
                    size=data.get("size")
                )
                return download_url
            else:
                logger.warning(
                    "resend_attachment_fetch_failed",
                    email_id=email_id,
                    attachment_id=attachment_id,
                    status=response.status_code,
                    response=response.text[:500]
                )
                return None

    except Exception as e:
        logger.error(
            "resend_attachment_fetch_error",
            email_id=email_id,
            attachment_id=attachment_id,
            error=str(e)
        )
        return None


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
    """Email data from Resend inbound webhook - includes full email content"""
    email_id: str
    created_at: str
    from_: str = Field(..., alias="from")
    to: List[str]
    cc: Optional[List[str]] = []
    bcc: Optional[List[str]] = []
    subject: Optional[str] = None
    message_id: Optional[str] = None
    html: Optional[str] = None  # HTML body (included in inbound webhook)
    text: Optional[str] = None  # Plain text body (included in inbound webhook)
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

    # Log full payload for debugging
    logger.info("resend_webhook_received", svix_id=svix_id, raw_payload=body.decode('utf-8')[:2000])

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

    # Step 5: Parse received_at timestamp
    received_at = datetime.utcnow()
    try:
        from dateutil import parser as date_parser
        received_at = date_parser.parse(email_data.created_at)
    except Exception as e:
        logger.warning("created_at_parse_failed", error=str(e))

    # Step 6: Extract sender email from "Name <email>" format
    from_email = email_data.from_
    from_name = None
    if '<' in from_email and '>' in from_email:
        # Parse "Sender Name <sender@example.com>"
        parts = from_email.split('<')
        from_name = parts[0].strip().strip('"')
        from_email = parts[1].rstrip('>')

    # Step 6b: Fetch full email content from Resend API
    # Inbound webhooks only contain metadata, not the email body
    email_html = email_data.html
    email_text = email_data.text

    if not email_html and not email_text:
        logger.info("fetching_email_content_from_resend_api", email_id=email_data.email_id)
        content = await fetch_email_content_from_resend(email_data.email_id)
        email_html = content.get("html")
        email_text = content.get("text")

    # Step 7: Store incoming email with RECEIVED status
    incoming_email = IncomingEmail(
        zendesk_ticket_id=email_data.message_id or email_data.email_id,  # Use message_id as reference
        zendesk_webhook_id=email_data.email_id,  # Resend email ID for dedup
        from_email=from_email.strip(),
        from_name=from_name,
        subject=email_data.subject,
        raw_body_html=email_html,
        raw_body_text=email_text,
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

    # Step 8: Transition to QUEUED status
    incoming_email.processing_status = "queued"
    db.commit()

    # Step 9: Enqueue Dramatiq job for async processing
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
