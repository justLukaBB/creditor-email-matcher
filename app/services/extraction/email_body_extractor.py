"""
Email Body Text Extractor

Extracts Forderungshoehe (claim amounts) and entity names from email body text
using regex patterns. No API calls - pure text parsing.

Phase 3 Scope:
- Gesamtforderung (total claim amount) with German number format
- client_name (Mandant/Schuldner)
- creditor_name (Gläubiger/Inkasso)
"""

import re
import logging
from typing import List, Dict, Any

from app.models.extraction_result import (
    SourceExtractionResult,
    ExtractedAmount,
    ExtractedEntity,
)

logger = logging.getLogger(__name__)


class EmailBodyExtractor:
    """
    Extracts structured data from email body text.

    This is the simplest extractor - operates on already-cleaned email text.
    Uses regex patterns to find German-format monetary amounts and entity names.
    """

    def __init__(self):
        # Amount patterns ordered by specificity (most specific first)
        # Allow flexible text between keyword and amount (e.g., "beträgt", "von", ":")
        self.amount_patterns = [
            # Explicit Gesamtforderung (with flexible separator)
            r'[Gg]esamtforderung[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€)',
            r'[Gg]esamt(?:betrag|summe)[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€)',
            # Forderung patterns
            r'[Ff]orderung(?:shöhe|sbetrag)?[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€)',
            # Generic amount patterns
            r'[Bb]etrag[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€)',
            r'[Ss]umme[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€)',
            # Amount followed by currency (catch-all)
            r'([0-9][0-9.,]*)\s*(EUR|€)',
            # Currency first patterns
            r'(EUR|€)\s*([0-9][0-9.,]*)',
        ]

    def extract(self, email_text: str) -> SourceExtractionResult:
        """
        Extract claim amounts and entity names from email body text.

        Args:
            email_text: Plain text email body (already cleaned by EmailParser)

        Returns:
            SourceExtractionResult with extracted amounts and names
        """
        result = SourceExtractionResult(
            source_type="email_body",
            source_name="email_body",
            extraction_method="text_parsing",
            tokens_used=0  # No API calls
        )

        if not email_text or not email_text.strip():
            result.error = "empty_email_body"
            logger.warning("EmailBodyExtractor: empty email body")
            return result

        # Find all amounts in text
        found_amounts = self._find_amounts(email_text)

        logger.info(f"EmailBodyExtractor: found {len(found_amounts)} amounts in email body")

        # Take the highest amount (USER DECISION: highest wins)
        if found_amounts:
            best = max(found_amounts, key=lambda x: x['value'])
            result.gesamtforderung = ExtractedAmount(
                value=best['value'],
                currency="EUR",
                raw_text=best['raw'],
                source="email_body",
                confidence=best['confidence']
            )
            logger.info(
                f"EmailBodyExtractor: selected {best['value']} EUR "
                f"(confidence: {best['confidence']}) from {len(found_amounts)} candidates"
            )

        # Extract entity names
        self._extract_names(email_text, result)

        return result

    def _find_amounts(self, text: str) -> List[Dict[str, Any]]:
        """
        Find all monetary amounts in text using regex patterns.

        Returns list of dicts with: value, raw, confidence
        """
        found_amounts = []

        for pattern in self.amount_patterns:
            for match in re.finditer(pattern, text):
                try:
                    # Handle both patterns: "1.234,56 EUR" and "EUR 1.234,56"
                    groups = match.groups()
                    if groups[0] in ('EUR', '€'):
                        amount_str = groups[1]
                    else:
                        amount_str = groups[0]

                    # Parse German number format: 1.234,56 -> 1234.56
                    normalized = amount_str.replace('.', '').replace(',', '.')
                    amount_value = float(normalized)

                    if amount_value > 0:
                        found_amounts.append({
                            'value': amount_value,
                            'raw': match.group(0),
                            'confidence': 'HIGH' if ',' in amount_str else 'MEDIUM'
                        })
                except ValueError:
                    continue

        return found_amounts

    def _extract_names(self, text: str, result: SourceExtractionResult) -> None:
        """
        Extract client and creditor names from email text.

        Note: This is basic extraction - Phase 4 will improve German name extraction.
        """
        name_patterns = [
            # Client/debtor patterns (index 0)
            (r'(?:Mandant|Schuldner|Kunde)[:\s]+([A-Za-zäöüÄÖÜß\-,\s]+)', 'client'),
            # Creditor patterns (index 1)
            (r'(?:Gläubiger|Inkasso|Firma)[:\s]+([A-Za-zäöüÄÖÜß\-,\s]+)', 'creditor'),
        ]

        for pattern, entity_type in name_patterns:
            match = re.search(pattern, text)
            if match:
                name = match.group(1).strip()
                # Filter out too-short matches and clean trailing punctuation
                name = re.sub(r'[,.\s]+$', '', name)
                if len(name) > 3:
                    if entity_type == 'client':
                        result.client_name = ExtractedEntity(
                            value=name,
                            entity_type="client_name",
                            confidence="MEDIUM"
                        )
                        logger.debug(f"EmailBodyExtractor: found client_name: {name}")
                    else:
                        result.creditor_name = ExtractedEntity(
                            value=name,
                            entity_type="creditor_name",
                            confidence="MEDIUM"
                        )
                        logger.debug(f"EmailBodyExtractor: found creditor_name: {name}")
