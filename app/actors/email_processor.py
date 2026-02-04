"""
Email Processing Actor
Dramatiq actor for asynchronous email processing with retry logic and state management
"""

import gc
import dramatiq
import structlog
import psutil
from datetime import datetime
from typing import Optional

logger = structlog.get_logger()


def should_retry(retries_so_far: int, exception: Exception) -> bool:
    """
    Determine if a failed job should be retried based on exception type.

    Retryable exceptions (transient failures):
    - RateLimitError (from anthropic SDK)
    - ConnectionError, TimeoutError
    - OperationalError (from sqlalchemy - database connection issues)

    Non-retryable exceptions (permanent failures):
    - BadRequestError (from anthropic - invalid request)
    - ValueError, KeyError (programming errors)

    Args:
        retries_so_far: Number of retries attempted so far
        exception: The exception that was raised

    Returns:
        True if should retry (and haven't exceeded max retries), False otherwise
    """
    # Lazy import to avoid import-time dependencies
    from sqlalchemy.exc import OperationalError

    # Check if this is a retryable exception type
    retryable_types = (
        ConnectionError,
        TimeoutError,
        OperationalError,
    )

    # Try to import anthropic exceptions (may not be installed)
    try:
        from anthropic import RateLimitError, BadRequestError
        retryable_types = retryable_types + (RateLimitError,)
        non_retryable_types = (BadRequestError,)
    except ImportError:
        non_retryable_types = ()

    # Permanent failures - do not retry
    permanent_failures = (ValueError, KeyError) + non_retryable_types
    if isinstance(exception, permanent_failures):
        logger.info("non_retryable_exception",
                   exception_type=type(exception).__name__,
                   retries=retries_so_far)
        return False

    # Transient failures - retry up to max
    if isinstance(exception, retryable_types):
        should_retry_flag = retries_so_far < 5
        logger.info("retryable_exception",
                   exception_type=type(exception).__name__,
                   retries=retries_so_far,
                   will_retry=should_retry_flag)
        return should_retry_flag

    # Unknown exception - retry to be safe
    logger.warning("unknown_exception_type",
                  exception_type=type(exception).__name__,
                  retries=retries_so_far)
    return retries_so_far < 5


def on_process_email_failure(message_data, exception):
    """
    Callback invoked by Dramatiq when email processing permanently fails.

    This is called ONLY when:
    1. All retries are exhausted (max_retries reached), OR
    2. A non-retryable exception was raised

    This ensures failure notifications are sent only for permanent failures,
    not for every transient retry.

    Args:
        message_data: Dramatiq message data (contains args/kwargs)
        exception: The exception that caused permanent failure
    """
    try:
        # Extract email_id from message
        email_id = None
        if hasattr(message_data, 'args') and len(message_data.args) > 0:
            email_id = message_data.args[0]
        elif hasattr(message_data, 'kwargs') and 'email_id' in message_data.kwargs:
            email_id = message_data.kwargs['email_id']

        if email_id is None:
            logger.error("on_failure_callback_no_email_id",
                        message=str(message_data),
                        error=str(exception))
            return

        logger.error("permanent_job_failure",
                    email_id=email_id,
                    error=str(exception),
                    exception_type=type(exception).__name__)

        # Lazy import to avoid circular dependencies
        from app.services.failure_notifier import notify_permanent_failure

        # Send failure notification (best-effort)
        notify_permanent_failure(email_id)

    except Exception as e:
        # Never crash the worker - this callback is best-effort
        logger.error("on_failure_callback_error",
                    error=str(e),
                    exc_info=True)


@dramatiq.actor(
    max_retries=5,
    min_backoff=15000,  # 15 seconds
    max_backoff=300000,  # 5 minutes
    retry_when=should_retry,
    on_failure=on_process_email_failure,
    queue_name="email_processing"
)
def process_email(email_id: int) -> None:
    """
    Process an incoming email asynchronously.

    This actor performs the full email processing pipeline:
    1. Load and lock email row (FOR UPDATE SKIP LOCKED)
    2. Parse email (clean HTML, count tokens)
    3. Extract entities with LLM (creditor name, debt amount, etc.)
    4. Match client and creditor in database
    5. Update creditor debt using DualDatabaseWriter saga pattern
    6. Send notification on successful auto-match

    State machine transitions:
    - received -> queued (done by webhook before enqueueing)
    - queued -> processing (start of this function)
    - processing -> completed (success)
    - processing -> failed (permanent failure after retries)
    - processing -> not_creditor_reply (if is_creditor_reply=False)

    Memory management:
    - Explicit gc.collect() after processing for 512MB memory constraint
    - psutil logging to track memory usage

    Args:
        email_id: The IncomingEmail.id to process

    Raises:
        Re-raises exceptions after updating email status to trigger Dramatiq retry logic
    """
    # Lazy imports to avoid circular dependencies and import-time side effects
    from app.database import SessionLocal
    from app.models import IncomingEmail

    # Log memory before processing
    process = psutil.Process()
    memory_before_mb = process.memory_info().rss / 1024 / 1024
    logger.info("process_email_start",
               email_id=email_id,
               memory_mb=round(memory_before_mb, 2))

    db = SessionLocal()
    try:
        # Step 1: Load and lock email row with FOR UPDATE SKIP LOCKED
        # This prevents duplicate processing by concurrent workers
        email = db.query(IncomingEmail).filter(
            IncomingEmail.id == email_id
        ).with_for_update(skip_locked=True).first()

        if email is None:
            # Row is locked by another worker or doesn't exist
            logger.warning("email_not_found_or_locked",
                          email_id=email_id)
            return

        # Check if already completed
        if email.processing_status in ("completed", "failed"):
            logger.info("email_already_processed",
                       email_id=email_id,
                       status=email.processing_status)
            return

        # Step 2: Transition to "processing" state
        email.processing_status = "processing"
        email.started_at = datetime.utcnow()
        db.commit()

        logger.info("email_processing_started",
                   email_id=email_id,
                   from_email=email.from_email,
                   subject=email.subject)

        # Lazy import processing dependencies
        from app.services.email_parser import email_parser
        from app.services.entity_extractor import entity_extractor as openai_extractor
        from app.services.entity_extractor_claude import entity_extractor_claude
        from app.services.dual_write import DualDatabaseWriter
        from app.services.idempotency import IdempotencyService, generate_idempotency_key
        from app.services.email_notifier import email_notifier
        from app.config import settings

        # Step 3: Parse email
        logger.info("parsing_email", email_id=email_id)
        parsed = email_parser.parse_email(
            html_body=email.raw_body_html,
            text_body=email.raw_body_text
        )

        # Store parsed data
        email.cleaned_body = parsed["cleaned_body"]
        email.token_count_before = parsed["token_count_before"]
        email.token_count_after = parsed["token_count_after"]
        email.processing_status = "parsed"
        db.commit()

        # Step 4: Extract entities with LLM
        logger.info("extracting_entities",
                   email_id=email_id,
                   llm_provider=settings.llm_provider)
        email.processing_status = "extracting"
        db.commit()

        # Choose LLM provider
        if settings.llm_provider == "claude":
            extractor = entity_extractor_claude
        else:
            extractor = openai_extractor

        extracted_entities = extractor.extract_entities(
            email_body=parsed["cleaned_body"],
            from_email=email.from_email,
            subject=email.subject
        )

        # Store extracted data
        email.extracted_data = extracted_entities.model_dump()
        email.processing_status = "extracted"
        db.commit()

        # Step 5: Check if this is actually a creditor reply
        if not extracted_entities.is_creditor_reply:
            logger.info("not_creditor_reply",
                       email_id=email_id)
            email.processing_status = "not_creditor_reply"
            email.match_status = "no_match"
            email.completed_at = datetime.utcnow()
            email.processed_at = datetime.utcnow()
            db.commit()
            return

        # Step 6: Match and write using DualDatabaseWriter saga pattern
        logger.info("matching_and_writing",
                   email_id=email_id,
                   client_name=extracted_entities.client_name,
                   creditor_name=extracted_entities.creditor_name)
        email.processing_status = "matching"
        db.commit()

        # Extract required data
        client_name = extracted_entities.client_name
        creditor_name = extracted_entities.creditor_name
        creditor_email = email.from_email
        new_debt_amount = extracted_entities.debt_amount

        # Validate required fields
        if not client_name or not (creditor_name or creditor_email) or not new_debt_amount:
            logger.warning("missing_required_fields",
                          email_id=email_id,
                          has_client=bool(client_name),
                          has_creditor=bool(creditor_name or creditor_email),
                          has_amount=bool(new_debt_amount))
            email.processing_status = "completed"
            email.match_status = "no_match"
            email.completed_at = datetime.utcnow()
            email.processed_at = datetime.utcnow()
            db.commit()
            return

        # Extract aktenzeichen from reference numbers
        client_aktenzeichen = None
        if extracted_entities.reference_numbers:
            for ref in extracted_entities.reference_numbers:
                if ref.isdigit() and len(ref) >= 4:
                    client_aktenzeichen = ref
                    break

        # Use creditor email as fallback if name not extracted
        creditor_name_or_email = creditor_name or creditor_email

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

        # Create DualDatabaseWriter with current session
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

        # Attempt MongoDB write (post-commit, compensatable)
        mongodb_success = False
        if result.get("outbox_message_id"):
            mongodb_success = dual_writer.execute_mongodb_write(result["outbox_message_id"])

        # Update match status based on MongoDB write result
        if mongodb_success:
            email.match_status = "auto_matched"
            email.match_confidence = 100
            logger.info("mongodb_update_success",
                       email_id=email_id,
                       client_name=client_name,
                       creditor_name=creditor_name_or_email,
                       amount=new_debt_amount)

            # Send email notification on successful auto-match
            email_notifier.send_debt_update_notification(
                client_name=client_name,
                creditor_name=creditor_name_or_email,
                creditor_email=creditor_email,
                old_debt_amount=None,
                new_debt_amount=new_debt_amount,
                side_conversation_id="N/A",
                zendesk_ticket_id=email.zendesk_ticket_id,
                reference_numbers=extracted_entities.reference_numbers,
                confidence_score=1.0
            )
        else:
            email.match_status = "no_match"
            logger.warning("mongodb_update_failed",
                          email_id=email_id,
                          client_name=client_name,
                          creditor_name=creditor_name_or_email)

        # Step 7: Mark as completed
        email.processing_status = "completed"
        email.completed_at = datetime.utcnow()
        email.processed_at = datetime.utcnow()
        db.commit()

        logger.info("email_processing_completed",
                   email_id=email_id,
                   match_status=email.match_status)

    except Exception as e:
        # Load email fresh and mark as failed
        logger.error("email_processing_error",
                    email_id=email_id,
                    error=str(e),
                    exception_type=type(e).__name__,
                    exc_info=True)

        try:
            email = db.query(IncomingEmail).filter(
                IncomingEmail.id == email_id
            ).first()
            if email:
                email.processing_status = "failed"
                email.processing_error = str(e)
                email.retry_count = (email.retry_count or 0) + 1
                db.commit()
        except Exception as commit_error:
            logger.error("failed_to_update_error_status",
                        email_id=email_id,
                        error=str(commit_error))

        # Re-raise to trigger Dramatiq retry logic
        # The on_failure callback will be invoked only after all retries exhausted
        raise

    finally:
        # Close database connection
        db.close()

        # Explicit garbage collection for memory stability (512MB constraint)
        gc.collect()

        # Log memory after gc
        memory_after_mb = process.memory_info().rss / 1024 / 1024
        logger.info("process_email_complete",
                   email_id=email_id,
                   memory_before_mb=round(memory_before_mb, 2),
                   memory_after_mb=round(memory_after_mb, 2),
                   memory_freed_mb=round(memory_before_mb - memory_after_mb, 2))
