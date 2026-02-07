"""
Intent Classification Service
Classifies email intent using rule-based detection (cheap) with Claude Haiku fallback (LLM)
"""

import re
import structlog
import time
from typing import Optional, Dict

from app.models.intent_classification import IntentResult, EmailIntent
from app.services.prompt_manager import get_active_prompt
from app.services.prompt_renderer import PromptRenderer
from app.services.prompt_metrics_service import record_extraction_metrics

logger = structlog.get_logger(__name__)


def classify_intent_cheap(headers: Dict[str, str], subject: str, body: str) -> Optional[IntentResult]:
    """
    Classify email intent using cheap rule-based detection.

    Returns None if classification is ambiguous (requires LLM).

    Rule-based detection for:
    1. AUTO_REPLY: RFC 5322 headers (Auto-Submitted, X-Auto-Response-Suppress)
    2. AUTO_REPLY: Out-of-office subject patterns (German and English)
    3. SPAM: noreply@ email addresses

    Args:
        headers: Email headers dictionary (case-insensitive keys)
        subject: Email subject line
        body: Email body text

    Returns:
        IntentResult if confident classification, None if ambiguous

    Example:
        >>> headers = {"Auto-Submitted": "auto-replied"}
        >>> result = classify_intent_cheap(headers, "", "")
        >>> print(result.intent, result.skip_extraction)
        auto_reply True
    """
    # Normalize header keys to lowercase for case-insensitive lookup
    headers_lower = {k.lower(): v for k, v in headers.items()}

    # Rule 1: AUTO_REPLY detection via RFC 5322 headers
    # Auto-Submitted: auto-replied (standard header for automated responses)
    auto_submitted = headers_lower.get("auto-submitted", "")
    if auto_submitted and "auto-replied" in auto_submitted.lower():
        logger.info("intent_classified_cheap", intent="auto_reply", method="header_auto_submitted")
        return IntentResult(
            intent=EmailIntent.auto_reply,
            confidence=1.0,
            method="header_auto_submitted",
            skip_extraction=True
        )

    # X-Auto-Response-Suppress header (Microsoft Exchange auto-reply indicator)
    x_auto_response = headers_lower.get("x-auto-response-suppress", "")
    if x_auto_response:
        logger.info("intent_classified_cheap", intent="auto_reply", method="header_x_auto_response")
        return IntentResult(
            intent=EmailIntent.auto_reply,
            confidence=1.0,
            method="header_x_auto_response",
            skip_extraction=True
        )

    # Rule 2: AUTO_REPLY detection via subject line patterns
    # German patterns: "Abwesenheitsnotiz", "Automatische Antwort", "Nicht im Büro"
    # English patterns: "Out of Office", "Automatic Reply", "Auto-Reply"
    ooo_patterns = [
        r"(?i)abwesenheitsnotiz",
        r"(?i)automatische\s+antwort",
        r"(?i)nicht\s+im\s+büro",
        r"(?i)out\s+of\s+office",
        r"(?i)automatic\s+reply",
        r"(?i)auto-reply",
        r"(?i)ooo:",  # Common abbreviation
    ]

    for pattern in ooo_patterns:
        if re.search(pattern, subject):
            logger.info("intent_classified_cheap", intent="auto_reply", method="subject_ooo_pattern", pattern=pattern)
            return IntentResult(
                intent=EmailIntent.auto_reply,
                confidence=0.95,
                method="subject_ooo_pattern",
                skip_extraction=True
            )

    # Rule 3: SPAM detection via noreply@ address pattern
    # Check if "noreply" appears in any header (From, Reply-To, Sender)
    from_header = headers_lower.get("from", "")
    reply_to = headers_lower.get("reply-to", "")
    sender = headers_lower.get("sender", "")

    all_addresses = f"{from_header} {reply_to} {sender}".lower()
    if "noreply@" in all_addresses or "no-reply@" in all_addresses:
        logger.info("intent_classified_cheap", intent="spam", method="noreply_address")
        return IntentResult(
            intent=EmailIntent.spam,
            confidence=0.9,
            method="noreply_address",
            skip_extraction=True
        )

    # Ambiguous - requires LLM classification
    logger.info("intent_classification_ambiguous", reason="no_rule_match")
    return None


def classify_intent_with_llm(body: str, subject: str, email_id: int = None) -> IntentResult:
    """
    Classify email intent using Claude Haiku (cheapest LLM option).

    Truncates body to 500 chars for token efficiency.
    Returns structured JSON with intent and confidence.

    Args:
        body: Email body text
        subject: Email subject line
        email_id: Optional email ID for metrics tracking

    Returns:
        IntentResult with LLM classification

    Example:
        >>> result = classify_intent_with_llm("Sehr geehrte...", "Forderung")
        >>> print(result.intent, result.confidence)
        debt_statement 0.85
    """
    # Lazy import to avoid import-time Anthropic SDK dependency
    try:
        from anthropic import Anthropic
        from app.config import settings
    except ImportError:
        logger.error("anthropic_sdk_not_installed")
        # Fallback: assume debt_statement with low confidence
        return IntentResult(
            intent=EmailIntent.debt_statement,
            confidence=0.6,
            method="claude_haiku_fallback",
            skip_extraction=False
        )

    # Truncate body to 500 chars for token efficiency
    truncated_body = body[:500] if body else ""

    # Try database-backed prompt first
    prompt_template = None
    db = None
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        prompt_template = get_active_prompt(db, task_type='classification', name='email_intent')
    except Exception as e:
        logger.warning("database_prompt_load_failed", error=str(e))

    # Construct prompt for intent classification
    if prompt_template:
        # Use database-backed prompt
        renderer = PromptRenderer()
        prompt = renderer.render(
            prompt_template.user_prompt_template,
            variables={'subject': subject, 'truncated_body': truncated_body},
            template_name='classification.email_intent'
        )
        model_name = prompt_template.model_name or "claude-3-5-haiku-20241022"
        temperature = prompt_template.temperature if prompt_template.temperature is not None else 0.0
        max_tokens = prompt_template.max_tokens or 100
    else:
        # Fallback to hardcoded prompt (current behavior)
        model_name = "claude-3-5-haiku-20241022"
        temperature = 0.0
        max_tokens = 100
        prompt = f"""Klassifiziere die E-Mail-Intent in eine der folgenden Kategorien:

1. debt_statement - Gläubigerantwort mit Forderungsbetrag oder Schuldenstatus
2. payment_plan - Zahlungsplan-Vorschlag oder Bestätigung
3. rejection - Ablehnung oder Widerspruch der Forderung
4. inquiry - Frage die manuelle Antwort erfordert
5. auto_reply - Abwesenheitsnotiz oder automatische Antwort
6. spam - Marketing, unrelated content

E-Mail:
Betreff: {subject}
Text: {truncated_body}

Antworte nur mit JSON:
{{"intent": "debt_statement|payment_plan|rejection|inquiry|auto_reply|spam", "confidence": 0.0-1.0}}"""

    start_time = time.time()

    try:
        # Use Claude Haiku (cheapest model for classification)
        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}]
        )

        # Parse JSON response
        import json
        response_text = response.content[0].text.strip()

        # Extract JSON if wrapped in markdown code blocks
        if response_text.startswith("```"):
            response_text = re.search(r'```(?:json)?\s*(\{.*\})\s*```', response_text, re.DOTALL)
            if response_text:
                response_text = response_text.group(1)

        result = json.loads(response_text)
        intent_str = result.get("intent", "debt_statement")
        confidence = float(result.get("confidence", 0.7))

        # Validate intent string
        try:
            intent = EmailIntent(intent_str)
        except ValueError:
            logger.warning("invalid_intent_from_llm", intent=intent_str, defaulting_to="debt_statement")
            intent = EmailIntent.debt_statement
            confidence = 0.6

        # Skip extraction for auto_reply and spam
        skip_extraction = intent in [EmailIntent.auto_reply, EmailIntent.spam]

        logger.info("intent_classified_llm",
                   intent=intent.value,
                   confidence=confidence,
                   model=model_name)

        # Record metrics if prompt_template was used and email_id provided
        if prompt_template and db and email_id:
            try:
                execution_time_ms = int((time.time() - start_time) * 1000)
                record_extraction_metrics(
                    db=db,
                    prompt_template_id=prompt_template.id,
                    email_id=email_id,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model_name=model_name,
                    extraction_success=True,
                    confidence_score=confidence,
                    manual_review_required=False,
                    execution_time_ms=execution_time_ms
                )
                db.commit()
            except Exception as metrics_error:
                logger.warning("metrics_recording_failed", error=str(metrics_error))
                # Don't fail classification if metrics recording fails
                if db:
                    db.rollback()

        return IntentResult(
            intent=intent,
            confidence=confidence,
            method="claude_haiku",
            skip_extraction=skip_extraction
        )

    except Exception as e:
        logger.error("llm_classification_error", error=str(e), exc_info=True)
        # Fallback: assume debt_statement with low confidence
        return IntentResult(
            intent=EmailIntent.debt_statement,
            confidence=0.6,
            method="claude_haiku_error_fallback",
            skip_extraction=False
        )
    finally:
        # Ensure db session is closed
        if db:
            db.close()


def classify_email_intent(email_id: int, headers: Dict[str, str], subject: str, body: str) -> IntentResult:
    """
    Classify email intent using rule-based detection first, falling back to LLM.

    This is the main entry point for intent classification.

    Strategy:
    1. Try cheap rule-based classification (headers, subject patterns, noreply@)
    2. If ambiguous, fall back to Claude Haiku LLM
    3. Log classification method and result

    Args:
        email_id: IncomingEmail ID (for logging)
        headers: Email headers dictionary
        subject: Email subject line
        body: Email body text

    Returns:
        IntentResult with intent, confidence, method, skip_extraction

    Example:
        >>> result = classify_email_intent(123, {}, "Forderung", "Sehr geehrte...")
        >>> print(result.intent, result.method)
        debt_statement claude_haiku
    """
    logger.info("intent_classification_started", email_id=email_id)

    # Try cheap classification first
    cheap_result = classify_intent_cheap(headers, subject, body)

    if cheap_result is not None:
        # Handle both enum and string (due to use_enum_values=True in Pydantic config)
        cheap_intent = cheap_result.intent.value if hasattr(cheap_result.intent, 'value') else cheap_result.intent
        logger.info("intent_classification_complete",
                   email_id=email_id,
                   intent=cheap_intent,
                   confidence=cheap_result.confidence,
                   method=cheap_result.method,
                   cost="$0.00")
        return cheap_result

    # Ambiguous - use LLM
    llm_result = classify_intent_with_llm(body, subject, email_id=email_id)

    # Handle both enum and string (due to use_enum_values=True in Pydantic config)
    intent_value = llm_result.intent.value if hasattr(llm_result.intent, 'value') else llm_result.intent
    logger.info("intent_classification_complete",
               email_id=email_id,
               intent=intent_value,
               confidence=llm_result.confidence,
               method=llm_result.method,
               cost="~$0.001")

    return llm_result
