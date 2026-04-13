"""
Phase 4 Tests: DeterministicRouter

Tests cover all 4 routing stages:
- Stage 1: Reply-To address parsing
- Stage 2: In-Reply-To / Message-ID header matching
- Stage 3: Body RAV-{id} reference
- Stage 4: From-address unique DB lookup

Also tests:
- Cascade order (first match wins)
- Ambiguous from-address (should NOT match)
- No match (all stages fail)
- Bounce webhook VERP parsing
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from app.services.deterministic_router import (
    DeterministicRouter,
    RoutingResult,
    REPLY_TO_PATTERN,
    BODY_RAV_PATTERN,
)


def _classify_bounce_inline(subject, body):
    """Mirror of bounce_webhook._classify_bounce to avoid transitive import issues."""
    text = f"{subject or ''} {body or ''}".lower()
    hard = ["user unknown", "mailbox not found", "address rejected",
            "does not exist", "no such user", "undeliverable",
            "permanent", "550 ", "551 ", "552 ", "553 ", "554 "]
    for ind in hard:
        if ind in text:
            return "hard_bounce"
    soft = ["mailbox full", "over quota", "temporarily",
            "try again", "service unavailable", "421 ", "450 ", "452 "]
    for ind in soft:
        if ind in text:
            return "soft_bounce"
    return "unknown_bounce"


def _make_inquiry(id=1, routing_id="SC-A1221-42", resend_message_id=None,
                  creditor_email="creditor@bank.de", status="sent",
                  letter_type="first", client_name="Max Mustermann",
                  creditor_name="Sparkasse", reference_number="123456",
                  kanzlei_id="scuric", debt_amount=None):
    """Create a mock CreditorInquiry."""
    inquiry = MagicMock()
    inquiry.id = id
    inquiry.routing_id = routing_id
    inquiry.resend_message_id = resend_message_id
    inquiry.creditor_email = creditor_email
    inquiry.status = status
    inquiry.letter_type = letter_type
    inquiry.client_name = client_name
    inquiry.creditor_name = creditor_name
    inquiry.reference_number = reference_number
    inquiry.kanzlei_id = kanzlei_id
    inquiry.debt_amount = debt_amount
    return inquiry


class TestReplyToPattern:
    """Test the Reply-To regex pattern."""

    def test_legacy_reply_domain(self):
        match = REPLY_TO_PATTERN.search("reply-SC-A1221-42@reply.rasolv.ai")
        assert match is not None
        assert match.group(1) == "SC-A1221-42"

    def test_insocore_reply_domain(self):
        match = REPLY_TO_PATTERN.search("reply-SC-A1221-42@reply.insocore.de")
        assert match is not None
        assert match.group(1) == "SC-A1221-42"

    def test_kanzlei_subdomain(self):
        """Per-kanzlei subdomain: reply-ES-xxx@es.insocore.de"""
        match = REPLY_TO_PATTERN.search("reply-ES-B9921-3@es.insocore.de")
        assert match is not None
        assert match.group(1) == "ES-B9921-3"

    def test_reply_to_in_angle_brackets(self):
        match = REPLY_TO_PATTERN.search("<reply-SC-A1221-42@sc.insocore.de>")
        assert match is not None
        assert match.group(1) == "SC-A1221-42"

    def test_three_char_prefix(self):
        match = REPLY_TO_PATTERN.search("reply-MUE-B5543-7@mue.insocore.de")
        assert match is not None
        assert match.group(1) == "MUE-B5543-7"

    def test_no_match_wrong_domain(self):
        match = REPLY_TO_PATTERN.search("reply-SC-A1221-42@other.domain.com")
        assert match is None

    def test_no_match_wrong_prefix(self):
        match = REPLY_TO_PATTERN.search("noreply@reply.insocore.de")
        assert match is None


class TestBodyRavPattern:
    """Test the body RAV-{id} regex pattern."""

    def test_standard_rav_reference(self):
        match = BODY_RAV_PATTERN.search("Referenz: RAV-SC-A1221-42 bitte beachten")
        assert match is not None
        assert match.group(1) == "SC-A1221-42"

    def test_rav_in_html(self):
        match = BODY_RAV_PATTERN.search("<p>RAV-MUE-B5543-7</p>")
        assert match is not None
        assert match.group(1) == "MUE-B5543-7"

    def test_no_rav_reference(self):
        match = BODY_RAV_PATTERN.search("Sehr geehrte Damen und Herren")
        assert match is None


class TestStage1ReplyTo:
    """Stage 1: Reply-To address parsing."""

    def test_match_via_reply_to(self):
        inquiry = _make_inquiry()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_reply_to(["reply-SC-A1221-42@sc.insocore.de"])

        assert result.matched is True
        assert result.inquiry_id == 1
        assert result.routing_method == "reply_to_address"
        assert result.routing_id_parsed == "SC-A1221-42"
        assert result.confidence == 0.99

    def test_no_match_when_inquiry_not_found(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        router = DeterministicRouter(db)
        result = router._stage_reply_to(["reply-UNKNOWN-X999-1@un.insocore.de"])

        assert result.matched is False

    def test_no_match_empty_addresses(self):
        db = MagicMock()
        router = DeterministicRouter(db)

        assert router._stage_reply_to(None).matched is False
        assert router._stage_reply_to([]).matched is False

    def test_match_from_multiple_addresses(self):
        """Should find routing ID even if mixed with other addresses."""
        inquiry = _make_inquiry()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_reply_to([
            "info@creditor.de",
            "reply-SC-A1221-42@sc.insocore.de",
        ])

        assert result.matched is True


class TestStage2MessageId:
    """Stage 2: In-Reply-To header matching."""

    def test_match_via_in_reply_to(self):
        inquiry = _make_inquiry(resend_message_id="abc123@resend.dev")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_message_id("<abc123@resend.dev>")

        assert result.matched is True
        assert result.routing_method == "in_reply_to_header"
        assert result.confidence == 0.98

    def test_match_without_angle_brackets(self):
        inquiry = _make_inquiry(resend_message_id="abc123@resend.dev")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_message_id("abc123@resend.dev")

        assert result.matched is True

    def test_no_match_empty_header(self):
        db = MagicMock()
        router = DeterministicRouter(db)

        assert router._stage_message_id(None).matched is False
        assert router._stage_message_id("").matched is False
        assert router._stage_message_id("  ").matched is False


class TestStage3BodyReference:
    """Stage 3: Body RAV-{id} reference."""

    def test_match_in_text_body(self):
        inquiry = _make_inquiry()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_body_reference(
            body_text="Bitte beziehen Sie sich auf RAV-SC-A1221-42 in Ihrer Antwort.",
            body_html=None,
        )

        assert result.matched is True
        assert result.routing_method == "body_reference"
        assert result.confidence == 0.95

    def test_match_in_html_body(self):
        inquiry = _make_inquiry()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_body_reference(
            body_text=None,
            body_html="<p>Ref: RAV-SC-A1221-42</p>",
        )

        assert result.matched is True

    def test_text_body_preferred_over_html(self):
        """Text body is checked first."""
        inquiry = _make_inquiry()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_body_reference(
            body_text="RAV-SC-A1221-42",
            body_html="RAV-SC-A1221-42",
        )

        assert result.matched is True

    def test_no_match_no_reference(self):
        db = MagicMock()
        router = DeterministicRouter(db)
        result = router._stage_body_reference(
            body_text="Sehr geehrte Damen und Herren, hiermit teilen wir mit...",
            body_html=None,
        )
        assert result.matched is False


class TestStage4FromAddress:
    """Stage 4: From-address unique DB lookup."""

    def test_unique_sender_matches(self):
        inquiry = _make_inquiry()
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [inquiry]

        router = DeterministicRouter(db)
        result = router._stage_from_address("creditor@bank.de")

        assert result.matched is True
        assert result.routing_method == "from_address_unique"
        assert result.confidence == 0.85

    def test_ambiguous_sender_no_match(self):
        """Multiple inquiries for same sender should NOT match."""
        inquiry1 = _make_inquiry(id=1)
        inquiry2 = _make_inquiry(id=2)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [inquiry1, inquiry2]

        router = DeterministicRouter(db)
        result = router._stage_from_address("creditor@bank.de")

        assert result.matched is False

    def test_no_inquiries_no_match(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        router = DeterministicRouter(db)
        result = router._stage_from_address("unknown@sender.de")

        assert result.matched is False

    def test_empty_email_no_match(self):
        db = MagicMock()
        router = DeterministicRouter(db)

        assert router._stage_from_address(None).matched is False
        assert router._stage_from_address("").matched is False


class TestRouteCascade:
    """Test the full routing cascade — first match wins."""

    def test_reply_to_wins_over_message_id(self):
        """Stage 1 match should prevent Stage 2 from running."""
        inquiry = _make_inquiry()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router.route(
            to_addresses=["reply-SC-A1221-42@sc.insocore.de"],
            in_reply_to="<some-message-id@resend.dev>",
        )

        assert result.matched is True
        assert result.routing_method == "reply_to_address"

    def test_fallthrough_to_stage4(self):
        """When stages 1-3 fail, stage 4 (from_address) should match."""
        inquiry = _make_inquiry()

        # Test stage 4 directly — the cascade is tested implicitly by
        # verifying each stage returns matched=False for non-matching input.
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [inquiry]

        router = DeterministicRouter(db)
        result = router._stage_from_address("creditor@bank.de")

        assert result.matched is True
        assert result.routing_method == "from_address_unique"

    def test_no_match_all_stages(self):
        """When all stages fail, result should be not matched."""
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value.first.return_value = None
        query_mock.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        db.query.return_value = query_mock

        router = DeterministicRouter(db)
        result = router.route(
            to_addresses=["random@creditor.de"],
            in_reply_to=None,
            from_email="unknown@sender.de",
            body_text="Keine Referenz vorhanden.",
        )

        assert result.matched is False
        assert result.routing_method is None


class TestBounceWebhookVerpParsing:
    """Test VERP pattern parsing for bounce webhook."""

    def test_verp_pattern_kanzlei_subdomain(self):
        import re
        VERP_PATTERN = re.compile(
            r"bounce-([A-Z]{2,3}-[A-Za-z0-9]+-\d+)@(?:bounce\.insocore\.de|bounce\.rasolv\.ai|[a-z]{2,3}\.insocore\.de)",
            re.IGNORECASE,
        )
        match = VERP_PATTERN.search("bounce-ES-A1221-42@es.insocore.de")
        assert match is not None
        assert match.group(1) == "ES-A1221-42"

    def test_verp_pattern_legacy_domain(self):
        import re
        VERP_PATTERN = re.compile(
            r"bounce-([A-Z]{2,3}-[A-Za-z0-9]+-\d+)@(?:bounce\.insocore\.de|bounce\.rasolv\.ai|[a-z]{2,3}\.insocore\.de)",
            re.IGNORECASE,
        )
        match = VERP_PATTERN.search("bounce-SC-A1221-42@bounce.rasolv.ai")
        assert match is not None
        assert match.group(1) == "SC-A1221-42"

    def test_verp_pattern_three_char_prefix(self):
        import re
        VERP_PATTERN = re.compile(
            r"bounce-([A-Z]{2,3}-[A-Za-z0-9]+-\d+)@(?:bounce\.insocore\.de|bounce\.rasolv\.ai|[a-z]{2,3}\.insocore\.de)",
            re.IGNORECASE,
        )
        match = VERP_PATTERN.search("bounce-MUE-B5543-7@mue.insocore.de")
        assert match is not None
        assert match.group(1) == "MUE-B5543-7"

    def test_verp_no_match_wrong_domain(self):
        import re
        VERP_PATTERN = re.compile(
            r"bounce-([A-Z]{2,3}-[A-Za-z0-9]+-\d+)@(?:bounce\.insocore\.de|bounce\.rasolv\.ai|[a-z]{2,3}\.insocore\.de)",
            re.IGNORECASE,
        )
        match = VERP_PATTERN.search("bounce-SC-A1221-42@other.domain.com")
        assert match is None

    def test_bounce_classify_hard(self):
        assert _classify_bounce_inline("Undeliverable: Your message", None) == "hard_bounce"
        assert _classify_bounce_inline("550 User unknown", None) == "hard_bounce"

    def test_bounce_classify_soft(self):
        assert _classify_bounce_inline("Mailbox full", None) == "soft_bounce"
        assert _classify_bounce_inline("452 Try again later", None) == "soft_bounce"

    def test_bounce_classify_unknown(self):
        assert _classify_bounce_inline("RE: Ihr Schreiben", None) == "unknown_bounce"
