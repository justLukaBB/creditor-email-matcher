"""
Webhook Router
Handles incoming Zendesk webhooks for creditor emails
"""

from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import hashlib
import hmac
import logging

from app.database import get_db, SessionLocal
from app.config import settings
from app.models.webhook_schemas import ZendeskWebhookEmail, WebhookResponse
from app.models import IncomingEmail, MatchResult as DBMatchResult
from app.services.email_parser import email_parser
from app.services.entity_extractor import entity_extractor as openai_extractor
from app.services.entity_extractor_claude import entity_extractor_claude
from app.services.matching_engine import MatchingEngine
from app.services.zendesk_client import zendesk_client
from app.services.mongodb_client import mongodb_service
from app.services.email_notifier import email_notifier
from app.services.idempotency import IdempotencyService, generate_idempotency_key
from app.services.dual_write import DualDatabaseWriter
import json

logger = logging.getLogger(__name__)

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
    background_tasks: BackgroundTasks,
    x_zendesk_signature: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """
    Receive incoming email webhook from Zendesk

    This endpoint:
    1. Validates webhook signature
    2. Stores incoming email
    3. Processes email (parse, extract, match) in background
    4. Returns quick acknowledgment

    Args:
        webhook_data: Email data from Zendesk
        x_zendesk_signature: Webhook signature header
        db: Database session

    Returns:
        WebhookResponse with processing status
    """
    logger.info(
        f"Webhook received - Ticket: {webhook_data.ticket_id}, "
        f"From: {webhook_data.from_email}"
    )

    # Step 1: Verify webhook signature (if configured)
    if settings.webhook_secret:
        payload_str = webhook_data.model_dump_json()
        if not verify_webhook_signature(payload_str, x_zendesk_signature, settings.webhook_secret):
            logger.warning(f"Invalid webhook signature from {webhook_data.from_email}")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    email_id = None

    # Step 2: Check for duplicate and store email (only if PostgreSQL is available)
    if db is not None:
        if webhook_data.webhook_id:
            existing = db.query(IncomingEmail).filter(
                IncomingEmail.zendesk_webhook_id == webhook_data.webhook_id
            ).first()
            if existing:
                logger.info(f"Duplicate webhook ignored: {webhook_data.webhook_id}")
                return WebhookResponse(
                    status="duplicate",
                    message="Email already processed",
                    email_id=existing.id
                )

        # Step 3: Parse received_at with fallback
        received_at = datetime.utcnow()  # Default to now
        if webhook_data.received_at:
            try:
                # Try to parse various datetime formats
                from dateutil import parser as date_parser
                received_at = date_parser.parse(webhook_data.received_at)
            except Exception as e:
                logger.warning(f"Could not parse received_at '{webhook_data.received_at}': {e}, using current time")

        # Step 4: Store incoming email
        incoming_email = IncomingEmail(
            zendesk_ticket_id=webhook_data.ticket_id.strip(),
            zendesk_webhook_id=webhook_data.webhook_id,
            from_email=webhook_data.from_email.strip(),
            from_name=webhook_data.from_name.strip() if webhook_data.from_name else None,
            subject=webhook_data.subject.strip() if webhook_data.subject else None,
            raw_body_html=webhook_data.body_html,
            raw_body_text=webhook_data.body_text,
            received_at=received_at,
            processing_status="received"
        )
        db.add(incoming_email)
        db.commit()
        db.refresh(incoming_email)

        email_id = incoming_email.id
        logger.info(f"Email stored - ID: {email_id}")
    else:
        logger.info("PostgreSQL not configured - processing email directly without logging")

    # Step 5: Process email asynchronously
    background_tasks.add_task(
        process_incoming_email,
        email_id=email_id,
        webhook_data=webhook_data if email_id is None else None
    )

    return WebhookResponse(
        status="accepted",
        message="Email queued for processing",
        email_id=email_id
    )


async def process_incoming_email(email_id: int = None, webhook_data: ZendeskWebhookEmail = None):
    """
    Background task to process incoming email

    Steps:
    1. Parse and clean email
    2. Extract entities with LLM
    3. Find matches using matching engine
    4. Store results
    5. Route based on confidence

    Args:
        email_id: PostgreSQL email ID (if available)
        webhook_data: Raw webhook data (if PostgreSQL not available)
    """
    # Create new DB session for background task (if PostgreSQL available)
    db = SessionLocal() if SessionLocal is not None else None

    try:
        # Load email data from PostgreSQL or webhook
        if email_id is not None and db is not None:
            # PostgreSQL mode - load from database
            email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()
            if not email:
                logger.error(f"Email {email_id} not found")
                return

            logger.info(f"Processing email {email_id}")
            email.processing_status = "parsing"
            db.commit()

            # Extract data from database model
            raw_body_html = email.raw_body_html
            raw_body_text = email.raw_body_text
            from_email = email.from_email
            subject = email.subject
            zendesk_ticket_id = email.zendesk_ticket_id
        else:
            # MongoDB-only mode - use webhook data directly
            logger.info(f"Processing email from {webhook_data.from_email} (MongoDB-only mode)")
            email = None  # No database record
            raw_body_html = webhook_data.body_html
            raw_body_text = webhook_data.body_text
            from_email = webhook_data.from_email
            subject = webhook_data.subject
            zendesk_ticket_id = webhook_data.ticket_id

        # Step 1: Parse and clean email
        parsed = email_parser.parse_email(
            html_body=raw_body_html,
            text_body=raw_body_text
        )

        if email is not None and db is not None:
            email.cleaned_body = parsed["cleaned_body"]
            email.token_count_before = parsed["token_count_before"]
            email.token_count_after = parsed["token_count_after"]
            email.processing_status = "parsed"
            db.commit()

        # Step 2: Extract entities with LLM
        logger.info(f"Extracting entities using {settings.llm_provider}")
        if email is not None and db is not None:
            email.processing_status = "extracting"
            db.commit()

        # Choose LLM provider
        if settings.llm_provider == "claude":
            extractor = entity_extractor_claude
        else:
            extractor = openai_extractor

        extracted_entities = extractor.extract_entities(
            email_body=parsed["cleaned_body"],
            from_email=from_email,
            subject=subject
        )

        # Store extracted data
        if email is not None and db is not None:
            email.extracted_data = extracted_entities.model_dump()
            email.processing_status = "extracted"
            db.commit()

        # Check if this is actually a creditor reply
        if not extracted_entities.is_creditor_reply:
            logger.info(f"Email is not a creditor reply - skipping matching")
            if email is not None and db is not None:
                email.processing_status = "not_creditor_reply"
                email.match_status = "no_match"
                db.commit()
            return

        # Step 3: Use DualDatabaseWriter saga pattern for MongoDB updates
        logger.info(f"Processing creditor debt update using DualDatabaseWriter saga pattern")
        if email is not None and db is not None:
            email.processing_status = "matching"
            db.commit()

        # Extract required data for matching
        client_name = extracted_entities.client_name
        creditor_name = extracted_entities.creditor_name
        creditor_email = from_email
        new_debt_amount = extracted_entities.debt_amount

        # Check if we have the required data
        # We need at least: client_name, (creditor_name OR creditor_email), and new_debt_amount
        mongodb_success = False
        if client_name and (creditor_name or creditor_email) and new_debt_amount:
            # Extract aktenzeichen from reference numbers if present
            client_aktenzeichen = None
            if extracted_entities.reference_numbers:
                # Try to find a reference number that looks like an aktenzeichen
                for ref in extracted_entities.reference_numbers:
                    if ref.isdigit() and len(ref) >= 4:  # e.g., "542900"
                        client_aktenzeichen = ref
                        break

            logger.info(
                f"MongoDB matching - Client: {client_name}, "
                f"AZ: {client_aktenzeichen}, Creditor: {creditor_name or creditor_email}"
            )

            # Use email as fallback if creditor name not extracted
            creditor_name_or_email = creditor_name or creditor_email
            logger.info(
                f"Email {email_id} - Processing MongoDB update for "
                f"Client: {client_name}, Creditor: {creditor_name_or_email}, Amount: {new_debt_amount}"
            )

            # Use DualDatabaseWriter for saga pattern (PostgreSQL mode only)
            if email is not None and db is not None:
                # Generate idempotency key
                idempotency_key = generate_idempotency_key(
                    operation="creditor_debt_update",
                    aggregate_id=str(email_id),
                    payload={
                        "client_name": client_name,
                        "creditor_email": creditor_email,
                        "amount": new_debt_amount
                    }
                )

                # Create DualDatabaseWriter with current session and idempotency service
                idempotency_svc = IdempotencyService(SessionLocal)
                dual_writer = DualDatabaseWriter(db, idempotency_svc)

                # Execute saga pattern: PG write + outbox (atomic)
                result = dual_writer.update_creditor_debt(
                    email_id=email_id,
                    client_name=client_name,
                    client_aktenzeichen=client_aktenzeichen,
                    creditor_email=creditor_email,
                    creditor_name=creditor_name_or_email,
                    new_debt_amount=new_debt_amount,
                    response_text=extracted_entities.summary,
                    reference_numbers=extracted_entities.reference_numbers,
                    idempotency_key=idempotency_key
                )

                # Commit PostgreSQL transaction (outbox message included atomically)
                db.commit()

                # Now attempt MongoDB write (post-commit, compensatable)
                if result.get("outbox_message_id"):
                    mongodb_success = dual_writer.execute_mongodb_write(result["outbox_message_id"])
            else:
                # MongoDB-only mode - direct update without saga pattern
                mongodb_success = mongodb_service.update_creditor_debt_amount(
                    client_name=client_name,
                    client_aktenzeichen=client_aktenzeichen,
                    creditor_email=creditor_email,
                    creditor_name=creditor_name_or_email,
                    new_debt_amount=new_debt_amount,
                    response_text=extracted_entities.summary,
                    reference_numbers=extracted_entities.reference_numbers
                )

            if mongodb_success:
                if email is not None and db is not None:
                    email.match_status = "auto_matched"
                    email.match_confidence = 100

                logger.info(
                    f"✅ MongoDB updated successfully - "
                    f"Client: {client_name}, Creditor: {creditor_name}, Amount: {new_debt_amount}"
                )

                # Send email notification to glaubiger@scuric.zendesk.com
                email_notifier.send_debt_update_notification(
                    client_name=client_name,
                    creditor_name=creditor_name_or_email,  # Use fallback
                    creditor_email=creditor_email,
                    old_debt_amount=None,  # We don't have the old amount from extracted data
                    new_debt_amount=new_debt_amount,
                    side_conversation_id="N/A",
                    zendesk_ticket_id=zendesk_ticket_id,
                    reference_numbers=extracted_entities.reference_numbers,
                    confidence_score=1.0
                )
            else:
                if email is not None and db is not None:
                    email.match_status = "no_match"

                logger.warning(
                    f"MongoDB update failed - Client or creditor not found in MongoDB. "
                    f"Client: {client_name}, Creditor: {creditor_name}"
                )
        else:
            # Missing required data for MongoDB update
            if email is not None and db is not None:
                email.match_status = "no_match"

            logger.warning(
                f"Missing required data for MongoDB update - "
                f"Client: {client_name}, Creditor: {creditor_name}, Amount: {new_debt_amount}"
            )

        if email is not None and db is not None:
            email.processing_status = "completed"
            email.processed_at = datetime.utcnow()
            email.matched_at = datetime.utcnow()
            db.commit()

        logger.info(f"Email processing complete - MongoDB-only mode" if email is None else f"Email {email_id} processing complete - Status: {email.match_status}")

    except Exception as e:
        logger.error(f"Error processing email: {e}", exc_info=True)
        if email_id is not None and db is not None:
            email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()
            if email:
                email.processing_status = "failed"
                email.processing_error = str(e)
                db.commit()

    finally:
        if db is not None:
            db.close()


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
