"""
Entity Extractor Service
Uses OpenAI GPT-4o to extract structured data from creditor emails
"""

import json
from typing import Dict, Optional, List
from openai import OpenAI
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


class EntityExtractor:
    """
    Extracts structured entities from email text using OpenAI GPT-4o
    """

    def __init__(self):
        if not settings.openai_api_key:
            logger.warning("OpenAI API key not configured - entity extraction will fail")
            self.client = None
        else:
            self.client = OpenAI(api_key=settings.openai_api_key)

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
            logger.error("OpenAI client not initialized - returning empty result")
            return ExtractedEntities(
                is_creditor_reply=False,
                confidence=0.0
            )

        try:
            # Build the prompt
            prompt = self._build_extraction_prompt(email_body, from_email, subject)

            # Call OpenAI with structured output
            response = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": self._get_system_prompt()
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Low temperature for consistent extraction
            )

            # Parse the response
            result_text = response.choices[0].message.content
            result_dict = json.loads(result_text)

            # Convert to ExtractedEntities
            entities = ExtractedEntities(**result_dict)

            logger.info(
                f"Entities extracted - is_creditor: {entities.is_creditor_reply}, "
                f"confidence: {entities.confidence:.2f}, "
                f"client: {entities.client_name}"
            )

            return entities

        except Exception as e:
            logger.error(f"Entity extraction failed: {e}", exc_info=True)
            return ExtractedEntities(
                is_creditor_reply=False,
                confidence=0.0
            )

    def _get_system_prompt(self) -> str:
        """
        System prompt defining the AI's role and output format
        """
        return """You are an expert assistant for a German law firm (Rechtsanwaltskanzlei) specializing in debt counseling (Schuldnerberatung).

Your task is to analyze incoming emails from creditors (Gläubiger) and extract structured information.

The law firm sends inquiries to creditors on behalf of their clients (Mandanten). Creditors then reply with information about debts.

Extract the following information from the email:

1. **is_creditor_reply**: Is this a legitimate creditor response? (not spam, not auto-reply, not out-of-office)
2. **client_name**: The client's full name (the person who owes the debt). Look for phrases like "Herr/Frau [Name]", "Mandant", "Schuldner"
3. **creditor_name**: The creditor's company name (bank, insurance, telecom, etc.)
4. **debt_amount**: Total amount owed in EUR. Look for "Forderung", "Betrag", "Schulden"
5. **reference_numbers**: Any reference numbers (Aktenzeichen, Kundennummer, Vertragsnummer, Rechnungsnummer)
6. **confidence**: Your confidence in the extraction (0.0 = very uncertain, 1.0 = very certain)
7. **summary**: Brief 1-2 sentence summary of the email

**Important**:
- Client names can be in format "Lastname, Firstname" or "Firstname Lastname"
- Normalize to "Lastname, Firstname" format if possible
- If you're not sure, set confidence low but still make your best guess
- Return valid JSON matching the schema

**Output Format** (JSON):
{
  "is_creditor_reply": true/false,
  "client_name": "Mustermann, Max" or null,
  "creditor_name": "Sparkasse Bochum" or null,
  "debt_amount": 1234.56 or null,
  "reference_numbers": ["AZ-123", "KD-456"] or [],
  "confidence": 0.85,
  "summary": "Brief summary here" or null
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
            "Please extract information from this email:\n",
            f"**From**: {from_email}",
        ]

        if subject:
            prompt_parts.append(f"**Subject**: {subject}")

        prompt_parts.append(f"\n**Email Content**:\n{email_body}")

        return "\n".join(prompt_parts)


# Global instance
entity_extractor = EntityExtractor()
