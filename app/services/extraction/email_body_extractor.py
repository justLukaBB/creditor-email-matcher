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
from app.services.extraction.german_preprocessor import GermanTextPreprocessor
from app.services.extraction.german_parser import parse_german_amount
from app.services.extraction.german_validator import GermanValidator

logger = logging.getLogger(__name__)


class EmailBodyExtractor:
    """
    Extracts structured data from email body text.

    This is the simplest extractor - operates on already-cleaned email text.
    Uses regex patterns to find German-format monetary amounts and entity names.
    """

    def __init__(self):
        self.preprocessor = GermanTextPreprocessor()
        self.validator = GermanValidator()
        # Amount patterns grouped by priority tier.
        # Tier 1 (TOTAL keywords) = definitive total amounts — always preferred.
        # Tier 2 (SPECIFIC keywords) = strong context like Gesamtforderung, Forderung.
        # Tier 3 (GENERIC) = catch-all EUR patterns.
        # Within each tier, highest amount wins. Higher tier always beats lower tier.
        self.amount_patterns_tiered = [
            # === TIER 1: Definitive total keywords (Summe, noch zu zahlen, insgesamt) ===
            (1, r'(?:noch\s+zu\s+zahlen|Noch\s+zu\s+zahlen)[:\s]*([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            (1, r'[Ii]nsgesamt[:\s]*([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            (1, r'[Ff]orderung[:\s\w]*?insgesamt[:\s]*([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            (1, r'[Ss]umme[:\s]*([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            (1, r'[Gg]esamtforderung[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            (1, r'[Gg]esamt(?:betrag|summe)[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            # === TIER 2: Specific claim keywords with EUR ===
            (2, r'[Ff]orderung(?:shöhe|sbetrag)?[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            (2, r'[Bb]etrag[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            (2, r'[Rr]estschuld[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            (2, r'[Oo]ffener\s+(?:Betrag|Saldo)[:\s\w]*?([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            # === TIER 2b: Keyword-based patterns WITHOUT currency (strong context) ===
            (2, r'[Gg]esamtforderung[:\s\w]*?([0-9][0-9.,]{2,})()'),
            (2, r'[Gg]esamt(?:betrag|summe)[:\s\w]*?([0-9][0-9.,]{2,})()'),
            (2, r'[Ff]orderung(?:shöhe|sbetrag)?[:\s\w]*?([0-9][0-9.,]{2,})()'),
            (2, r'[Ss]aldo[:\s\w]*?([0-9][0-9.,]{2,})()'),
            (2, r'[Rr]ückstand[:\s\w]*?([0-9][0-9.,]{2,})()'),
            (2, r'[Hh]auptforderung[:\s\w]*?([0-9][0-9.,]{2,})()'),
            (2, r'[Zz]ahlungsbetrag[:\s\w]*?([0-9][0-9.,]{2,})()'),
            # === TIER 3: Catch-all (any number near EUR) ===
            (3, r'([0-9][0-9.,]*)\s*(EUR|€|Euro)'),
            (3, r'(EUR|€)\s*([0-9][0-9.,]*)'),
        ]

        # Phone number prefixes to filter (German area codes like 0761, 0234, etc.)
        self._phone_prefixes = re.compile(r'^0\d{2,4}$')
        # Date patterns that look like German numbers (DD.MM.YYYY, D.M.YY)
        self._date_pattern = re.compile(r'^\d{1,2}\.\d{1,2}\.\d{2,4}$')

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

        # NEW: Apply German preprocessing (Unicode normalization + OCR correction)
        preprocessed_text = self.preprocessor.preprocess(email_text)

        # Find all amounts in preprocessed text (tiered by keyword context)
        found_amounts = self._find_amounts(preprocessed_text)

        logger.info(f"EmailBodyExtractor: found {len(found_amounts)} amounts in email body")

        # Select best amount: highest tier wins, within tier highest amount wins
        if found_amounts:
            best = self._select_best_amount(found_amounts)
            result.gesamtforderung = ExtractedAmount(
                value=best['value'],
                currency="EUR",
                raw_text=best['raw'],
                source="email_body",
                confidence=best['confidence'],
                tier=best['tier'],
            )
            logger.info(
                f"EmailBodyExtractor: selected {best['value']} EUR "
                f"(tier: {best['tier']}, confidence: {best['confidence']}) "
                f"from {len(found_amounts)} candidates"
            )

        # Extract entity names
        self._extract_names(preprocessed_text, result)

        return result

    def _find_amounts(self, text: str) -> List[Dict[str, Any]]:
        """
        Find all monetary amounts in text using tiered regex patterns.

        Returns list of dicts with: value, raw, confidence, tier
        """
        found_amounts = []

        for tier, pattern in self.amount_patterns_tiered:
            for match in re.finditer(pattern, text):
                try:
                    # Handle both patterns: "1.234,56 EUR" and "EUR 1.234,56"
                    groups = match.groups()
                    if groups[0] in ('EUR', '€'):
                        amount_str = groups[1]
                    else:
                        amount_str = groups[0]

                    # Filter phone numbers (e.g., 0761 from "Telefon 0761 279-2445")
                    amount_str_clean = amount_str.replace('.', '').replace(',', '')
                    if self._phone_prefixes.match(amount_str_clean):
                        logger.debug(f"EmailBodyExtractor: filtered phone number: {amount_str}")
                        continue

                    # Filter dates (e.g., "04.12.2024" would parse as 4122024)
                    if self._date_pattern.match(amount_str):
                        logger.debug(f"EmailBodyExtractor: filtered date pattern: {amount_str}")
                        continue

                    # Use babel-based parser
                    try:
                        amount_value = parse_german_amount(amount_str)
                    except ValueError:
                        continue  # Skip unparseable amounts

                    if amount_value > 0:
                        # Confidence based on currency marker and format
                        has_currency = any(c in match.group(0) for c in ('EUR', '€', 'Euro'))
                        has_german_decimal = ',' in amount_str and amount_str.index(',') > amount_str.rfind('.')
                        if has_german_decimal and has_currency:
                            confidence = 'HIGH'
                        elif has_german_decimal or has_currency:
                            confidence = 'MEDIUM'
                        else:
                            confidence = 'LOW'
                        found_amounts.append({
                            'value': amount_value,
                            'raw': match.group(0),
                            'confidence': confidence,
                            'tier': tier,
                        })
                except (ValueError, IndexError):
                    continue

        return found_amounts

    def _select_best_amount(self, amounts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Select best amount using tier priority, then highest value within tier.

        Tier 1 (Summe/insgesamt/noch zu zahlen) always beats Tier 2/3.
        Within same tier, highest amount wins.
        """
        # Group by tier, pick the best tier available
        best_tier = min(a['tier'] for a in amounts)
        tier_amounts = [a for a in amounts if a['tier'] == best_tier]
        return max(tier_amounts, key=lambda x: x['value'])

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

                # NEW: Apply OCR correction to name fields only
                name = self.preprocessor.correct_name_field(name)

                # NEW: Validate name format before accepting (REQ-GERMAN-04)
                if len(name) > 3 and self.validator.validate_name(name):
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
                elif len(name) > 3:
                    # Name failed validation - log but still include with lower confidence
                    logger.debug(f"Name '{name}' failed German format validation")
                    if entity_type == 'client':
                        result.client_name = ExtractedEntity(
                            value=name,
                            entity_type="client_name",
                            confidence="LOW"
                        )
                    else:
                        result.creditor_name = ExtractedEntity(
                            value=name,
                            entity_type="creditor_name",
                            confidence="LOW"
                        )
