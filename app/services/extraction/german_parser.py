"""
German Amount Parser (Phase 4: German Document Extraction)

Parses monetary amounts using German locale (de_DE) with US fallback.

USER DECISION: Try German format first (1.234,56), fall back to US (1,234.56),
accept whichever parses successfully.
"""

import re
from typing import Optional
from decimal import Decimal

import structlog

try:
    from babel.numbers import parse_decimal, NumberFormatError
except ImportError:
    parse_decimal = None
    NumberFormatError = ValueError

logger = structlog.get_logger(__name__)


def parse_german_amount(amount_str: str) -> float:
    """
    Parse German-format monetary amount with US format fallback.

    Args:
        amount_str: Amount string like "1.234,56 EUR", "EUR 1,234.56", "1234.56"

    Returns:
        Parsed float value (e.g., 1234.56)

    Raises:
        ValueError: If amount cannot be parsed in either format

    Examples:
        >>> parse_german_amount("1.234,56 EUR")
        1234.56
        >>> parse_german_amount("1,234.56 EUR")
        1234.56
        >>> parse_german_amount("2.500 EUR")
        2500.0
    """
    if parse_decimal is None:
        raise ImportError("babel package required. Install with: pip install babel")

    # Clean amount string: remove currency symbols and extra whitespace
    cleaned = amount_str.strip()
    cleaned = re.sub(r'\s*(EUR|Euro|\u20ac)\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()

    if not cleaned:
        raise ValueError(f"Empty amount after cleaning: {amount_str}")

    log = logger.bind(original=amount_str, cleaned=cleaned)

    # Detect format based on pattern to avoid ambiguous parsing
    # If ends with .XX (2 digits after period), it's likely US format
    # If ends with ,XX (2 digits after comma), it's likely German format
    has_comma = ',' in cleaned
    has_period = '.' in cleaned

    # Pattern detection: US format typically has period before last 2 digits
    us_pattern = re.search(r'\.\d{2}$', cleaned)
    # German pattern typically has comma before last 2 digits
    german_pattern = re.search(r',\d{2}$', cleaned)

    # Determine which locale to try first based on pattern
    if german_pattern and not us_pattern:
        # Clearly German format (ends with ,XX)
        locales = ['de_DE', 'en_US']
    elif us_pattern and not german_pattern:
        # Clearly US format (ends with .XX)
        locales = ['en_US', 'de_DE']
    elif has_comma and has_period:
        # Both separators present - use position to decide
        # If comma comes after period, it's German (1.234,56)
        # If period comes after comma, it's US (1,234.56)
        comma_pos = cleaned.rfind(',')
        period_pos = cleaned.rfind('.')
        if comma_pos > period_pos:
            locales = ['de_DE', 'en_US']
        else:
            locales = ['en_US', 'de_DE']
    else:
        # Single separator or no decimals - try German first (USER DECISION)
        locales = ['de_DE', 'en_US']

    # Try locales in determined order
    for locale in locales:
        try:
            result = float(parse_decimal(cleaned, locale=locale))
            log.debug("amount_parsed", locale=locale, result=result)
            return result
        except (NumberFormatError, ValueError) as e:
            log.debug("parse_failed", locale=locale, error=str(e))

    # Both failed - raise with helpful message
    raise ValueError(
        f"Cannot parse amount '{amount_str}' (cleaned: '{cleaned}') "
        f"as German (de_DE) or US (en_US) format"
    )


def extract_amount_from_text(text: str) -> Optional[float]:
    """
    Extract first monetary amount from text string.

    Searches for patterns like "1.234,56 EUR", "EUR 1234.56", "1234,56 Euro".

    Args:
        text: Text potentially containing a monetary amount

    Returns:
        Parsed amount as float, or None if no amount found

    Examples:
        >>> extract_amount_from_text("Die Gesamtforderung betraegt 1.234,56 EUR")
        1234.56
        >>> extract_amount_from_text("No amount here")
        None
    """
    # Pattern to find amount with optional currency
    amount_pattern = r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(?:EUR|Euro|\u20ac)?|(?:EUR|Euro|\u20ac)\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)'

    match = re.search(amount_pattern, text, re.IGNORECASE)
    if not match:
        return None

    # Get the matched amount (could be in group 1 or 2)
    amount_str = match.group(1) or match.group(2)
    if not amount_str:
        return None

    try:
        return parse_german_amount(amount_str)
    except ValueError:
        return None


__all__ = ["parse_german_amount", "extract_amount_from_text"]
