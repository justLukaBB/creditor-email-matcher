"""
Entity Extractor Service with Claude (Anthropic)
Uses Claude 3.5 Sonnet to extract structured data from creditor emails
"""

import json
from typing import Dict, Optional, List
from anthropic import Anthropic
from pydantic import BaseModel, Field
from app.config import settings
import logging

logger = logging.getLogger(__name__)


class ExtractedEntities(BaseModel):
    """
    Structured data extracted from creditor email
    """
    is_creditor_reply: bool = Field(
        description="Is this email actually a reply from a creditor? (vs spam, auto-reply, etc.)"
    )
    client_name: Optional[str] = Field(
        None,
        description="Full name of the client (Mandant), e.g. 'Mustermann, Max' or 'Max Mustermann'"
    )
    creditor_name: Optional[str] = Field(
        None,
        description="Name of the creditor/company, e.g. 'Sparkasse Bochum'"
    )
    debt_amount: Optional[float] = Field(
        None,
        description="Total debt amount in EUR, e.g. 1234.56"
    )
    reference_numbers: List[str] = Field(
        default_factory=list,
        description="Any reference numbers mentioned (Aktenzeichen, Kundennummer, etc.)"
    )
    confidence: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the extraction (0.0 to 1.0)"
    )
    summary: Optional[str] = Field(
        None,
        description="Brief summary of the email content (1-2 sentences)"
    )


class EntityExtractorClaude:
    """
    Extracts structured entities from email text using Claude 3.5 Sonnet
    """

    def __init__(self):
        if not settings.anthropic_api_key:
            logger.warning("Anthropic API key not configured - entity extraction will fail")
            self.client = None
        else:
            self.client = Anthropic(api_key=settings.anthropic_api_key)

    def extract_entities(
        self,
        email_body: str,
        from_email: str,
        subject: Optional[str] = None
    ) -> ExtractedEntities:
        """
        Extract structured entities from email content

        Args:
            email_body: Cleaned email body text
            from_email: Sender's email address
            subject: Email subject line

        Returns:
            ExtractedEntities object with extracted data
        """
        if not self.client:
            logger.error("Anthropic client not initialized - returning empty result")
            return ExtractedEntities(
                is_creditor_reply=False,
                confidence=0.0
            )

        try:
            # Build the prompt
            prompt = self._build_extraction_prompt(email_body, from_email, subject)

            # Call Claude API
            message = self.client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1024,
                temperature=0.1,  # Low temperature for consistent extraction
                system=self._get_system_prompt(),
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            # Parse the response
            result_text = message.content[0].text

            # Claude returns the JSON directly
            result_dict = json.loads(result_text)

            # Convert to ExtractedEntities
            entities = ExtractedEntities(**result_dict)

            logger.info(
                f"Entities extracted - is_creditor: {entities.is_creditor_reply}, "
                f"confidence: {entities.confidence:.2f}, "
                f"client: {entities.client_name}"
            )

            return entities

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            logger.error(f"Raw response: {result_text if 'result_text' in locals() else 'N/A'}")
            return ExtractedEntities(
                is_creditor_reply=False,
                confidence=0.0
            )
        except Exception as e:
            logger.error(f"Entity extraction failed: {e}", exc_info=True)
            return ExtractedEntities(
                is_creditor_reply=False,
                confidence=0.0
            )

    def _get_system_prompt(self) -> str:
        """
        System prompt defining Claude's role and output format
        """
        return """Du bist ein Experten-Assistent für eine deutsche Rechtsanwaltskanzlei, die sich auf Schuldnerberatung spezialisiert hat.

Deine Aufgabe ist es, eingehende E-Mails von Gläubigern zu analysieren und strukturierte Informationen zu extrahieren.

Die Kanzlei sendet Anfragen an Gläubiger im Namen ihrer Mandanten. Die Gläubiger antworten dann mit Informationen über Schulden.

Extrahiere die folgenden Informationen aus der E-Mail:

1. **is_creditor_reply**: Ist dies eine legitime Gläubiger-Antwort? (nicht Spam, nicht Auto-Reply, nicht Out-of-Office)
2. **client_name**: Der vollständige Name des Mandanten (die Person, die die Schulden hat). Suche nach Phrasen wie "Herr/Frau [Name]", "Mandant", "Schuldner"
3. **creditor_name**: Der Firmenname des Gläubigers (Bank, Versicherung, Telekom, Inkassobüro, Rechtsanwaltskanzlei, etc.)
   - **WICHTIG**: Der Gläubiger ist die Firma/Organisation, die die FORDERUNG hält
   - Oft steht der Gläubiger-Name in der **Signatur** der Email (z.B. "Mit freundlichen Grüßen, [Name], [Firma]")
   - Der Gläubiger-Name kann auch im Briefkopf, Footer oder in der Absender-Zeile stehen
   - Auch wenn die Email von einer persönlichen Adresse kommt (z.B. gmail.com), schaue in der Signatur nach der Firmenbezeichnung
4. **debt_amount**: Gesamtschulden in EUR. Suche nach "Forderung", "Betrag", "Schulden"
5. **reference_numbers**: Alle Referenznummern (Aktenzeichen, Kundennummer, Vertragsnummer, Rechnungsnummer)
6. **confidence**: Dein Vertrauen in die Extraktion (0.0 = sehr unsicher, 1.0 = sehr sicher)
7. **summary**: Kurze 1-2 Satz Zusammenfassung der E-Mail

**Wichtig**:
- Kundennamen können im Format "Nachname, Vorname" oder "Vorname Nachname" sein
- Normalisiere wenn möglich auf "Nachname, Vorname" Format
- **SUCHE IMMER in der Signatur nach dem Gläubiger-Namen** (z.B. "Mit freundlichen Grüßen, K. Capelle, awt Rechtsanwälte" → creditor_name = "awt Rechtsanwälte")
- Wenn du unsicher bist, setze confidence niedrig, aber gib trotzdem deine beste Schätzung ab
- Gib nur valides JSON zurück, das dem Schema entspricht

**Output Format** (NUR JSON, keine zusätzlichen Kommentare):
{
  "is_creditor_reply": true/false,
  "client_name": "Mustermann, Max" oder null,
  "creditor_name": "Sparkasse Bochum" oder null,
  "debt_amount": 1234.56 oder null,
  "reference_numbers": ["AZ-123", "KD-456"] oder [],
  "confidence": 0.85,
  "summary": "Kurze Zusammenfassung" oder null
}"""

    def _build_extraction_prompt(
        self,
        email_body: str,
        from_email: str,
        subject: Optional[str]
    ) -> str:
        """
        Build the user prompt with email content
        """
        prompt_parts = [
            "Bitte extrahiere Informationen aus dieser E-Mail:\n",
            f"**Von**: {from_email}",
        ]

        if subject:
            prompt_parts.append(f"**Betreff**: {subject}")

        prompt_parts.append(f"\n**E-Mail Inhalt**:\n{email_body}")
        prompt_parts.append("\n\nGib die Antwort als JSON zurück (nur JSON, keine zusätzlichen Erklärungen):")

        return "\n".join(prompt_parts)


# Global instance
entity_extractor_claude = EntityExtractorClaude()
