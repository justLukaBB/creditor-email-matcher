"""
Tests for EmailBodyExtractor tiered amount selection and phone number filtering.
"""

import pytest

from app.services.extraction.email_body_extractor import EmailBodyExtractor


@pytest.fixture
def extractor():
    return EmailBodyExtractor()


class TestTieredAmountSelection:
    """Tier 1 (Summe/insgesamt) should always be preferred over Tier 2/3."""

    def test_insgesamt_beats_catch_all(self, extractor):
        """Badenova scenario: 'Forderung von insgesamt 182,30 EUR' should win."""
        text = (
            "wir nehmen Bezug auf Ihre E-Mail und übersenden in der Anlage "
            "die Kontoinformation, welcher Sie die aktuelle Forderung von "
            "insgesamt 182,30 EUR entnehmen können."
        )
        result = extractor.extract(text)
        assert result.gesamtforderung is not None
        assert result.gesamtforderung.value == 182.30
        assert result.gesamtforderung.tier == 1

    def test_summe_preferred_over_einzelposten(self, extractor):
        """'Summe: 182,30 EUR' should win over individual line items."""
        text = (
            "Forderung 80,00 EUR\n"
            "Rückläuferkosten 10,15 EUR\n"
            "Mahnanforderung 2,00 EUR\n"
            "Summe 182,30 EUR\n"
        )
        result = extractor.extract(text)
        assert result.gesamtforderung is not None
        assert result.gesamtforderung.value == 182.30
        assert result.gesamtforderung.tier == 1

    def test_noch_zu_zahlen_preferred(self, extractor):
        """'noch zu zahlen: 182,30 EUR' is tier 1."""
        text = "noch zu zahlen: 182,30 EUR"
        result = extractor.extract(text)
        assert result.gesamtforderung is not None
        assert result.gesamtforderung.value == 182.30
        assert result.gesamtforderung.tier == 1

    def test_gesamtforderung_is_tier1(self, extractor):
        """'Gesamtforderung: 1.234,56 EUR' is tier 1."""
        text = "Die Gesamtforderung beträgt 1.234,56 EUR."
        result = extractor.extract(text)
        assert result.gesamtforderung is not None
        assert result.gesamtforderung.value == 1234.56
        assert result.gesamtforderung.tier == 1


class TestPhoneNumberFilter:
    """Phone numbers should not be extracted as amounts."""

    def test_phone_number_not_extracted_as_amount(self, extractor):
        """'0761' from phone number should not become 761 EUR."""
        text = (
            "Telefon 0761 279 2445 (kostenlos)\n"
            "Forderung von insgesamt 182,30 EUR"
        )
        result = extractor.extract(text)
        assert result.gesamtforderung is not None
        assert result.gesamtforderung.value == 182.30

    def test_plz_not_extracted(self, extractor):
        """Postleitzahl should not be extracted."""
        text = (
            "79108 Freiburg\n"
            "Betrag 500,00 EUR"
        )
        result = extractor.extract(text)
        assert result.gesamtforderung is not None
        assert result.gesamtforderung.value == 500.00


class TestBackwardsCompatibility:
    """Existing behavior should be preserved when no tier 1 keywords present."""

    def test_forderung_with_eur_still_works(self, extractor):
        """Standard 'Forderung: 1.234,56 EUR' still extracted (tier 2)."""
        text = "Forderungshöhe: 1.234,56 EUR"
        result = extractor.extract(text)
        assert result.gesamtforderung is not None
        assert result.gesamtforderung.value == 1234.56

    def test_plain_eur_amount_still_works(self, extractor):
        """Plain '500,00 EUR' still works as catch-all (tier 3)."""
        text = "Bitte überweisen Sie 500,00 EUR auf unser Konto."
        result = extractor.extract(text)
        assert result.gesamtforderung is not None
        assert result.gesamtforderung.value == 500.00
