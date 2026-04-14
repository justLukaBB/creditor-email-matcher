"""
Email Processing Actor
Dramatiq actor for asynchronous email processing with retry logic and state management
"""

import gc
import time
import dramatiq
import logging
import psutil
from datetime import datetime
from typing import Optional
from asgi_correlation_id.context import correlation_id as correlation_id_ctx
from app.services.monitoring.metrics import MetricsCollector
from app.services.monitoring.error_tracking import set_processing_context, add_breadcrumb
from app.services.processing_reports import create_processing_report

logger = logging.getLogger(__name__)


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
                   extra={"exception_type": type(exception).__name__,
                          "retries": retries_so_far})
        return False

    # Transient failures - retry up to max
    if isinstance(exception, retryable_types):
        should_retry_flag = retries_so_far < 5
        logger.info("retryable_exception",
                   extra={"exception_type": type(exception).__name__,
                          "retries": retries_so_far,
                          "will_retry": should_retry_flag})
        return should_retry_flag

    # Unknown exception - retry to be safe
    logger.warning("unknown_exception_type",
                  extra={"exception_type": type(exception).__name__,
                         "retries": retries_so_far})
    return retries_so_far < 5


def _confidence_to_float(confidence: str) -> float:
    """Convert confidence level to float for backward compatibility."""
    mapping = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}
    return mapping.get(confidence, 0.5)


def _get_redis_client():
    """Get Redis client if available."""
    from app.config import settings
    if settings.redis_url:
        import redis
        return redis.from_url(settings.redis_url)
    return None


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
                        extra={"message": str(message_data),
                               "error": str(exception)})
            return

        logger.error("permanent_job_failure",
                    extra={"email_id": email_id,
                           "error": str(exception),
                           "exception_type": type(exception).__name__})

        # Lazy import to avoid circular dependencies
        from app.services.failure_notifier import notify_permanent_failure

        # Send failure notification (best-effort)
        notify_permanent_failure(email_id)

    except Exception as e:
        # Never crash the worker - this callback is best-effort
        logger.error("on_failure_callback_error",
                    extra={"error": str(e)},
                    exc_info=True)


@dramatiq.actor(
    max_retries=5,
    min_backoff=15000,  # 15 seconds
    max_backoff=300000,  # 5 minutes
    retry_when=should_retry,
    queue_name="email_processing"
)
# Note: on_failure removed - use Dramatiq's builtin error handling instead
def process_email(email_id: int, correlation_id: str = None) -> None:
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
        correlation_id: Optional correlation ID from originating request for tracing

    Raises:
        Re-raises exceptions after updating email status to trigger Dramatiq retry logic
    """
    # Re-establish correlation ID context for this actor execution
    # This is necessary because Dramatiq actors run in separate threads/processes
    # where the original HTTP request's async context is not available
    if correlation_id:
        correlation_id_ctx.set(correlation_id)
    # Lazy imports to avoid circular dependencies and import-time side effects
    from app.database import init_db
    import app.database as database
    from app.models import IncomingEmail

    # Ensure database is initialized in worker process
    if database.SessionLocal is None:
        init_db()

    SessionLocal = database.SessionLocal

    # Log memory before processing
    process = psutil.Process()
    memory_before_mb = process.memory_info().rss / 1024 / 1024
    logger.info("process_email_start",
               extra={"email_id": email_id,
                      "memory_mb": round(memory_before_mb, 2)})

    # Record start time for duration tracking
    start_time = time.time()

    db = SessionLocal()
    try:
        # Restore correlation ID context for this actor execution
        if correlation_id:
            correlation_id_ctx.set(correlation_id)

        # Set Sentry context at actor start
        set_processing_context(
            email_id=email_id,
            actor="process_email",
            correlation_id=correlation_id or "none"
        )

        # Initialize metrics collector
        metrics = MetricsCollector(db)
        # Step 1: Load and lock email row with FOR UPDATE SKIP LOCKED
        # This prevents duplicate processing by concurrent workers
        email = db.query(IncomingEmail).filter(
            IncomingEmail.id == email_id
        ).with_for_update(skip_locked=True).first()

        if email is None:
            # Row is locked by another worker or doesn't exist
            logger.warning("email_not_found_or_locked",
                          extra={"email_id": email_id})
            return

        # Check if already completed
        if email.processing_status in ("completed", "failed"):
            logger.info("email_already_processed",
                       extra={"email_id": email_id,
                              "status": email.processing_status})
            return

        # Step 2: Transition to "processing" state
        email.processing_status = "processing"
        email.started_at = datetime.utcnow()
        db.commit()

        logger.info("email_processing_started",
                   extra={"email_id": email_id,
                          "from_email": email.from_email,
                          "subject": email.subject})

        # Lazy import processing dependencies
        from app.services.email_parser import email_parser
        from app.services.entity_extractor import entity_extractor as openai_extractor
        from app.services.entity_extractor_claude import entity_extractor_claude
        from app.services.dual_write import DualDatabaseWriter
        from app.services.idempotency import IdempotencyService, generate_idempotency_key
        from app.services.email_notifier import email_notifier
        from app.actors.content_extractor import ContentExtractionService
        from app.services.matching_engine_v2 import MatchingEngineV2
        from app.services.review_queue import enqueue_ambiguous_match
        from app.config import settings

        # Step 3: Parse email
        logger.info("parsing_email", extra={"email_id": email_id})
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

        # ========================================
        # PHASE 4: Deterministic Routing
        # ========================================
        # Attempt to route email without LLM processing.
        # If a deterministic match is found, skip Agents 1-3 and MatchingEngineV2.

        from app.services.deterministic_router import DeterministicRouter

        det_router = DeterministicRouter(db, kanzlei_id=email.kanzlei_id)
        det_result = det_router.route(
            to_addresses=email.to_addresses,
            in_reply_to=email.in_reply_to_header,
            message_id=email.zendesk_ticket_id,
            from_email=email.from_email,
            subject=email.subject,
            body_text=email.raw_body_text,
            body_html=email.raw_body_html,
        )

        # Persist routing attempt on email record
        email.deterministic_match = det_result.matched
        if det_result.matched:
            email.routing_method = det_result.routing_method
            email.routing_id_parsed = det_result.routing_id_parsed
            email.deterministic_confidence = det_result.confidence
            email.deterministic_inquiry_id = det_result.inquiry_id
        db.commit()

        if det_result.matched:
            logger.info("deterministic_routing_success", extra={
                "email_id": email_id,
                "method": det_result.routing_method,
                "inquiry_id": det_result.inquiry_id,
                "confidence": det_result.confidence,
            })
            add_breadcrumb(
                "pipeline",
                f"Deterministic match: {det_result.routing_method}",
                data={"inquiry_id": det_result.inquiry_id, "confidence": det_result.confidence}
            )

            # Skip entire LLM pipeline — go straight to matched inquiry processing
            matched_inquiry = det_result.inquiry
            email.matched_inquiry_id = matched_inquiry.id
            email.match_status = "auto_matched"
            email.match_confidence = int(det_result.confidence * 100)
            email.processing_status = "matching"

            # Set confidence values — deterministic match is high confidence
            email.extraction_confidence = int(det_result.confidence * 100)
            email.overall_confidence = int(det_result.confidence * 100)
            email.confidence_route = "high"
            db.commit()

            # Dual-write to MongoDB
            from app.services.idempotency import IdempotencyService, generate_idempotency_key
            from app.services.dual_write import DualDatabaseWriter

            # Still need basic entity extraction for debt amount
            # Use Claude for quick extraction on deterministic matches
            from app.services.entity_extractor_claude import entity_extractor_claude

            # Prefer raw body for deterministic matches — the email parser
            # can strip the entire body (100% reduction) when EmailReplyParser
            # misclassifies the content as quoted text.
            email_body_for_extraction = (
                email.raw_body_text or email.raw_body_html or email.cleaned_body
            )
            extracted_entities = None
            new_debt_amount = None
            reference_numbers = []
            client_name = matched_inquiry.client_name
            creditor_name = matched_inquiry.creditor_name or email.from_email
            client_aktenzeichen = matched_inquiry.reference_number

            if email_body_for_extraction:
                try:
                    extracted_entities = entity_extractor_claude.extract_entities(
                        email_body=email_body_for_extraction,
                        from_email=email.from_email,
                        subject=email.subject,
                        email_id=email_id,
                        db=db,
                    )
                    if extracted_entities:
                        new_debt_amount = extracted_entities.debt_amount
                        reference_numbers = extracted_entities.reference_numbers or []
                        email.extracted_data = {
                            "is_creditor_reply": True,
                            "client_name": extracted_entities.client_name or client_name,
                            "creditor_name": extracted_entities.creditor_name or creditor_name,
                            "debt_amount": new_debt_amount,
                            "reference_numbers": reference_numbers,
                            "routing_method": det_result.routing_method,
                        }
                        db.commit()
                except Exception as extract_err:
                    logger.warning("deterministic_extraction_failed", extra={
                        "email_id": email_id, "error": str(extract_err),
                        "error_type": type(extract_err).__name__,
                        "body_length": len(email_body_for_extraction) if email_body_for_extraction else 0,
                    })

            # Determine letter type
            letter_type = getattr(matched_inquiry, 'letter_type', 'first') or 'first'

            if letter_type == 'second':
                # Settlement response — delegate to existing handler
                from app.services.confidence import calculate_overall_confidence, route_by_confidence
                confidence_result_obj = type('C', (), {
                    'extraction': det_result.confidence,
                    'match': det_result.confidence,
                    'overall': det_result.confidence,
                    'weakest_link': 'deterministic',
                })()
                route = route_by_confidence(det_result.confidence)

                _process_second_round(
                    db=db,
                    email=email,
                    email_id=email_id,
                    matched_inquiry=matched_inquiry,
                    matching_result=None,
                    client_name=client_name,
                    client_aktenzeichen=client_aktenzeichen,
                    creditor_email=email.from_email,
                    creditor_name=creditor_name,
                    email_body=email_body_for_extraction,
                    subject=email.subject,
                    confidence_result=confidence_result_obj,
                    route=route,
                )
            else:
                # First round — apply amount update guard and dual-write
                from app.services.amount_update_guard import should_update_amount

                # Extract creditor position from routing ID (e.g. "ES-A4234-04" → 4)
                creditor_position = None
                if det_result.routing_id_parsed:
                    import re as _re
                    pos_match = _re.search(r'-(\d{2,})$', det_result.routing_id_parsed)
                    if pos_match:
                        creditor_position = int(pos_match.group(1))

                guard_ok, guard_reason = should_update_amount(
                    existing_amount=getattr(matched_inquiry, 'debt_amount', None),
                    new_amount=new_debt_amount,
                    confidence=det_result.confidence,
                )

                if guard_ok and new_debt_amount is not None:
                    idempotency_key = generate_idempotency_key(
                        operation="creditor_debt_update",
                        aggregate_id=str(email_id),
                        payload={
                            "client_name": client_name,
                            "creditor_email": email.from_email,
                            "amount": new_debt_amount,
                        }
                    )
                    idempotency_svc = IdempotencyService(SessionLocal)
                    dual_writer = DualDatabaseWriter(db, idempotency_svc)

                    result = dual_writer.update_creditor_debt(
                        email_id=email_id,
                        client_name=client_name,
                        client_aktenzeichen=client_aktenzeichen,
                        creditor_email=email.from_email,
                        creditor_name=creditor_name,
                        new_debt_amount=new_debt_amount,
                        response_text=None,
                        reference_numbers=reference_numbers,
                        idempotency_key=idempotency_key,
                        extraction_confidence=det_result.confidence,
                        creditor_position=creditor_position,
                    )
                    db.commit()

                    if result.get("outbox_message_id"):
                        dual_writer.execute_mongodb_write(result["outbox_message_id"])

                # Notify portal
                from app.services.portal_notifier import notify_creditor_response
                notify_creditor_response(
                    email_id=email_id,
                    client_aktenzeichen=client_aktenzeichen,
                    client_name=client_name,
                    creditor_name=creditor_name,
                    creditor_email=email.from_email,
                    new_debt_amount=new_debt_amount,
                    amount_source="creditor_response",
                    extraction_confidence=det_result.confidence,
                    match_status="auto_matched",
                    confidence_route="high",
                    needs_review=False,
                    reference_numbers=reference_numbers,
                    email_subject=email.subject,
                    email_body_preview=email.raw_body_text,
                    attachment_urls=email.attachment_urls,
                    resend_email_id=email.zendesk_webhook_id,
                    routing_method=det_result.routing_method,
                    routing_id=det_result.routing_id_parsed,
                    deterministic_match=True,
                    kanzlei_id=getattr(matched_inquiry, 'kanzlei_id', None),
                )

            # Mark completed and create processing report
            email.processing_status = "completed"
            email.completed_at = datetime.utcnow()
            email.processed_at = datetime.utcnow()

            duration_ms = int((time.time() - start_time) * 1000)
            try:
                create_processing_report(
                    db=db,
                    email_id=email_id,
                    extracted_data=email.extracted_data or {},
                    agent_checkpoints={"deterministic_routing": {
                        "method": det_result.routing_method,
                        "inquiry_id": det_result.inquiry_id,
                        "confidence": det_result.confidence,
                    }},
                    overall_confidence=det_result.confidence,
                    confidence_route="high",
                    needs_review=False,
                    review_reason=None,
                    processing_time_ms=duration_ms,
                )
            except Exception as report_error:
                logger.warning("processing_report_failed", extra={"error": str(report_error), "email_id": email_id})

            db.commit()
            logger.info("deterministic_processing_completed", extra={
                "email_id": email_id,
                "method": det_result.routing_method,
                "duration_ms": duration_ms,
            })

            # Record metric
            metrics.record_confidence("high", det_result.confidence, email_id=email_id)

            # Jump to finally block — skip entire LLM pipeline
            return

        # ========================================
        # PHASE 5: Multi-Agent Pipeline
        # ========================================

        # Stage 1: Intent Classification (Agent 1)
        logger.info("agent1_intent_classification_started", extra={"email_id": email_id})
        email.processing_status = "intent_classifying"
        db.commit()

        from app.actors.intent_classifier import classify_intent
        intent_result = classify_intent(email_id)

        logger.info("agent1_intent_classification_completed",
                   extra={"email_id": email_id,
                          "intent": intent_result.get("intent"),
                          "confidence": intent_result.get("confidence"),
                          "skip_extraction": intent_result.get("skip_extraction")})

        # Add breadcrumb for intent classification
        add_breadcrumb(
            "pipeline",
            f"Intent: {intent_result.get('intent')}",
            data={"intent": intent_result.get("intent"), "confidence": intent_result.get("confidence")}
        )

        # Handle skip_extraction intents (auto_reply, spam)
        if intent_result.get("skip_extraction"):
            logger.info("skip_extraction_intent_detected",
                       extra={"email_id": email_id,
                              "intent": intent_result.get("intent")})
            email.processing_status = "not_creditor_reply"
            email.match_status = "no_match"
            email.completed_at = datetime.utcnow()
            email.processed_at = datetime.utcnow()

            # Store intent in extracted_data for reference
            email.extracted_data = {
                "is_creditor_reply": False,
                "intent": intent_result.get("intent"),
                "confidence": intent_result.get("confidence"),
                "method": "intent_classification"
            }
            db.commit()

            # Notify portal so ALL emails appear in admin inbox
            from app.services.portal_notifier import notify_creditor_response
            notify_creditor_response(
                email_id=email_id,
                client_aktenzeichen=None,
                client_name=None,
                creditor_name=email.from_email or "unknown",
                creditor_email=email.from_email or "",
                new_debt_amount=None,
                amount_source="none",
                extraction_confidence=None,
                match_status="no_match",
                confidence_route="skip_extraction",
                needs_review=False,
                reference_numbers=[],
                email_subject=email.subject,
                email_body_preview=email.raw_body_text or email.raw_body_html,
                intent=intent_result.get("intent"),
                attachment_urls=email.attachment_urls,
                resend_email_id=email.zendesk_webhook_id,
            )
            return

        # Stage 2: Content Extraction (Agent 2)
        logger.info("agent2_content_extraction_started", extra={"email_id": email_id})
        email.processing_status = "content_extracting"
        db.commit()

        # Get email body for extraction (use cleaned_body if available)
        email_body_for_extraction = (
            email.cleaned_body or email.raw_body_text or email.raw_body_html
        )

        # Get attachment URLs from JSON column (populated by webhook in Phase 2)
        attachment_urls = email.attachment_urls or []

        # Enrich attachments with download URLs from Resend API
        if attachment_urls and email.zendesk_webhook_id:
            from app.services.resend_client import enrich_attachments_with_download_urls
            logger.info("enriching_attachments",
                       extra={"count": len(attachment_urls),
                              "resend_email_id": email.zendesk_webhook_id})
            attachment_urls = enrich_attachments_with_download_urls(
                resend_email_id=email.zendesk_webhook_id,
                attachments=attachment_urls
            )
            logger.info("attachments_enriched",
                       extra={"count": len(attachment_urls),
                              "with_urls": sum(1 for a in attachment_urls if a.get("url"))})

        # Call Agent 2 extraction with intent_result
        from app.actors.content_extractor import extract_content
        extraction_result = extract_content(
            email_id=email_id,
            email_body=email_body_for_extraction,
            attachment_urls=attachment_urls,
            intent_result=intent_result
        )

        logger.info("agent2_content_extraction_completed",
                   extra={"email_id": email_id,
                          "amount": extraction_result.get("gesamtforderung"),
                          "confidence": extraction_result.get("confidence"),
                          "sources": extraction_result.get("sources_processed"),
                          "needs_review": extraction_result.get("needs_review")})

        # Add breadcrumb for extraction
        add_breadcrumb(
            "pipeline",
            f"Extracted amount: {extraction_result.get('gesamtforderung')}",
            data={"sources": extraction_result.get("sources_processed", 0)}
        )

        # Record token usage from extraction
        total_tokens = extraction_result.get("total_tokens_used", 0)
        if total_tokens > 0:
            metrics.record_token_usage(
                model="claude-sonnet",
                operation="extraction",
                tokens=total_tokens,
                email_id=email_id
            )

        # Stage 3: Consolidation (Agent 3)
        logger.info("agent3_consolidation_started", extra={"email_id": email_id})
        email.processing_status = "consolidating"
        db.commit()

        from app.actors.consolidation_agent import consolidate_results
        consolidation_result = consolidate_results(email_id)

        logger.info("agent3_consolidation_completed",
                   extra={"email_id": email_id,
                          "final_amount": consolidation_result.get("final_amount"),
                          "conflicts_detected": consolidation_result.get("conflicts_detected"),
                          "needs_review": consolidation_result.get("needs_review"),
                          "validation_status": consolidation_result.get("validation_status")})

        # Add breadcrumb for consolidation
        add_breadcrumb(
            "pipeline",
            f"Consolidation: {consolidation_result.get('validation_status')}",
            data={"conflicts": consolidation_result.get("conflicts_detected", 0)}
        )

        # Store pipeline results in extracted_data with pipeline_metadata
        email.extracted_data = {
            "is_creditor_reply": True,
            "client_name": consolidation_result.get("client_name"),
            "creditor_name": consolidation_result.get("creditor_name"),
            "debt_amount": consolidation_result.get("final_amount"),
            "reference_numbers": [],  # Phase 4 will extract reference numbers
            "confidence": consolidation_result.get("confidence", 0.7),
            "pipeline_metadata": {
                "intent": intent_result.get("intent"),
                "intent_confidence": intent_result.get("confidence"),
                "sources_processed": consolidation_result.get("sources_processed", 0),
                "total_tokens_used": consolidation_result.get("total_tokens_used", 0),
                "conflicts_detected": consolidation_result.get("conflicts_detected", 0),
                "needs_review": consolidation_result.get("needs_review", False),
                "validation_status": consolidation_result.get("validation_status"),
                "method": "multi_agent_pipeline"
            }
        }

        # Enqueue to ManualReviewQueue if needs_review
        if consolidation_result.get("needs_review"):
            logger.info("enqueuing_for_manual_review", extra={"email_id": email_id})
            from app.services.validation import enqueue_for_review

            # Determine review reason
            conflicts = consolidation_result.get("conflicts_detected", 0)
            if conflicts > 0:
                reason = "conflict_detected"
                details = {
                    "conflicts": consolidation_result.get("conflicts", []),
                    "confidence": consolidation_result.get("confidence")
                }
            else:
                reason = "low_confidence"
                details = {
                    "confidence": consolidation_result.get("confidence"),
                    "threshold": 0.7
                }

            enqueue_for_review(db, email_id, reason, details)

        email.processing_status = "content_extracted"
        db.commit()

        logger.info("multi_agent_pipeline_completed",
                   extra={"email_id": email_id,
                          "amount": consolidation_result.get("final_amount"),
                          "needs_review": consolidation_result.get("needs_review")})

        # Step 4: Extract entities with LLM (for reference_numbers and summary)
        logger.info("extracting_entities",
                   extra={"email_id": email_id,
                          "llm_provider": settings.llm_provider})
        email.processing_status = "extracting"
        db.commit()

        # Choose LLM provider
        if settings.llm_provider == "claude":
            extractor = entity_extractor_claude
        else:
            extractor = openai_extractor

        # Get attachment texts from extraction result for entity extraction
        attachment_texts = extraction_result.get("attachment_texts", [])

        extracted_entities = extractor.extract_entities(
            email_body=parsed["cleaned_body"],
            from_email=email.from_email,
            subject=email.subject,
            attachment_texts=attachment_texts if attachment_texts else None
        )

        # Merge entity extraction results with Phase 3 extraction
        # Phase 3 provides: debt_amount (from attachments), client_name, creditor_name
        # Entity extraction provides: is_creditor_reply, reference_numbers, summary
        # Priority: Phase 3 debt_amount (processes attachments), entity extraction for intent
        current_extracted_data = email.extracted_data or {}

        # Override is_creditor_reply if pipeline extracted a debt amount
        # A debt amount from attachments/body is strong signal this is a creditor reply,
        # regardless of what the entity extractor thinks about the email body alone
        pipeline_has_amount = current_extracted_data.get("debt_amount") is not None
        non_creditor_intents = {"auto_reply", "spam"}
        intent_value = intent_result.get("intent", "")

        if pipeline_has_amount and intent_value not in non_creditor_intents:
            is_creditor_reply = True
            logger.info("is_creditor_override",
                       extra={"email_id": email_id,
                              "reason": "pipeline_extracted_amount",
                              "intent": intent_value,
                              "amount": current_extracted_data.get("debt_amount")})
        else:
            is_creditor_reply = extracted_entities.is_creditor_reply

        # Merge: Phase 3 pipeline data takes priority over entity extraction.
        # Use `is not None` instead of `or` to preserve falsy values like 0.0 and "".
        current_client = current_extracted_data.get("client_name")
        current_creditor = current_extracted_data.get("creditor_name")
        current_amount = current_extracted_data.get("debt_amount")

        email.extracted_data = {
            "is_creditor_reply": is_creditor_reply,
            "client_name": current_client if current_client is not None else extracted_entities.client_name,
            "creditor_name": current_creditor if current_creditor is not None else extracted_entities.creditor_name,
            "debt_amount": current_amount if current_amount is not None else extracted_entities.debt_amount,
            "reference_numbers": extracted_entities.reference_numbers or [],
            "confidence": current_extracted_data.get("confidence", 0.5),
            "summary": extracted_entities.summary,
            "extraction_metadata": current_extracted_data.get("extraction_metadata", {})
        }
        email.processing_status = "extracted"
        db.commit()

        # Step 5: Check if this is actually a creditor reply
        if not is_creditor_reply:
            logger.info("not_creditor_reply",
                       extra={"email_id": email_id})
            email.processing_status = "not_creditor_reply"
            email.match_status = "no_match"
            email.completed_at = datetime.utcnow()
            email.processed_at = datetime.utcnow()
            db.commit()

            # Notify portal so ALL emails appear in admin inbox
            from app.services.portal_notifier import notify_creditor_response
            notify_creditor_response(
                email_id=email_id,
                client_aktenzeichen=None,
                client_name=extracted_entities.client_name if extracted_entities else None,
                creditor_name=extracted_entities.creditor_name if extracted_entities else (email.from_email or "unknown"),
                creditor_email=email.from_email or "",
                new_debt_amount=current_extracted_data.get("debt_amount"),
                amount_source="not_creditor_reply",
                extraction_confidence=extracted_entities.confidence if extracted_entities else None,
                match_status="no_match",
                confidence_route="not_creditor_reply",
                needs_review=False,
                reference_numbers=extracted_entities.reference_numbers if extracted_entities else [],
                email_subject=email.subject,
                email_body_preview=email.raw_body_text or email.raw_body_html,
                intent=intent_result.get("intent"),
                attachment_urls=email.attachment_urls,
                resend_email_id=email.zendesk_webhook_id,
            )
            return

        # ========================================
        # PHASE 6: MatchingEngineV2 Integration
        # ========================================

        # Step 6: Match using MatchingEngineV2
        logger.info("matching_started",
                   extra={"email_id": email_id,
                          "client_name": extracted_entities.client_name,
                          "creditor_name": extracted_entities.creditor_name})
        email.processing_status = "matching"
        db.commit()

        # Extract required data (from merged extracted_data - Phase 3 + entity extraction)
        final_extracted = email.extracted_data
        client_name = final_extracted.get("client_name")
        creditor_name = final_extracted.get("creditor_name")
        creditor_email = email.from_email
        new_debt_amount = final_extracted.get("debt_amount")
        reference_numbers = final_extracted.get("reference_numbers", [])

        # Validate required fields for matching
        # Allow matching if we have client_name OR reference_numbers (Aktenzeichen)
        has_matching_signal = bool(client_name) or bool(reference_numbers)
        has_creditor = bool(creditor_name or creditor_email)
        if not has_matching_signal or not has_creditor:
            logger.warning("missing_required_fields_for_matching",
                          extra={"email_id": email_id,
                                 "has_client": bool(client_name),
                                 "has_reference": bool(reference_numbers),
                                 "has_creditor": has_creditor})
            email.processing_status = "completed"
            email.match_status = "no_match"
            email.completed_at = datetime.utcnow()
            email.processed_at = datetime.utcnow()
            db.commit()
            return

        # Create MatchingEngineV2 instance
        engine = MatchingEngineV2(
            db=db,
            lookback_days=settings.match_lookback_days,
            kanzlei_id=email.kanzlei_id
        )

        # Build matching input from extracted data
        matching_input = {
            "client_name": client_name,
            "creditor_name": creditor_name,
            "reference_numbers": reference_numbers,
        }

        # Call engine.find_match()
        matching_result = engine.find_match(
            email_id=email_id,
            extracted_data=matching_input,
            from_email=creditor_email,
            received_at=email.received_at or datetime.utcnow(),
            creditor_category="default"
        )

        # Save match results with explainability JSONB
        engine.save_match_results(email_id, matching_result)

        logger.info("matching_completed",
                   extra={"email_id": email_id,
                          "status": matching_result.status,
                          "candidates_count": len(matching_result.candidates)})

        # Add breadcrumb for matching
        add_breadcrumb(
            "pipeline",
            f"Match status: {matching_result.status}",
            data={"score": matching_result.match.total_score if matching_result.match else 0}
        )

        # ========================================
        # PHASE 7: Confidence Scoring & Routing
        # ========================================

        # Collect document types from extraction
        extraction_checkpoint = (email.agent_checkpoints or {}).get("agent_2_extraction", {})
        document_types = extraction_checkpoint.get("source_types", ["email_body"])

        # Calculate overall confidence with dimension breakdown
        from app.services.confidence import calculate_overall_confidence, route_by_confidence, RoutingAction, get_review_expiration_days

        confidence_result = calculate_overall_confidence(
            agent_checkpoints=email.agent_checkpoints,
            document_types=document_types,
            match_result={
                "status": matching_result.status,
                "total_score": matching_result.match.total_score if matching_result.match else 0,
                "candidates": len(matching_result.candidates)
            } if matching_result else None,
            final_extracted_data=email.extracted_data  # Use merged data for completeness check
        )

        # Store confidence breakdown
        email.extraction_confidence = int(confidence_result.extraction * 100)
        email.overall_confidence = int(confidence_result.overall * 100)
        # Note: match_confidence already set from matching_result

        # Determine routing
        route = route_by_confidence(confidence_result.overall)
        email.confidence_route = route.level.value

        logger.info(
            "confidence_routing_determined",
            extra={"email_id": email_id,
                   "overall": confidence_result.overall,
                   "extraction": confidence_result.extraction,
                   "match": confidence_result.match,
                   "weakest_link": confidence_result.weakest_link,
                   "route": route.action.value}
        )

        # Record confidence metric
        confidence_bucket = "high" if confidence_result.overall >= 0.85 else "medium" if confidence_result.overall >= 0.6 else "low"
        metrics.record_confidence(confidence_bucket, confidence_result.overall, email_id=email_id)

        # Handle matching outcomes
        if matching_result.status == "auto_matched":
            # AUTO-MATCHED: Apply confidence routing
            logger.info("auto_matched",
                       extra={"email_id": email_id,
                              "inquiry_id": matching_result.match.inquiry.id,
                              "score": matching_result.match.total_score,
                              "confidence_route": route.action.value})

            # Set matched_inquiry_id from the matched candidate
            matched_inquiry = matching_result.match.inquiry
            email.matched_inquiry_id = matched_inquiry.id

            # Use matched inquiry's client_name as fallback if extraction returned None
            # (creditor replies rarely mention the client name)
            if not client_name and matched_inquiry.client_name:
                client_name = matched_inquiry.client_name
                logger.info("client_name_from_inquiry",
                            extra={"email_id": email_id,
                                   "client_name": client_name,
                                   "inquiry_id": matched_inquiry.id})

            # Extract aktenzeichen from reference numbers
            # Accepts formats like: "476982_64928", "542900", "AZ-123456", "2007/255"
            import re
            client_aktenzeichen = None
            if reference_numbers:
                for ref in reference_numbers:
                    # Match references with digits (optionally separated by underscores, hyphens, or slashes)
                    if re.match(r'^[\d_\-/]+$', ref) and len(ref) >= 4:
                        client_aktenzeichen = ref
                        break

            # Use creditor email as fallback if name not extracted
            creditor_name_or_email = creditor_name or creditor_email

            # --- 2. Schreiben Branch ---
            # If this inquiry is for a Schuldenbereinigungsplan, use settlement extraction
            # instead of the normal amount extraction path.
            letter_type = getattr(matched_inquiry, 'letter_type', 'first') or 'first'

            # If matched inquiry is 'first' but a 'second' inquiry exists among
            # candidates, prefer it — BUT only when intent suggests a settlement
            # response (payment_plan). debt_statement emails are 1. Schreiben
            # responses and must stay on the first-round path.
            intent = intent_result.get("intent")
            intent_suggests_second = intent in ("payment_plan", "settlement", "counter_offer")
            if letter_type == 'first' and matching_result.candidates and intent_suggests_second:
                for candidate in matching_result.candidates:
                    cand_lt = getattr(candidate.inquiry, 'letter_type', 'first') or 'first'
                    if cand_lt == 'second':
                        logger.info("letter_type_override_to_second",
                                    extra={"email_id": email_id,
                                           "original_inquiry_id": matched_inquiry.id,
                                           "second_inquiry_id": candidate.inquiry.id})
                        matched_inquiry = candidate.inquiry
                        email.matched_inquiry_id = matched_inquiry.id
                        letter_type = 'second'
                        # Update client info from the second inquiry if available
                        if not client_aktenzeichen and matched_inquiry.reference_number:
                            client_aktenzeichen = matched_inquiry.reference_number
                        if not client_name and matched_inquiry.client_name:
                            client_name = matched_inquiry.client_name
                        break

            if letter_type == 'second':
                _process_second_round(
                    db=db,
                    email=email,
                    email_id=email_id,
                    matched_inquiry=matched_inquiry,
                    matching_result=matching_result,
                    client_name=client_name,
                    client_aktenzeichen=client_aktenzeichen,
                    creditor_email=creditor_email,
                    creditor_name=creditor_name_or_email,
                    email_body=email_body_for_extraction,
                    subject=email.subject,
                    confidence_result=confidence_result,
                    route=route,
                )
                # Skip the normal 1. Schreiben path below — jump to Step 7
                # (processing completion is handled inside _process_second_round sets status,
                # final commit + report happens after this if/else block)
            else:
                # --- 1. Schreiben: Intent-Based Amount Gating ---
                # Only update debt amounts for debt_statement and payment_plan intents.
                from app.services.amount_update_guard import should_update_amount

                intent = intent_result.get("intent")
                existing_amount = None
                if intent not in ("debt_statement", "payment_plan"):
                    guard_ok = False
                    guard_reason = f"intent_not_debt_statement:{intent}"
                    logger.info("amount_update_blocked_by_intent",
                                extra={"email_id": email_id, "intent": intent,
                                       "extracted_amount": new_debt_amount})
                else:
                    # --- Amount Update Guard ---
                    existing_amount = consolidation_result.get("existing_current_debt_amount")
                    if existing_amount is None and matched_inquiry:
                        existing_amount = getattr(matched_inquiry, 'debt_amount', None)
                        if existing_amount is not None:
                            logger.info("existing_amount_fallback_from_inquiry",
                                        extra={"email_id": email_id,
                                               "fallback_amount": existing_amount,
                                               "inquiry_id": matched_inquiry.id})

                    guard_ok, guard_reason = should_update_amount(
                        existing_amount=existing_amount,
                        new_amount=new_debt_amount,
                        confidence=confidence_result.overall,
                    )

                update_decision = "UPDATED" if guard_ok else "SKIPPED"

                logger.info("email_processed",
                           extra={
                               "event": "email_processed",
                               "email_id": email_id,
                               "matched_creditor_id": matched_inquiry.id,
                               "match_confidence": matching_result.match.total_score,
                               "existing_amount": existing_amount,
                               "extracted_amount": new_debt_amount,
                               "overall_confidence": confidence_result.overall,
                               "update_decision": update_decision,
                               "skip_reason": guard_reason if not guard_ok else None,
                               "confidence_route": route.level.value,
                           })

                if not guard_ok:
                    email.match_status = "auto_matched"
                    email.match_confidence = int(matching_result.match.total_score * 100)

                    # No amount extracted = nothing to review, just log the response
                    needs_review = new_debt_amount is not None
                    logger.info("amount_update_skipped_by_guard",
                               extra={"email_id": email_id,
                                      "reason": guard_reason,
                                      "existing_amount": existing_amount,
                                      "new_amount": new_debt_amount,
                                      "needs_review": needs_review})

                    # Notify portal — needs_review only if there's actually an amount to review
                    from app.services.portal_notifier import notify_creditor_response
                    notify_creditor_response(
                        email_id=email_id,
                        client_aktenzeichen=client_aktenzeichen,
                        client_name=client_name,
                        creditor_name=creditor_name_or_email,
                        creditor_email=creditor_email,
                        new_debt_amount=new_debt_amount,
                        amount_source="creditor_response",
                        extraction_confidence=confidence_result.overall if confidence_result else None,
                        match_status="auto_matched",
                        confidence_route=route.level.value if route else "unknown",
                        needs_review=needs_review,
                        reference_numbers=reference_numbers,
                        email_subject=email.subject,
                        email_body_preview=email.raw_body_text,
                        attachment_urls=email.attachment_urls,
                        resend_email_id=email.zendesk_webhook_id,
                    )
                else:
                    # Guard approved — proceed with dual write
                    idempotency_key = generate_idempotency_key(
                        operation="creditor_debt_update",
                        aggregate_id=str(email_id),
                        payload={
                            "client_name": client_name,
                            "creditor_email": creditor_email,
                            "amount": new_debt_amount
                        }
                    )

                    idempotency_svc = IdempotencyService(SessionLocal)
                    dual_writer = DualDatabaseWriter(db, idempotency_svc)

                    result = dual_writer.update_creditor_debt(
                        email_id=email_id,
                        client_name=client_name,
                        client_aktenzeichen=client_aktenzeichen,
                        creditor_email=creditor_email,
                        creditor_name=creditor_name_or_email,
                        new_debt_amount=new_debt_amount,
                        response_text=final_extracted.get("summary"),
                        reference_numbers=reference_numbers,
                        idempotency_key=idempotency_key,
                        extraction_confidence=confidence_result.overall if confidence_result else None
                    )

                    db.commit()

                    mongodb_success = False
                    if result.get("outbox_message_id"):
                        mongodb_success = dual_writer.execute_mongodb_write(result["outbox_message_id"])

                    if mongodb_success:
                        email.match_status = "auto_matched"
                        email.match_confidence = int(matching_result.match.total_score * 100)
                        logger.info("mongodb_update_success",
                                   extra={"email_id": email_id,
                                          "client_name": client_name,
                                          "creditor_name": creditor_name_or_email,
                                          "amount": new_debt_amount})

                        from app.services.portal_notifier import notify_creditor_response
                        notify_creditor_response(
                            email_id=email_id,
                            client_aktenzeichen=client_aktenzeichen,
                            client_name=client_name,
                            creditor_name=creditor_name_or_email,
                            creditor_email=creditor_email,
                            new_debt_amount=new_debt_amount,
                            amount_source="creditor_response",
                            extraction_confidence=confidence_result.overall if confidence_result else None,
                            match_status="auto_matched",
                            confidence_route=route.level.value if route else "unknown",
                            needs_review=False,
                            reference_numbers=reference_numbers,
                            email_subject=email.subject,
                            email_body_preview=email.raw_body_text,
                            attachment_urls=email.attachment_urls,
                            resend_email_id=email.zendesk_webhook_id,
                        )

                        if route.action == RoutingAction.AUTO_UPDATE:
                            logger.info("high_confidence_auto_update",
                                       extra={"email_id": email_id,
                                              "confidence": confidence_result.overall})

                        elif route.action == RoutingAction.UPDATE_AND_NOTIFY:
                            logger.info("medium_confidence_update_and_notify",
                                       extra={"email_id": email_id,
                                              "confidence": confidence_result.overall})

                            email_notifier.send_debt_update_notification(
                                client_name=client_name,
                                creditor_name=creditor_name_or_email,
                                creditor_email=creditor_email,
                                old_debt_amount=existing_amount,
                                new_debt_amount=new_debt_amount,
                                side_conversation_id="N/A",
                                zendesk_ticket_id=email.zendesk_ticket_id,
                                reference_numbers=reference_numbers,
                                confidence_score=matching_result.match.total_score
                            )

                            add_breadcrumb("notification", "Auto-match notification sent")

                        elif route.action == RoutingAction.MANUAL_REVIEW:
                            logger.info("low_confidence_manual_review_override",
                                       extra={"email_id": email_id,
                                              "confidence": confidence_result.overall})

                            from app.services.validation import enqueue_for_review
                            expiration_days = get_review_expiration_days(route.level)

                            enqueue_for_review(
                                db,
                                email_id,
                                reason="low_confidence",
                                details={
                                    "overall_confidence": confidence_result.overall,
                                    "extraction_confidence": confidence_result.extraction,
                                    "match_confidence": confidence_result.match,
                                    "weakest_link": confidence_result.weakest_link,
                                    "expiration_days": expiration_days,
                                    "match_status": "auto_matched_but_low_confidence"
                                }
                            )
                            email.match_status = "needs_review"

                    else:
                        email.match_status = "no_match"
                        logger.warning("mongodb_update_failed",
                                      extra={"email_id": email_id,
                                             "client_name": client_name,
                                             "creditor_name": creditor_name_or_email})

        else:
            # AMBIGUOUS / BELOW_THRESHOLD / NO_RECENT_INQUIRY: Enqueue to ManualReviewQueue
            logger.info("non_auto_matched_enqueuing_for_review",
                       extra={"email_id": email_id,
                              "status": matching_result.status,
                              "confidence_route": route.action.value})

            review_id = enqueue_ambiguous_match(db, email_id, matching_result)

            if review_id:
                email.match_status = "needs_review"
                email.match_confidence = int(matching_result.candidates[0].total_score * 100) if matching_result.candidates else 0
                logger.info("enqueued_for_manual_review",
                           extra={"email_id": email_id,
                                  "review_id": review_id,
                                  "status": matching_result.status})
            else:
                # Duplicate skipped
                email.match_status = "needs_review"
                logger.info("review_queue_duplicate",
                           extra={"email_id": email_id})

            # Notify portal for unmatched emails so they appear in admin inbox
            from app.services.portal_notifier import notify_creditor_response
            best_candidate_name = None
            if matching_result.candidates:
                best_candidate_name = matching_result.candidates[0].client_name
            notify_creditor_response(
                email_id=email_id,
                client_aktenzeichen=client_aktenzeichen,
                client_name=best_candidate_name or client_name,
                creditor_name=creditor_name_or_email,
                creditor_email=creditor_email,
                new_debt_amount=new_debt_amount,
                amount_source="creditor_response",
                extraction_confidence=confidence_result.overall if confidence_result else None,
                match_status=matching_result.status,
                confidence_route=route.level.value if route else "unknown",
                needs_review=True,
                reference_numbers=reference_numbers,
                email_subject=email.subject,
                email_body_preview=email.raw_body_text,
                attachment_urls=email.attachment_urls,
                resend_email_id=email.zendesk_webhook_id,
            )

        # Step 7: Mark as completed
        email.processing_status = "completed"
        email.completed_at = datetime.utcnow()
        email.processed_at = datetime.utcnow()

        # Calculate processing duration
        duration_ms = int((time.time() - start_time) * 1000)

        # Create processing report (REQ-OPS-06)
        try:
            create_processing_report(
                db=db,
                email_id=email_id,
                extracted_data=email.extracted_data,
                agent_checkpoints=email.agent_checkpoints or {},
                overall_confidence=confidence_result.overall if confidence_result else 0.5,
                confidence_route=email.confidence_route or "unknown",
                needs_review=bool(email.match_status == "needs_review"),
                review_reason=consolidation_result.get("review_reason") if consolidation_result else None,
                processing_time_ms=duration_ms
            )
        except Exception as report_error:
            logger.warning("processing_report_failed", extra={"error": str(report_error), "email_id": email_id})
            # Don't fail processing for report generation errors

        db.commit()

        logger.info("email_processing_completed",
                   extra={"email_id": email_id,
                          "match_status": email.match_status,
                          "duration_ms": duration_ms})

    except Exception as e:
        # Load email fresh and mark as failed
        logger.error("email_processing_error",
                    extra={"email_id": email_id,
                           "error": str(e),
                           "exception_type": type(e).__name__},
                    exc_info=True)

        # Record error metric
        try:
            metrics.record_error("process_email", type(e).__name__, email_id=email_id)
        except Exception:
            pass  # Don't fail on metrics error

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
                        extra={"email_id": email_id,
                               "error": str(commit_error)})

        # Re-raise to trigger Dramatiq retry logic
        # The on_failure callback will be invoked only after all retries exhausted
        raise

    finally:
        # Record processing time metric
        try:
            duration_ms = int((time.time() - start_time) * 1000)
            metrics.record_processing_time("process_email", "complete", duration_ms, email_id=email_id)
        except Exception:
            pass  # Don't fail on metrics error

        # Close database connection
        db.close()

        # Explicit garbage collection for memory stability (512MB constraint)
        gc.collect()

        # Log memory after gc
        memory_after_mb = process.memory_info().rss / 1024 / 1024
        logger.info("process_email_complete",
                   extra={"email_id": email_id,
                          "memory_before_mb": round(memory_before_mb, 2),
                          "memory_after_mb": round(memory_after_mb, 2),
                          "memory_freed_mb": round(memory_before_mb - memory_after_mb, 2)})


def _process_second_round(
    db,
    email,
    email_id: int,
    matched_inquiry,
    matching_result,
    client_name: Optional[str],
    client_aktenzeichen: Optional[str],
    creditor_email: str,
    creditor_name: str,
    email_body: str,
    subject: Optional[str],
    confidence_result,
    route,
):
    """
    Process a 2. Schreiben (Schuldenbereinigungsplan) response.

    Classifies the creditor's response as accepted/declined/counter_offer,
    writes settlement data to MongoDB, and notifies the portal.
    """
    from app.services.settlement_extractor import settlement_extractor
    from app.services.mongodb_client import mongodb_service
    from app.services.portal_notifier import notify_settlement_response

    logger.info("second_round_processing_start",
               extra={"email_id": email_id,
                      "inquiry_id": matched_inquiry.id,
                      "creditor": creditor_name})

    # Step 1: Settlement extraction (Claude Haiku)
    settlement_result = settlement_extractor.extract(
        email_body=email_body,
        from_email=creditor_email,
        subject=subject,
    )

    # Step 2: Confidence check
    needs_review = (
        settlement_result.confidence < 0.70
        or settlement_result.settlement_decision == "no_clear_response"
    )

    logger.info("settlement_extraction_complete",
               extra={"email_id": email_id,
                      "decision": settlement_result.settlement_decision,
                      "confidence": settlement_result.confidence,
                      "counter_offer": settlement_result.counter_offer_amount,
                      "needs_review": needs_review})

    # Step 3: Store in agent_checkpoints (existing JSONB column)
    checkpoints = email.agent_checkpoints or {}
    checkpoints["settlement_extraction"] = {
        "settlement_decision": settlement_result.settlement_decision,
        "counter_offer_amount": settlement_result.counter_offer_amount,
        "conditions": settlement_result.conditions,
        "reference_to_proposal": settlement_result.reference_to_proposal,
        "confidence": settlement_result.confidence,
        "summary": settlement_result.summary,
        "needs_review": needs_review,
    }
    email.agent_checkpoints = checkpoints

    # Step 4: MongoDB write
    mongodb_success = mongodb_service.update_settlement_response(
        client_name=client_name,
        client_aktenzeichen=client_aktenzeichen,
        creditor_email=creditor_email,
        creditor_name=creditor_name,
        settlement_decision=settlement_result.settlement_decision,
        response_summary=settlement_result.summary,
        counter_offer_amount=settlement_result.counter_offer_amount,
        conditions=settlement_result.conditions,
        extraction_confidence=settlement_result.confidence,
    )

    # Step 5: Set match status
    if mongodb_success:
        email.match_status = "auto_matched"
        email.match_confidence = int(matching_result.match.total_score * 100)
    else:
        email.match_status = "no_match"
        logger.warning("settlement_mongodb_write_failed",
                      extra={"email_id": email_id})

    if needs_review:
        email.match_status = "needs_review"

    # Step 6: Portal notification
    notify_settlement_response(
        email_id=email_id,
        client_aktenzeichen=client_aktenzeichen,
        client_name=client_name,
        creditor_name=creditor_name,
        creditor_email=creditor_email,
        settlement_decision=settlement_result.settlement_decision,
        counter_offer_amount=settlement_result.counter_offer_amount,
        conditions=settlement_result.conditions,
        confidence=settlement_result.confidence,
        match_status=email.match_status,
        needs_review=needs_review,
        email_subject=subject,
        email_body_preview=email_body,
        attachment_urls=email.attachment_urls,
        resend_email_id=email.zendesk_webhook_id,
    )

    logger.info("second_round_processing_complete",
               extra={"email_id": email_id,
                      "decision": settlement_result.settlement_decision,
                      "match_status": email.match_status})
