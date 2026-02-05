"""
German Validator for Extraction Pipeline

Validates German-specific formats: postal codes, names, addresses.
Ensures data quality before database storage.
"""

import re
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)


class GermanValidator:
    """
    Validates German-specific data formats.

    Handles:
    1. Postal codes (exactly 5 digits)
    2. Names (with Umlauts, noble prefixes, hyphens)
    3. Street addresses (street name + house number + optional apartment)
    """

    # German postal code: exactly 5 digits
    POSTAL_CODE_PATTERN = r'^\d{5}$'

    # German name: allows Umlauts, hyphens, spaces, noble prefixes (von, zu, etc)
    # Unicode escapes for portability: ä=\u00e4, ö=\u00f6, ü=\u00fc, Ä=\u00c4, Ö=\u00d6, Ü=\u00dc, ß=\u00df
    NAME_PATTERN = r'^[A-Za-z\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df\-\s]+(von|zu|vom|zum|zur|der)?\s*[A-Za-z\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df\-\s]*$'

    # German street address: street name + house number + optional apartment
    ADDRESS_PATTERN = r'^[A-Za-z\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df\.\-\s]+\s+\d+[a-z]?(\s*//.+)?$'

    def __init__(self):
        """Initialize validator with compiled regex patterns."""
        self.postal_code_regex = re.compile(self.POSTAL_CODE_PATTERN)
        self.name_regex = re.compile(self.NAME_PATTERN, re.IGNORECASE)
        self.address_regex = re.compile(self.ADDRESS_PATTERN, re.IGNORECASE)
        logger.info("GermanValidator initialized")

    def validate_postal_code(self, code: str) -> bool:
        """
        Validate German postal code format.

        Args:
            code: Postal code string

        Returns:
            True if valid (exactly 5 digits), False otherwise
        """
        if not code:
            return False

        # Strip whitespace before checking
        code_stripped = code.strip()

        is_valid = bool(self.postal_code_regex.match(code_stripped))

        logger.debug(
            "Postal code validation",
            code=code_stripped,
            valid=is_valid
        )

        return is_valid

    def validate_name(self, name: str) -> bool:
        """
        Validate German name format.

        Allows:
        - Umlauts (ä, ö, ü, Ä, Ö, Ü, ß)
        - Hyphens (e.g., "Schmidt-Mueller")
        - Spaces (e.g., "von Goethe")
        - Noble prefixes (von, zu, vom, zum, zur, der)

        Args:
            name: Name string

        Returns:
            True if valid German name format, False otherwise
        """
        if not name:
            return False

        # Strip whitespace before checking
        name_stripped = name.strip()

        # Minimum length 2 characters
        if len(name_stripped) < 2:
            return False

        is_valid = bool(self.name_regex.match(name_stripped))

        logger.debug(
            "Name validation",
            name=name_stripped,
            valid=is_valid
        )

        return is_valid

    def validate_street_address(self, address: str) -> bool:
        """
        Validate German street address format.

        Expected format: "Street Name Number[letter] [//Apartment]"
        Examples:
        - "Hauptstrasse 15"
        - "Am Ring 3a"
        - "Gartenweg 42 //Whg. 5"

        Args:
            address: Street address string

        Returns:
            True if valid German street address format, False otherwise
        """
        if not address:
            return False

        # Strip whitespace before checking
        address_stripped = address.strip()

        is_valid = bool(self.address_regex.match(address_stripped))

        logger.debug(
            "Street address validation",
            address=address_stripped,
            valid=is_valid
        )

        return is_valid

    def is_valid_german_format(self, value: str, field_type: str) -> bool:
        """
        Dispatcher method for field-specific validation.

        Args:
            value: Value to validate
            field_type: Type of field ('postal_code', 'name', 'address')

        Returns:
            True if valid for field type, True for unknown field_type (permissive by default)
        """
        if not value:
            return False

        validators = {
            'postal_code': self.validate_postal_code,
            'name': self.validate_name,
            'address': self.validate_street_address,
        }

        validator = validators.get(field_type)

        if validator is None:
            # Permissive by default for unknown field types
            logger.debug(
                "Unknown field type, accepting by default",
                field_type=field_type,
                value=value
            )
            return True

        return validator(value)
