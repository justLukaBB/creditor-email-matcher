"""
Intent Classifier Actor (Agent 1)
Dramatiq actor for email intent classification with checkpoint saving
"""

import dramatiq
import structlog
from typing import Dict

from app.models.intent_classification import EmailIntent

logger = structlog.get_logger(__name__)


@dramatiq.actor(
    max_retries=3,
    min_backoff=5000,  # 5 seconds
    max_backoff=60000,  # 1 minute
    queue_name="intent_classification"
)
def classify_intent(email_id: int) -> Dict:
    """
    Agent 1: Classify email intent using rule-based + LLM fallback.

    This actor:
    1. Loads email from database
    2. Extracts headers from raw email (if available)
    3. Classifies intent using cheap rules first, LLM fallback
    4. Checks confidence threshold (0.7)
    5. Saves checkpoint to agent_checkpoints JSONB column
    6. Returns structured dict for pipeline chaining

    Checkpoint structure:
    {
        "intent": "debt_statement",
        "confidence": 0.85,
        "method": "claude_haiku",
        "skip_extraction": false,
        "needs_review": false,
        "timestamp": "2026-02-05T10:30:00Z",
        "validation_status": "passed"
    }

    Args:
        email_id: IncomingEmail ID to classify

    Returns:
        Dict with:
            - email_id: int
            - intent: str (EmailIntent value)
            - confidence: float
            - skip_extraction: bool
            - needs_review: bool
            - method: str (classification method used)

    Example:
        >>> classify_intent.send(123)
        >>> # Or call directly in tests:
        >>> result = classify_intent(123)
        >>> print(result["intent"], result["skip_extraction"])
        debt_statement False
    """
    # Lazy imports to avoid circular dependencies
    from app.database import SessionLocal
    from app.models.incoming_email import IncomingEmail
    from app.services.intent_classifier import classify_email_intent
    from app.services.validation import save_checkpoint, has_valid_checkpoint

    logger.info("classify_intent_started", email_id=email_id)

    db = SessionLocal()
    try:
        # Load email
        email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()

        if not email:
            logger.error("email_not_found", email_id=email_id)
            raise ValueError(f"IncomingEmail with id={email_id} not found")

        # Skip if already classified (idempotent execution)
        if has_valid_checkpoint(db, email_id, "agent_1_intent"):
            logger.info("intent_already_classified", email_id=email_id)
            checkpoint = email.agent_checkpoints.get("agent_1_intent", {})
            return {
                "email_id": email_id,
                "intent": checkpoint.get("intent"),
                "confidence": checkpoint.get("confidence"),
                "skip_extraction": checkpoint.get("skip_extraction"),
                "needs_review": checkpoint.get("needs_review"),
                "method": checkpoint.get("method")
            }

        # Extract headers from raw email if available
        # For now, use empty dict (headers not captured yet in IncomingEmail model)
        # TODO: Add headers column to IncomingEmail or parse from raw_body_text
        headers = {}

        # Parse headers from raw_body_text if it contains email headers
        # Email format: headers followed by blank line, then body
        if email.raw_body_text:
            lines = email.raw_body_text.split('\n')
            parsed_headers = {}
            for i, line in enumerate(lines):
                if not line.strip():
                    # Blank line marks end of headers
                    break
                if ':' in line and not line.startswith(' ') and not line.startswith('\t'):
                    key, value = line.split(':', 1)
                    parsed_headers[key.strip()] = value.strip()

            # Only use parsed headers if we found standard email headers
            if any(h in parsed_headers for h in ['From', 'To', 'Subject', 'Date']):
                headers = parsed_headers
                logger.info("headers_parsed_from_raw_body", email_id=email_id, header_count=len(headers))

        # Get subject and body
        subject = email.subject or ""
        # Use cleaned_body if available, otherwise raw
        body = email.cleaned_body or email.raw_body_text or email.raw_body_html or ""

        # Classify intent
        result = classify_email_intent(
            email_id=email_id,
            headers=headers,
            subject=subject,
            body=body
        )

        # Check confidence threshold (0.7)
        # Below threshold = needs manual review
        confidence_threshold = 0.7
        needs_review = result.confidence < confidence_threshold

        if needs_review:
            logger.warning("low_confidence_classification",
                          email_id=email_id,
                          intent=result.intent.value,
                          confidence=result.confidence,
                          threshold=confidence_threshold)

        # Save checkpoint
        checkpoint_data = {
            "intent": result.intent.value,
            "confidence": result.confidence,
            "method": result.method,
            "skip_extraction": result.skip_extraction,
            "needs_review": needs_review
        }

        save_checkpoint(db, email_id, "agent_1_intent", checkpoint_data)

        logger.info("intent_classified",
                   email_id=email_id,
                   intent=result.intent.value,
                   confidence=result.confidence,
                   method=result.method,
                   skip_extraction=result.skip_extraction,
                   needs_review=needs_review)

        # Return structured dict for pipeline chaining
        return {
            "email_id": email_id,
            "intent": result.intent.value,
            "confidence": result.confidence,
            "skip_extraction": result.skip_extraction,
            "needs_review": needs_review,
            "method": result.method
        }

    except Exception as e:
        logger.error("intent_classification_error",
                    email_id=email_id,
                    error=str(e),
                    exc_info=True)
        raise

    finally:
        db.close()
