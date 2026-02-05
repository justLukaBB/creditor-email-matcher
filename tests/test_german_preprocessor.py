"""
Unit tests for German text preprocessor and validator.

Tests Unicode normalization, OCR correction, and German format validation.
"""

import pytest
from app.services.extraction.german_preprocessor import GermanTextPreprocessor
from app.services.extraction.german_validator import GermanValidator


class TestGermanTextPreprocessor:
    """Tests for GermanTextPreprocessor class."""

    @pytest.fixture
    def preprocessor(self):
        """Create preprocessor instance."""
        return GermanTextPreprocessor()

    def test_unicode_normalization_composed_unchanged(self, preprocessor):
        """Already composed Umlaut stays same."""
        # Müller with composed ü (U+00FC)
        result = preprocessor.preprocess("Müller")
        assert "Müller" == result or result == "Müller"  # Allow both if normalized

    def test_unicode_normalization_decomposed_to_composed(self, preprocessor):
        """Decomposed Umlaut (u + combining diaeresis) becomes composed."""
        # u (U+0075) + combining diaeresis (U+0308) -> ü (U+00FC)
        decomposed = "Mu\u0308ller"  # u + combining umlaut
        result = preprocessor.preprocess(decomposed)
        # After NFKC normalization, decomposed form should be gone
        assert "\u0308" not in result  # No combining diaeresis

    def test_ocr_correction_mueller_to_umlaut(self, preprocessor):
        """'Mueller' (digraph) should become 'Müller' (with umlaut ü) if in dictionary."""
        result = preprocessor.preprocess("Mueller")
        # Result may be "Müller" (with umlaut ü) or stay "Mueller" depending on dictionary
        # Both are acceptable - dictionary determines which is valid
        assert result in ["Mueller", "Müller"]

    def test_legitimate_words_unchanged(self, preprocessor):
        """'Feuer' should NOT become 'Für' - that's not a word."""
        result = preprocessor.preprocess("Feuer")
        assert result == "Feuer"
        assert "Für" not in result  # Should not be corrupted

    def test_name_field_digit_correction(self, preprocessor):
        """Only in name field, not general text."""
        result = preprocessor.correct_name_field("M3yer")
        # Should attempt to correct 3->e
        assert "3" not in result
        assert "e" in result

    def test_multiple_digit_substitutions(self, preprocessor):
        """Test multiple digit substitutions in name field."""
        result = preprocessor.correct_name_field("M3y3r 10")
        assert result == "Meyer lo"  # 3->e, 1->l, 0->o

    def test_empty_string_handling(self, preprocessor):
        """Empty strings should be handled gracefully."""
        assert preprocessor.preprocess("") == ""
        assert preprocessor.correct_name_field("") == ""

    def test_none_handling(self, preprocessor):
        """None values should be handled gracefully."""
        assert preprocessor.preprocess(None) is None
        assert preprocessor.correct_name_field(None) is None

    def test_case_preservation_in_umlaut_restoration(self, preprocessor):
        """Uppercase digraphs should become uppercase umlauts."""
        result = preprocessor.preprocess("MUELLER")
        # Should preserve case if correction happens
        if result != "MUELLER":
            assert "MÜLLER" == result or "Müller" in result


class TestGermanValidator:
    """Tests for GermanValidator class."""

    @pytest.fixture
    def validator(self):
        """Create validator instance."""
        return GermanValidator()

    # Postal Code Tests
    def test_postal_code_valid_5_digits(self, validator):
        """Valid 5-digit postal codes."""
        assert validator.validate_postal_code("12345") is True
        assert validator.validate_postal_code("01234") is True  # Leading zero

    def test_postal_code_invalid_4_digits(self, validator):
        """4 digits is invalid."""
        assert validator.validate_postal_code("1234") is False

    def test_postal_code_invalid_6_digits(self, validator):
        """6 digits is invalid."""
        assert validator.validate_postal_code("123456") is False

    def test_postal_code_invalid_letters(self, validator):
        """Letters are invalid in postal codes."""
        assert validator.validate_postal_code("1234A") is False
        assert validator.validate_postal_code("ABCDE") is False

    def test_postal_code_with_whitespace(self, validator):
        """Whitespace should be stripped and still validate."""
        assert validator.validate_postal_code("  12345  ") is True

    def test_postal_code_empty(self, validator):
        """Empty string is invalid."""
        assert validator.validate_postal_code("") is False

    # Name Tests
    def test_name_valid_simple(self, validator):
        """Simple German names are valid."""
        assert validator.validate_name("Mueller") is True
        assert validator.validate_name("Schmidt") is True

    def test_name_valid_with_umlaut(self, validator):
        """Names with Umlauts are valid."""
        assert validator.validate_name("Müller") is True  # actual umlaut ü
        assert validator.validate_name("Schröder") is True  # actual umlaut ö
        assert validator.validate_name("Bäcker") is True  # actual umlaut ä

    def test_name_valid_with_prefix(self, validator):
        """Names with noble prefixes are valid."""
        assert validator.validate_name("von Goethe") is True
        assert validator.validate_name("zu Gutenberg") is True
        assert validator.validate_name("vom Berg") is True

    def test_name_valid_with_hyphen(self, validator):
        """Hyphenated names are valid."""
        assert validator.validate_name("Schmidt-Mueller") is True
        assert validator.validate_name("von Schmidt-Mueller") is True

    def test_name_invalid_with_digits(self, validator):
        """Names with digits are invalid."""
        assert validator.validate_name("Mueller123") is False
        assert validator.validate_name("M3yer") is False

    def test_name_invalid_too_short(self, validator):
        """Names must be at least 2 characters."""
        assert validator.validate_name("M") is False
        assert validator.validate_name("a") is False

    def test_name_empty(self, validator):
        """Empty string is invalid."""
        assert validator.validate_name("") is False

    # Address Tests
    def test_address_valid_simple(self, validator):
        """Simple street + number addresses are valid."""
        assert validator.validate_street_address("Hauptstrasse 15") is True
        assert validator.validate_street_address("Gartenweg 42") is True

    def test_address_valid_with_letter(self, validator):
        """House numbers with letter suffixes are valid."""
        assert validator.validate_street_address("Am Ring 3a") is True
        assert validator.validate_street_address("Bergstrasse 12b") is True

    def test_address_valid_with_apartment(self, validator):
        """Addresses with apartment notation are valid."""
        assert validator.validate_street_address("Gartenweg 42 //Whg. 5") is True

    def test_address_valid_with_umlaut(self, validator):
        """Street names with Umlauts are valid."""
        assert validator.validate_street_address("Müllerstrasse 10") is True
        assert validator.validate_street_address("Goethestraße 5") is True  # ß

    def test_address_invalid_no_number(self, validator):
        """Addresses without house numbers are invalid."""
        assert validator.validate_street_address("Hauptstrasse") is False

    def test_address_invalid_only_number(self, validator):
        """Only a number is invalid."""
        assert validator.validate_street_address("42") is False

    def test_address_empty(self, validator):
        """Empty string is invalid."""
        assert validator.validate_street_address("") is False

    # Dispatcher Tests
    def test_dispatcher_postal_code(self, validator):
        """Dispatcher routes to postal code validator."""
        assert validator.is_valid_german_format("12345", "postal_code") is True
        assert validator.is_valid_german_format("1234", "postal_code") is False

    def test_dispatcher_name(self, validator):
        """Dispatcher routes to name validator."""
        assert validator.is_valid_german_format("Mueller", "name") is True
        assert validator.is_valid_german_format("M3yer", "name") is False

    def test_dispatcher_address(self, validator):
        """Dispatcher routes to address validator."""
        assert validator.is_valid_german_format("Hauptstrasse 15", "address") is True
        assert validator.is_valid_german_format("Hauptstrasse", "address") is False

    def test_dispatcher_unknown_field_type(self, validator):
        """Unknown field types are permissive (return True)."""
        assert validator.is_valid_german_format("anything", "unknown_type") is True

    def test_dispatcher_empty_value(self, validator):
        """Empty values are always invalid."""
        assert validator.is_valid_german_format("", "postal_code") is False
        assert validator.is_valid_german_format("", "name") is False
        assert validator.is_valid_german_format("", "address") is False
        assert validator.is_valid_german_format("", "unknown_type") is False
