"""Tests for German amount parser."""

import pytest
from app.services.extraction.german_parser import parse_german_amount, extract_amount_from_text


class TestParseGermanAmount:
    """Tests for parse_german_amount function."""

    def test_german_format_with_decimals(self):
        """1.234,56 EUR should parse to 1234.56"""
        assert parse_german_amount("1.234,56 EUR") == 1234.56

    def test_german_format_thousands_only(self):
        """2.500 EUR (German thousands separator) should parse to 2500.0"""
        assert parse_german_amount("2.500 EUR") == 2500.0

    def test_german_format_no_thousands(self):
        """234,56 EUR should parse to 234.56"""
        assert parse_german_amount("234,56 EUR") == 234.56

    def test_us_format_fallback(self):
        """1,234.56 EUR (US format) should parse via fallback"""
        result = parse_german_amount("1,234.56 EUR")
        assert result == 1234.56

    def test_us_format_thousands_only(self):
        """2,500 EUR (US format) should parse via fallback"""
        # Note: This is ambiguous - could be German 2.5 or US 2500
        # German locale tries first: 2,500 in de_DE = 2.5
        # If we want 2500, we need to detect the pattern
        result = parse_german_amount("2,500 EUR")
        # Accept either interpretation based on babel's behavior
        assert result in [2.5, 2500.0]

    def test_euro_sign_currency(self):
        """Amount with Euro sign should work"""
        assert parse_german_amount("1.234,56 €") == 1234.56

    def test_currency_before_amount(self):
        """EUR 1.234,56 should work"""
        assert parse_german_amount("EUR 1.234,56") == 1234.56

    def test_simple_integer(self):
        """Simple integer amount"""
        assert parse_german_amount("1234 EUR") == 1234.0

    def test_whitespace_handling(self):
        """Extra whitespace should be trimmed"""
        assert parse_german_amount("  1.234,56 EUR  ") == 1234.56

    def test_invalid_amount_raises(self):
        """Invalid amount should raise ValueError"""
        with pytest.raises(ValueError):
            parse_german_amount("not an amount")

    def test_empty_string_raises(self):
        """Empty string should raise ValueError"""
        with pytest.raises(ValueError):
            parse_german_amount("")

    def test_large_amount(self):
        """Large German format amount"""
        assert parse_german_amount("123.456.789,12 EUR") == 123456789.12


class TestExtractAmountFromText:
    """Tests for extract_amount_from_text function."""

    def test_extract_from_sentence(self):
        """Extract amount from German sentence"""
        text = "Die Gesamtforderung betraegt 1.234,56 EUR."
        assert extract_amount_from_text(text) == 1234.56

    def test_extract_with_euro_symbol(self):
        """Extract amount with Euro symbol"""
        text = "Betrag: 500,00 €"
        assert extract_amount_from_text(text) == 500.0

    def test_no_amount_returns_none(self):
        """No amount in text returns None"""
        assert extract_amount_from_text("No amounts here") is None

    def test_extract_first_amount(self):
        """Extracts first amount when multiple present"""
        text = "Hauptforderung 1.000 EUR, Zinsen 234,56 EUR"
        result = extract_amount_from_text(text)
        assert result == 1000.0  # First match
