"""
Settlement Extractor Service
Classifies creditor responses to Schuldenbereinigungsplan (2. Schreiben) using Claude Haiku.
"""

import json
import time
import logging
from decimal import Decimal
from typing import Optional, List, Tuple, Union

from anthropic import Anthropic
from app.config import settings
from app.models.intent_classification import SettlementExtractionResult, SettlementDecision
from app.services.monitoring.circuit_breakers import get_claude_breaker, CircuitBreakerError

logger = logging.getLogger(__name__)

SETTLEMENT_SYSTEM_PROMPT = """Du bist ein Experten-Assistent für eine deutsche Rechtsanwaltskanzlei (Insolvenzrecht/Schuldnerberatung).

Die Kanzlei hat Gläubigern einen Schuldenbereinigungsplan (2. Schreiben) zugestellt. Gläubiger antworten darauf mit Zustimmung, Ablehnung oder Gegenvorschlag.

Analysiere die Antwort und klassifiziere sie:

1. **settlement_decision**: Eines von:
   - "accepted" — Gläubiger stimmt dem Vergleichsvorschlag zu
   - "declined" — Gläubiger lehnt den Vorschlag ab
   - "counter_offer" — Gläubiger macht einen Gegenvorschlag (anderer Betrag, andere Raten)
   - "inquiry" — Gläubiger stellt Rückfragen, ohne zu entscheiden
   - "no_clear_response" — Antwort ist unklar oder nicht einzuordnen

2. **counter_offer_amount**: Nur bei "counter_offer" — der vorgeschlagene Gegenbetrag in EUR
3. **conditions**: Bedingungen oder Einschränkungen (z.B. "nur bei Einmalzahlung", "Ratenzahlung über 12 Monate")
4. **reference_to_proposal**: Art des Plans (z.B. "Ratenplan", "Nullplan", "Einmalzahlung")
5. **confidence**: Dein Vertrauen in die Klassifikation (0.0–1.0)
6. **summary**: 1-2 Sätze Zusammenfassung der Antwort

**Output Format** (NUR JSON, keine zusätzlichen Kommentare):
{
  "settlement_decision": "accepted",
  "counter_offer_amount": null,
  "conditions": null,
  "reference_to_proposal": "Ratenplan",
  "confidence": 0.90,
  "summary": "Gläubiger stimmt dem Schuldenbereinigungsplan zu."
}"""


class SettlementExtractor:
    """Classifies creditor responses to settlement proposals using Claude Haiku."""

    def __init__(self):
        if not settings.anthropic_api_key:
            logger.warning("Anthropic API key not configured - settlement extraction will fail")
            self.client = None
        else:
            self.client = Anthropic(api_key=settings.anthropic_api_key)

    def extract(
        self,
        email_body: str,
        from_email: str,
        subject: Optional[str] = None,
        attachment_texts: Optional[List[str]] = None,
    ) -> SettlementExtractionResult:
        if not self.client:
            logger.error("Anthropic client not initialized")
            return SettlementExtractionResult(
                settlement_decision=SettlementDecision.no_clear_response,
                confidence=0.0,
                summary="Anthropic client not available",
            )

        user_prompt = self._build_prompt(email_body, from_email, subject, attachment_texts)
        start_time = time.time()

        try:
            breaker = get_claude_breaker()
            try:
                message = breaker.call(
                    self.client.messages.create,
                    model="claude-haiku-4-5-20251001",
                    max_tokens=512,
                    temperature=0.1,
                    system=SETTLEMENT_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
            except CircuitBreakerError:
                logger.error("claude_circuit_open_settlement")
                raise

            duration_ms = int((time.time() - start_time) * 1000)

            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                first_nl = raw.find("\n")
                if first_nl != -1:
                    raw = raw[first_nl + 1:]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()

            result = SettlementExtractionResult(**json.loads(raw))

            logger.info(
                "settlement_extracted",
                extra={
                    "decision": result.settlement_decision,
                    "confidence": result.confidence,
                    "counter_offer": result.counter_offer_amount,
                    "duration_ms": duration_ms,
                },
            )
            return result

        except json.JSONDecodeError as e:
            logger.error("settlement_json_parse_error", extra={"error": str(e)})
            return SettlementExtractionResult(
                settlement_decision=SettlementDecision.no_clear_response,
                confidence=0.0,
                summary="Failed to parse LLM response",
            )
        except CircuitBreakerError:
            raise
        except Exception as e:
            logger.error("settlement_extraction_failed", extra={"error": str(e)}, exc_info=True)
            return SettlementExtractionResult(
                settlement_decision=SettlementDecision.no_clear_response,
                confidence=0.0,
                summary=f"Extraction error: {str(e)}",
            )

    def _build_prompt(
        self,
        email_body: str,
        from_email: str,
        subject: Optional[str],
        attachment_texts: Optional[List[str]],
    ) -> str:
        parts = [
            "Bitte analysiere diese Gläubiger-Antwort auf unseren Schuldenbereinigungsplan:\n",
            f"**Von**: {from_email}",
        ]
        if subject:
            parts.append(f"**Betreff**: {subject}")
        parts.append(f"\n**E-Mail Inhalt**:\n{email_body}")

        if attachment_texts:
            parts.append("\n\n**Anhänge (extrahierter Text)**:")
            for i, text in enumerate(attachment_texts, 1):
                truncated = text[:3000] if len(text) > 3000 else text
                parts.append(f"\n--- Anhang {i} ---\n{truncated}")

        parts.append("\n\nGib die Antwort als JSON zurück (nur JSON, keine zusätzlichen Erklärungen):")
        return "\n".join(parts)


settlement_extractor = SettlementExtractor()


# --- Consistency validation ---------------------------------------------------

# Conditional phrases that suggest a "soft accept" is actually a counter offer.
# Lower-cased and matched against `conditions` field as substrings.
_CONDITIONAL_PHRASES = (
    "nur wenn",
    "vorbehaltlich",
    "sofern",
    "rate",
    "raten",
    "einmalzahlung",
)


def validate_consistency(
    result: SettlementExtractionResult,
    original_debt: Optional[Union[float, Decimal]] = None,
) -> Tuple[bool, List[str]]:
    """
    Sanity-check the LLM output for cross-field inconsistencies that hint at
    incomplete extraction or LLM hallucination. Independent of the model's
    self-reported confidence.

    Returns (inconsistent, warnings). `inconsistent=True` should drive needs_review.
    """
    warnings: List[str] = []

    decision = result.settlement_decision
    # SettlementDecision uses use_enum_values=True so str compare works,
    # but coerce to be safe against future schema changes.
    decision_value = decision.value if hasattr(decision, "value") else decision

    if decision_value == SettlementDecision.counter_offer.value and result.counter_offer_amount is None:
        warnings.append("counter_offer_without_amount")

    if decision_value == SettlementDecision.accepted.value and result.conditions:
        cond_lower = result.conditions.lower()
        if any(phrase in cond_lower for phrase in _CONDITIONAL_PHRASES):
            warnings.append("accepted_with_conditional_phrasing")

    if result.counter_offer_amount is not None:
        if result.counter_offer_amount < 0:
            warnings.append("negative_counter_offer")
        if original_debt is not None:
            try:
                debt_float = float(original_debt)
                if debt_float > 0 and result.counter_offer_amount > debt_float * 2:
                    warnings.append("counter_offer_exceeds_2x_original")
            except (TypeError, ValueError):
                pass

    return (len(warnings) > 0, warnings)
