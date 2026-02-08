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

        extracted_entities = extractor.extract_entities(
            email_body=parsed["cleaned_body"],
            from_email=email.from_email,
            subject=email.subject
        )

        # Merge entity extraction results with Phase 3 extraction
        # Phase 3 provides: debt_amount (from attachments), client_name, creditor_name
        # Entity extraction provides: is_creditor_reply, reference_numbers, summary
        # Priority: Phase 3 debt_amount (processes attachments), entity extraction for intent
        current_extracted_data = email.extracted_data or {}

        # Override is_creditor_reply if pipeline extracted a debt amount AND intent is debt_statement
        # This handles cases where attachment contains the creditor data but email body is sparse
        pipeline_has_amount = current_extracted_data.get("debt_amount") is not None
        intent_is_debt = intent_result.get("intent") == "debt_statement"

        if pipeline_has_amount and intent_is_debt:
            is_creditor_reply = True
            logger.info("is_creditor_override",
                       extra={"email_id": email_id,
                              "reason": "pipeline_extracted_amount_with_debt_intent",
                              "amount": current_extracted_data.get("debt_amount")})
        else:
            is_creditor_reply = extracted_entities.is_creditor_reply

        email.extracted_data = {
            "is_creditor_reply": is_creditor_reply,
            "client_name": current_extracted_data.get("client_name") or extracted_entities.client_name,
            "creditor_name": current_extracted_data.get("creditor_name") or extracted_entities.creditor_name,
            "debt_amount": current_extracted_data.get("debt_amount") or extracted_entities.debt_amount,
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
        if not client_name or not (creditor_name or creditor_email):
            logger.warning("missing_required_fields_for_matching",
                          extra={"email_id": email_id,
                                 "has_client": bool(client_name),
                                 "has_creditor": bool(creditor_name or creditor_email)})
            email.processing_status = "completed"
            email.match_status = "no_match"
            email.completed_at = datetime.utcnow()
            email.processed_at = datetime.utcnow()
            db.commit()
            return

        # Create MatchingEngineV2 instance
        engine = MatchingEngineV2(
            db=db,
            lookback_days=settings.match_lookback_days
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

            # Extract aktenzeichen from reference numbers
            # Accepts formats like: "476982_64928", "542900", "AZ-123456"
            import re
            client_aktenzeichen = None
            if reference_numbers:
                for ref in reference_numbers:
                    # Match references with digits (optionally separated by underscores/hyphens)
                    if re.match(r'^[\d_\-]+$', ref) and len(ref) >= 4:
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
                response_text=final_extracted.get("summary"),
                reference_numbers=reference_numbers,
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
                email.match_confidence = int(matching_result.match.total_score * 100)
                logger.info("mongodb_update_success",
                           extra={"email_id": email_id,
                                  "client_name": client_name,
                                  "creditor_name": creditor_name_or_email,
                                  "amount": new_debt_amount})

                # Apply confidence-based notification routing
                if route.action == RoutingAction.AUTO_UPDATE:
                    # HIGH confidence: auto-update with log only, NO notification
                    logger.info("high_confidence_auto_update",
                               extra={"email_id": email_id,
                                      "confidence": confidence_result.overall})
                    # Do NOT send notification

                elif route.action == RoutingAction.UPDATE_AND_NOTIFY:
                    # MEDIUM confidence: write to database, then notify review team
                    logger.info("medium_confidence_update_and_notify",
                               extra={"email_id": email_id,
                                      "confidence": confidence_result.overall})

                    # Send email notification for verification (REQ-OPS-05)
                    email_notifier.send_debt_update_notification(
                        client_name=client_name,
                        creditor_name=creditor_name_or_email,
                        creditor_email=creditor_email,
                        old_debt_amount=None,
                        new_debt_amount=new_debt_amount,
                        side_conversation_id="N/A",
                        zendesk_ticket_id=email.zendesk_ticket_id,
                        reference_numbers=reference_numbers,
                        confidence_score=matching_result.match.total_score
                    )

                    # Add breadcrumb for notification
                    add_breadcrumb("notification", "Auto-match notification sent")

                elif route.action == RoutingAction.MANUAL_REVIEW:
                    # LOW confidence: route to manual review queue even if auto-matched
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
