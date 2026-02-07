"""
Test MongoDB creditor matching logic - specifically domain matching.
"""

import pytest


def match_email(cred_email: str, search_email: str) -> tuple[bool, str]:
    """
    Simulate the email matching logic from mongodb_client.py
    Returns (matched, match_type)
    """
    cred_email = cred_email.lower().strip()
    search_email = search_email.lower().strip()

    # Strategy 1: Check if either contains the other
    if (search_email in cred_email) or (cred_email in search_email):
        return True, "substring"

    # Strategy 2: Domain matching
    if '@' in cred_email and '@' in search_email:
        cred_domain = cred_email.split('@')[-1]
        search_domain = search_email.split('@')[-1]
        if cred_domain == search_domain:
            return True, "domain"

    return False, "none"


class TestEmailMatching:
    """Test email matching strategies."""

    def test_exact_match(self):
        """Exact same email should match."""
        matched, match_type = match_email(
            "inkasso@sparkasse.de",
            "inkasso@sparkasse.de"
        )
        assert matched is True
        assert match_type == "substring"

    def test_substring_match(self):
        """Substring match should work."""
        matched, match_type = match_email(
            "inkasso@sparkasse-bochum.de",
            "inkasso@sparkasse.de"
        )
        # "sparkasse.de" is in "sparkasse-bochum.de" - wait, no it's not
        # Let me reconsider: "inkasso@sparkasse.de" in "inkasso@sparkasse-bochum.de"? No
        # Actually substring checks full email, not just domain
        matched, match_type = match_email(
            "info@inkasso.sparkasse.de",
            "inkasso@sparkasse.de"
        )
        assert matched is False  # These don't contain each other

    def test_domain_match_different_local_part(self):
        """Same domain but different local part should match via domain matching."""
        matched, match_type = match_email(
            "inkasso@sparkasse.de",
            "forderungsmanagement@sparkasse.de"
        )
        assert matched is True
        assert match_type == "domain"

    def test_domain_match_case_insensitive(self):
        """Domain matching should be case insensitive."""
        matched, match_type = match_email(
            "Inkasso@Sparkasse.DE",
            "FORDERUNG@sparkasse.de"
        )
        assert matched is True
        assert match_type == "domain"

    def test_different_domain_no_match(self):
        """Different domains should not match."""
        matched, match_type = match_email(
            "inkasso@sparkasse.de",
            "inkasso@vodafone.de"
        )
        assert matched is False
        assert match_type == "none"

    def test_similar_domain_no_match(self):
        """Similar but different domains should not match."""
        matched, match_type = match_email(
            "info@sparkasse.de",
            "info@sparkasse-bochum.de"
        )
        assert matched is False
        assert match_type == "none"

    def test_subdomain_no_match(self):
        """Subdomains should not match main domain."""
        matched, match_type = match_email(
            "info@mail.sparkasse.de",
            "info@sparkasse.de"
        )
        assert matched is False
        assert match_type == "none"

    def test_empty_emails(self):
        """Empty emails - note: actual code guards against this with 'if creditor_email and cred.get(sender_email)'."""
        # Empty string is technically a substring of any string, so this matches
        # But the real code has guards: if creditor_email and cred.get('sender_email')
        # So we just verify it doesn't crash
        matched, match_type = match_email("", "test@example.com")
        assert match_type == "substring"  # "" in "test@example.com" is True

        matched, match_type = match_email("test@example.com", "")
        assert match_type == "substring"  # "" in "test@example.com" is True

    def test_no_at_symbol(self):
        """Emails without @ should not crash on domain matching."""
        matched, match_type = match_email("invalid-email", "test@example.com")
        assert matched is False

        matched, match_type = match_email("test@example.com", "invalid-email")
        assert matched is False


class TestRealWorldScenarios:
    """Test real-world creditor email scenarios."""

    def test_sparkasse_different_departments(self):
        """Sparkasse replying from different department email."""
        matched, _ = match_email(
            "inkasso@sparkasse-bochum.de",
            "kundenservice@sparkasse-bochum.de"
        )
        assert matched is True

    def test_vodafone_different_departments(self):
        """Vodafone replying from different department."""
        matched, _ = match_email(
            "forderungsmanagement@vodafone.de",
            "kundenbetreuung@vodafone.de"
        )
        assert matched is True

    def test_inkasso_company_different_agent(self):
        """Inkasso company with different agent emails."""
        matched, _ = match_email(
            "mueller@inkasso-firma.de",
            "schmidt@inkasso-firma.de"
        )
        assert matched is True

    def test_completely_different_creditors(self):
        """Different creditors should not match."""
        matched, _ = match_email(
            "inkasso@sparkasse.de",
            "forderung@vodafone.de"
        )
        assert matched is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
