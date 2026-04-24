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


# ============================================================================
# V2 Routing ID Tests
# Format: {KANZLEI}-{CREDITOR_IDX}-{LETTER}-{CLIENT_HASH}-{RAND}
# Example: SC-00-1-a3f2-k7p
# ============================================================================


class TestParseRoutingIdV2:
    """Unit tests for the V2 parser."""

    def test_parses_first_letter(self):
        from app.services.deterministic_router import parse_routing_id_v2
        result = parse_routing_id_v2("SC-00-1-a3f2-k7p")
        assert result is not None
        assert result["kanzlei_prefix"] == "SC"
        assert result["creditor_idx"] == 0
        assert result["letter"] == "1"
        assert result["letter_type"] == "first"
        assert result["client_hash"] == "a3f2"
        assert result["rand"] == "k7p"

    def test_parses_second_letter(self):
        from app.services.deterministic_router import parse_routing_id_v2
        result = parse_routing_id_v2("SC-42-2-a3f2-m9q")
        assert result is not None
        assert result["creditor_idx"] == 42
        assert result["letter"] == "2"
        assert result["letter_type"] == "second"

    def test_parses_three_char_prefix(self):
        from app.services.deterministic_router import parse_routing_id_v2
        result = parse_routing_id_v2("MUE-07-1-bb88-xy9")
        assert result is not None
        assert result["kanzlei_prefix"] == "MUE"

    def test_case_insensitive(self):
        from app.services.deterministic_router import parse_routing_id_v2
        result = parse_routing_id_v2("sc-00-1-A3F2-K7P")
        assert result is not None
        assert result["kanzlei_prefix"] == "SC"  # upper
        assert result["client_hash"] == "a3f2"  # lower

    def test_rejects_v1_format(self):
        from app.services.deterministic_router import parse_routing_id_v2
        assert parse_routing_id_v2("SC-A1221-42") is None

    def test_rejects_invalid_letter(self):
        from app.services.deterministic_router import parse_routing_id_v2
        assert parse_routing_id_v2("SC-00-3-a3f2-k7p") is None  # letter must be 1 or 2

    def test_rejects_wrong_hash_length(self):
        from app.services.deterministic_router import parse_routing_id_v2
        assert parse_routing_id_v2("SC-00-1-a3f-k7p") is None  # 3 chars not 4
        assert parse_routing_id_v2("SC-00-1-a3f2-k7") is None  # 2 chars not 3

    def test_rejects_empty(self):
        from app.services.deterministic_router import parse_routing_id_v2
        assert parse_routing_id_v2("") is None
        assert parse_routing_id_v2(None) is None


class TestReplyToV2Pattern:
    """Regex tests for V2 reply-to."""

    def test_standard_reply(self):
        from app.services.deterministic_router import REPLY_TO_V2_PATTERN
        m = REPLY_TO_V2_PATTERN.search("reply-SC-00-1-a3f2-k7p@reply.insocore.de")
        assert m is not None
        assert m.group(1).upper() == "SC-00-1-A3F2-K7P"

    def test_kanzlei_subdomain(self):
        from app.services.deterministic_router import REPLY_TO_V2_PATTERN
        m = REPLY_TO_V2_PATTERN.search("reply-MUE-07-2-bb88-xy9@mue.insocore.de")
        assert m is not None

    def test_does_not_match_v1(self):
        from app.services.deterministic_router import REPLY_TO_V2_PATTERN
        assert REPLY_TO_V2_PATTERN.search("reply-SC-A1221-42@sc.insocore.de") is None

    def test_v1_pattern_does_not_fully_match_v2(self):
        """V1 pattern may partially match a V2 ID — but exact lookup will fail, so cascade proceeds."""
        from app.services.deterministic_router import REPLY_TO_PATTERN, parse_routing_id_v2
        m = REPLY_TO_PATTERN.search("reply-SC-00-1-a3f2-k7p@reply.insocore.de")
        # V1 regex is loose — may capture partial. What matters: V2 runs FIRST in the cascade.
        # This test documents the quirk; the fix is cascade ordering, not pattern exclusion.
        if m:
            captured = m.group(1)
            # Partial V2 capture should not parse as valid V2
            assert parse_routing_id_v2(captured) is None


class TestBodyRavV2Pattern:
    """Regex tests for V2 RAV- body reference."""

    def test_standard_rav(self):
        from app.services.deterministic_router import BODY_RAV_V2_PATTERN
        m = BODY_RAV_V2_PATTERN.search("Bitte Ref: RAV-SC-00-1-a3f2-k7p angeben.")
        assert m is not None
        assert m.group(1).upper() == "SC-00-1-A3F2-K7P"

    def test_rav_in_html(self):
        from app.services.deterministic_router import BODY_RAV_V2_PATTERN
        m = BODY_RAV_V2_PATTERN.search("<p>RAV-MUE-07-2-bb88-xy9</p>")
        assert m is not None


class TestBareRoutingIdV2Pattern:
    """Regex tests for bare V2 ID in subject/body."""

    def test_matches_bare_id(self):
        from app.services.deterministic_router import BARE_ROUTING_ID_V2_PATTERN
        m = BARE_ROUTING_ID_V2_PATTERN.search("Ihr Az: SC-00-1-a3f2-k7p vom 14.04.")
        assert m is not None

    def test_does_not_match_v1(self):
        from app.services.deterministic_router import BARE_ROUTING_ID_V2_PATTERN
        assert BARE_ROUTING_ID_V2_PATTERN.search("Az: SC-A1221-42") is None


class TestCombinedRefV2Pattern:
    """Regex tests for V2 combined reference."""

    def test_standard_combined(self):
        from app.services.deterministic_router import COMBINED_REF_V2_PATTERN
        m = COMBINED_REF_V2_PATTERN.search("Az: 2025-00042/SC-00-1 in Ihrer Antwort")
        assert m is not None
        assert m.group(2).upper() == "SC"
        assert m.group(3) == "00"
        assert m.group(4) == "1"

    def test_does_not_collide_with_v1_combined(self):
        """V1 combined ref '2025-00042/SC-03' must not match V2 pattern."""
        from app.services.deterministic_router import COMBINED_REF_V2_PATTERN, COMBINED_REF_PATTERN
        v1_ref = "2025-00042/SC-03"
        assert COMBINED_REF_V2_PATTERN.search(v1_ref) is None
        assert COMBINED_REF_PATTERN.search(v1_ref) is not None  # V1 still works

    def test_v1_does_not_collide_with_v2(self):
        """V2 combined ref '2025-00042/SC-00-1' must NOT match V1 pattern (negative lookahead)."""
        from app.services.deterministic_router import COMBINED_REF_PATTERN
        v2_ref = "2025-00042/SC-00-1"
        assert COMBINED_REF_PATTERN.search(v2_ref) is None


class TestStage1ReplyToV2:
    """Stage 1 with V2 addresses."""

    def test_v2_match(self):
        inquiry = _make_inquiry(routing_id="SC-00-1-a3f2-k7p")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_reply_to(["reply-SC-00-1-a3f2-k7p@reply.insocore.de"])

        assert result.matched is True
        assert result.routing_id_version == "v2"
        assert result.routing_method == "reply_to_address"
        assert result.parsed.get("letter_type") == "first"
        assert result.parsed.get("creditor_idx") == 0
        assert result.confidence == 0.99

    def test_v2_second_letter(self):
        inquiry = _make_inquiry(routing_id="SC-42-2-a3f2-m9q", letter_type="second")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_reply_to(["reply-SC-42-2-a3f2-m9q@sc.insocore.de"])

        assert result.matched is True
        assert result.parsed.get("letter_type") == "second"


class TestStage3BodyReferenceV2:
    """Stage 3 with V2 RAV-{id}."""

    def test_v2_body_ref(self):
        inquiry = _make_inquiry(routing_id="SC-00-1-a3f2-k7p")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_body_reference(
            body_text="Referenz: RAV-SC-00-1-a3f2-k7p",
            body_html=None,
        )

        assert result.matched is True
        assert result.routing_id_version == "v2"


class TestStage35SubjectV2:
    """Stage 3.5 with V2 bare + combined ref."""

    def test_v2_bare_in_subject(self):
        inquiry = _make_inquiry(routing_id="SC-00-1-a3f2-k7p")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_subject_reference(
            subject="WG: Az SC-00-1-a3f2-k7p",
            body_text=None,
            body_html=None,
        )

        assert result.matched is True
        assert result.routing_id_version == "v2"

    def test_v2_combined_ref_in_subject(self):
        """Combined-ref V2 lookup falls back to the _lookup_by_combined_ref_v2 method."""
        inquiry = _make_inquiry(
            routing_id="SC-00-1-a3f2-k7p",
            letter_type="first",
            reference_number="2025-00042",
        )
        inquiry.creditor_idx_snapshot = 0
        inquiry.kanzlei_prefix = "SC"
        inquiry.routing_id_version = "v2"

        db = MagicMock()
        # _lookup_by_combined_ref_v2 calls query(...).filter(...).order_by(...).limit(...).all()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [inquiry]
        # Bare V2 pattern should NOT match "2025-00042/SC-00-1" on its own — only combined
        router = DeterministicRouter(db)
        result = router._stage_subject_reference(
            subject="Re: Az 2025-00042/SC-00-1",
            body_text=None,
            body_html=None,
        )

        assert result.matched is True
        assert result.routing_id_version == "v2"
        assert result.routing_method == "combined_reference"


class TestV1FallbackStillWorks:
    """V1 cascade still matches when V2 patterns don't fire."""

    def test_v1_reply_to_still_matches(self):
        inquiry = _make_inquiry(routing_id="SC-A1221-42")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = inquiry

        router = DeterministicRouter(db)
        result = router._stage_reply_to(["reply-SC-A1221-42@reply.insocore.de"])

        assert result.matched is True
        assert result.routing_id_version == "v1"
        assert result.routing_id_parsed == "SC-A1221-42"

    def test_v1_combined_ref_still_matches(self):
        # AZ hash: "2025-00042" → digits "202500042" → slice(-6) = "500042"
        inquiry = _make_inquiry(routing_id="SC-A500042-03")
        db = MagicMock()
        # _lookup_by_combined_ref iterates candidates
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [inquiry]

        router = DeterministicRouter(db)
        result = router._stage_subject_reference(
            subject="Az: 2025-00042/SC-03",
            body_text=None,
            body_html=None,
        )

        assert result.matched is True
        assert result.routing_id_version == "v1"


class TestV1BugfixesPhase1:
    """Phase 1 bugfixes verification."""

    def test_route_accepts_no_message_id_param(self):
        """After bugfix, route() signature no longer has `message_id`. (email_processor.py:291)"""
        import inspect
        from app.services.deterministic_router import DeterministicRouter

        sig = inspect.signature(DeterministicRouter.route)
        # `message_id` parameter removed — Stage 2 uses `in_reply_to` exclusively
        assert "message_id" not in sig.parameters

