"""
Regression tests for EmailParser quote-removal guard.

Background: EmailReplyParser can over-strip when the actual reply lives
inside or directly above a quoted block, leaving Claude with too little
context to classify creditor replies. The guard in _remove_quoted_content
falls back to manual quote removal in that case.
"""

from app.services.email_parser import EmailParser


def _make_quoted_email(reply: str) -> str:
    """Build a realistic German reply-on-top email with a quoted original."""
    return (
        f"{reply}\n"
        "\n"
        "Am 14.05.2026 um 17:30 schrieb kanzlei@example.de:\n"
        "> Sehr geehrte Damen und Herren,\n"
        "> bitte teilen Sie uns die aktuelle Forderungshoehe "
        "> fuer unseren Mandanten Mustermann mit.\n"
        "> Mit freundlichen Gruessen, Kanzlei\n"
    )


class TestRemoveQuotedContentGuard:
    def setup_method(self):
        self.parser = EmailParser()

    def test_keeps_substantial_reply_above_quote(self):
        reply = (
            "Sehr geehrte Damen und Herren,\n"
            "die aktuelle Forderung gegen Herrn Mustermann betraegt "
            "1.234,56 EUR. Aktenzeichen: AZ-2026-42.\n"
            "Mit freundlichen Gruessen, Sparkasse Bochum"
        )
        result = self.parser._remove_quoted_content(_make_quoted_email(reply))

        assert "Forderung" in result
        assert "1.234,56" in result
        assert "Sparkasse Bochum" in result

    def test_falls_back_when_library_over_strips(self):
        """
        Reply substance is present in the input, but EmailReplyParser
        returns an almost-empty string. The guard must restore content
        via manual removal so quote markers are still stripped while
        the reply survives.
        """

        class FakeEmailReplyParser:
            @staticmethod
            def parse_reply(text: str) -> str:
                return "Hi"  # Pathological over-strip

        import app.services.email_parser as ep

        original = ep.EmailReplyParser
        ep.EmailReplyParser = FakeEmailReplyParser
        try:
            email = _make_quoted_email(
                "Die offene Forderung gegen Herrn Mustermann betraegt "
                "1.234,56 EUR. Bitte um Klaerung des Aktenzeichens AZ-2026-42. "
                "Mit freundlichen Gruessen, Sparkasse Bochum"
            )
            result = self.parser._remove_quoted_content(email)
        finally:
            ep.EmailReplyParser = original

        assert "Forderung" in result
        assert "1.234,56" in result
        # Manual fallback should still drop the "Am ... schrieb" quote header
        assert "schrieb" not in result

    def test_does_not_fallback_for_short_legitimate_emails(self):
        """
        Very short emails (e.g. brief acknowledgements) must not trigger
        the guard — there's nothing to strip and no fallback needed.
        """
        short = "Vielen Dank, Eingang bestaetigt."
        assert self.parser._remove_quoted_content(short) == short
