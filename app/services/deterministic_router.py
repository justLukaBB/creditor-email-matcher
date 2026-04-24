"""
Deterministic Router — Phase 4 of Multi-Tenant Email Routing

Routes incoming creditor emails to the correct CreditorInquiry WITHOUT LLM processing.
Uses a 4-stage cascade:
  1. Reply-To address parsing (reply-{routingId}@reply.rasolv.ai)
  2. In-Reply-To / Message-ID header matching
  3. Body reference RAV-{routingId} regex
  4. From-address DB lookup (unique sender → match, ambiguous → fall through to LLM)

If any stage produces a definitive match, the email skips Agents 1-3 and goes
straight to confidence routing / dual-write.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session

from app.models.creditor_inquiry import CreditorInquiry

logger = logging.getLogger(__name__)

# --- V1 Patterns (legacy format: SC-A1221-42) ---
# Kept permissive for backwards compat with existing outbound emails. V2 runs first
# in the cascade, so V1's looseness only matters for V1 inquiries (lookup filters them anyway).
REPLY_TO_PATTERN = re.compile(
    r"reply-([A-Z]{2,3}-[A-Za-z0-9]+-\d+)@(?:reply\.insocore\.de|reply\.rasolv\.ai|[a-z]{2,3}\.insocore\.de)",
    re.IGNORECASE,
)
BODY_RAV_PATTERN = re.compile(
    r"RAV-([A-Z]{2,3}-[A-Za-z0-9]+-\d+)",
)
# V1 bare routing ID in subject/body: SC-A1221-42 (with optional collision suffix -b, -c, ...)
BARE_ROUTING_ID_PATTERN = re.compile(
    r"\b([A-Z]{2,3}-A\d+-\d{2,}(?:-[a-z])?)\b",
)
# V1 combined reference: {AZ}/{PREFIX}-{POS} e.g. "2025-00042/SC-03" or "AZ-1221/ES-12"
# Negative lookahead (?![-\d]) prevents partial match of V2 combined-ref (e.g. "2025-00042/SC-00-1")
COMBINED_REF_PATTERN = re.compile(
    r"([\w.-]*\d[\w.-]*)/([A-Z]{2,3})-(\d{2,})(?![-\d])",
)

# --- V2 Patterns ({KANZLEI}-{CREDITOR_IDX}-{LETTER}-{CLIENT_HASH}-{RAND}) ---
# Example: SC-00-1-a3f2-k7p
ROUTING_ID_V2_PATTERN = re.compile(
    r"^([A-Z]{2,3})-(\d{2,})-([12])-([a-z0-9]{4})-([a-z0-9]{3})$",
    re.IGNORECASE,
)
# Reply-To V2: reply-{V2ID}@...
REPLY_TO_V2_PATTERN = re.compile(
    r"reply-([A-Z]{2,3}-\d{2,}-[12]-[a-z0-9]{4}-[a-z0-9]{3})@(?:reply\.insocore\.de|reply\.rasolv\.ai|[a-z]{2,3}\.insocore\.de)",
    re.IGNORECASE,
)
BODY_RAV_V2_PATTERN = re.compile(
    r"RAV-([A-Z]{2,3}-\d{2,}-[12]-[a-z0-9]{4}-[a-z0-9]{3})",
    re.IGNORECASE,
)
# Bare V2 routing ID in subject/body — stricter than V1 because V2 has more segments
BARE_ROUTING_ID_V2_PATTERN = re.compile(
    r"\b([A-Z]{2,3}-\d{2,}-[12]-[a-z0-9]{4}-[a-z0-9]{3})\b",
    re.IGNORECASE,
)
# V2 combined reference in subject: {AZ}/{KANZLEI}-{CREDITOR_IDX}-{LETTER}
# e.g. "2025-00042/SC-00-1"
COMBINED_REF_V2_PATTERN = re.compile(
    r"([\w.-]*\d[\w.-]*)/([A-Z]{2,3})-(\d{2,})-([12])\b",
    re.IGNORECASE,
)


@dataclass
class RoutingResult:
    """Result of deterministic routing attempt."""
    matched: bool
    inquiry_id: Optional[int] = None
    inquiry: Optional[CreditorInquiry] = None
    routing_method: Optional[str] = None
    routing_id_parsed: Optional[str] = None
    routing_id_version: Optional[str] = None  # 'v1' | 'v2'
    parsed: Dict[str, Any] = field(default_factory=dict)  # V2 components: kanzlei, creditor_idx, letter, client_hash, rand
    confidence: float = 0.0


def parse_routing_id_v2(routing_id: str) -> Optional[Dict[str, Any]]:
    """Parse a V2 routing ID into components.

    Returns dict with: kanzlei_prefix, creditor_idx (int), letter ('1'|'2'),
    letter_type ('first'|'second'), client_hash, rand. Returns None on mismatch.
    """
    if not routing_id:
        return None
    m = ROUTING_ID_V2_PATTERN.match(routing_id.strip())
    if not m:
        return None
    letter = m.group(3)
    return {
        "kanzlei_prefix": m.group(1).upper(),
        "creditor_idx": int(m.group(2)),
        "letter": letter,
        "letter_type": "first" if letter == "1" else "second",
        "client_hash": m.group(4).lower(),
        "rand": m.group(5).lower(),
    }


class DeterministicRouter:
    """
    4-stage deterministic email router.

    Each stage is tried in order. First match wins.
    """

    def __init__(self, db: Session, kanzlei_id: Optional[str] = None):
        self.db = db
        self.kanzlei_id = kanzlei_id

    # Pattern to extract kanzlei prefix from insocore subdomain
    INSOCORE_DOMAIN_PATTERN = re.compile(
        r"@([a-z]{2,3})\.insocore\.de$", re.IGNORECASE
    )

    def route(
        self,
        to_addresses: Optional[List[str]] = None,
        in_reply_to: Optional[str] = None,
        from_email: Optional[str] = None,
        subject: Optional[str] = None,
        body_text: Optional[str] = None,
        body_html: Optional[str] = None,
    ) -> RoutingResult:
        """
        Attempt deterministic routing through 5 stages.

        Each stage tries V2 patterns first, then V1 as fallback.
        Returns RoutingResult with matched=True if a definitive match is found.
        """
        # Stage 1: Reply-To address (V2 → V1)
        result = self._stage_reply_to(to_addresses)
        if result.matched:
            return result

        # Stage 2: In-Reply-To / Message-ID header (version-agnostic — DB lookup)
        result = self._stage_message_id(in_reply_to)
        if result.matched:
            return result

        # Stage 3: Body RAV-{id} reference (V2 → V1)
        result = self._stage_body_reference(body_text, body_html)
        if result.matched:
            return result

        # Stage 3.5: Bare routing ID or combined reference in subject/body (V2 → V1)
        result = self._stage_subject_reference(subject, body_text, body_html)
        if result.matched:
            return result

        # Stage 4: From-address DB lookup (scoped to kanzlei)
        result = self._stage_from_address(from_email)
        if result.matched:
            return result

        logger.info("deterministic_routing_no_match", extra={
            "to_addresses": to_addresses,
            "in_reply_to": in_reply_to,
            "from_email": from_email,
        })
        return RoutingResult(matched=False)

    def _stage_reply_to(self, to_addresses: Optional[List[str]]) -> RoutingResult:
        """Stage 1: Parse reply-{routingId}@<domain> from To addresses. V2 first, V1 fallback."""
        if not to_addresses:
            return RoutingResult(matched=False)

        # Pass 1: V2 reply-to pattern
        for addr in to_addresses:
            m = REPLY_TO_V2_PATTERN.search(addr)
            if m:
                routing_id = m.group(1).upper()
                parsed = parse_routing_id_v2(routing_id)
                inquiry = self._lookup_by_routing_id(routing_id) or (
                    self._lookup_by_routing_id_v2(parsed) if parsed else None
                )
                if inquiry:
                    logger.info("deterministic_match_reply_to_v2", extra={
                        "routing_id": routing_id,
                        "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="reply_to_address",
                        routing_id_parsed=routing_id,
                        routing_id_version="v2",
                        parsed=parsed or {},
                        confidence=0.99,
                    )

        # Pass 2: V1 reply-to pattern (legacy)
        for addr in to_addresses:
            m = REPLY_TO_PATTERN.search(addr)
            if m:
                routing_id = m.group(1).upper()
                inquiry = self._lookup_by_routing_id(routing_id)
                if inquiry:
                    logger.info("deterministic_match_reply_to", extra={
                        "routing_id": routing_id,
                        "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="reply_to_address",
                        routing_id_parsed=routing_id,
                        routing_id_version="v1",
                        confidence=0.99,
                    )

        return RoutingResult(matched=False)

    def _stage_message_id(self, in_reply_to: Optional[str]) -> RoutingResult:
        """Stage 2: Match In-Reply-To header against stored resend_message_id."""
        if not in_reply_to:
            return RoutingResult(matched=False)

        # In-Reply-To can contain angle brackets: <msg-id@domain>
        clean_id = in_reply_to.strip().strip("<>")
        if not clean_id:
            return RoutingResult(matched=False)

        query = self.db.query(CreditorInquiry).filter(
            CreditorInquiry.resend_message_id == clean_id
        )
        if self.kanzlei_id:
            query = query.filter(CreditorInquiry.kanzlei_id == self.kanzlei_id)
        inquiry = query.first()

        if inquiry:
            logger.info("deterministic_match_message_id", extra={
                "in_reply_to": clean_id,
                "inquiry_id": inquiry.id,
            })
            parsed = parse_routing_id_v2(inquiry.routing_id) if inquiry.routing_id else None
            version = (inquiry.routing_id_version or ("v2" if parsed else "v1"))
            return RoutingResult(
                matched=True,
                inquiry_id=inquiry.id,
                inquiry=inquiry,
                routing_method="in_reply_to_header",
                routing_id_parsed=inquiry.routing_id,
                routing_id_version=version,
                parsed=parsed or {},
                confidence=0.98,
            )

        return RoutingResult(matched=False)

    def _stage_body_reference(
        self, body_text: Optional[str], body_html: Optional[str]
    ) -> RoutingResult:
        """Stage 3: Find RAV-{routingId} pattern in email body. V2 first, V1 fallback."""
        for body in (body_text, body_html):
            if not body:
                continue

            # V2 first
            m2 = BODY_RAV_V2_PATTERN.search(body)
            if m2:
                routing_id = m2.group(1).upper()
                parsed = parse_routing_id_v2(routing_id)
                inquiry = self._lookup_by_routing_id(routing_id) or (
                    self._lookup_by_routing_id_v2(parsed) if parsed else None
                )
                if inquiry:
                    logger.info("deterministic_match_body_ref_v2", extra={
                        "routing_id": routing_id, "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="body_reference",
                        routing_id_parsed=routing_id,
                        routing_id_version="v2",
                        parsed=parsed or {},
                        confidence=0.95,
                    )

            # V1 fallback
            match = BODY_RAV_PATTERN.search(body)
            if match:
                routing_id = match.group(1).upper()
                inquiry = self._lookup_by_routing_id(routing_id)
                if inquiry:
                    logger.info("deterministic_match_body_ref", extra={
                        "routing_id": routing_id, "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="body_reference",
                        routing_id_parsed=routing_id,
                        routing_id_version="v1",
                        confidence=0.95,
                    )

        return RoutingResult(matched=False)

    def _stage_subject_reference(
        self, subject: Optional[str], body_text: Optional[str], body_html: Optional[str]
    ) -> RoutingResult:
        """Stage 3.5: Find routing reference in subject or body. V2 first, V1 fallback.

        Patterns tried in order:
        1. V2 bare routing ID: "SC-00-1-a3f2-k7p"
        2. V2 combined reference: "2025-00042/SC-00-1"
        3. V1 combined reference: "2025-00042/SC-03"
        4. V1 bare routing ID: "SC-A1221-42"
        """
        for text in (subject, body_text, body_html):
            if not text:
                continue

            # --- V2: Bare routing ID ---
            m2 = BARE_ROUTING_ID_V2_PATTERN.search(text)
            if m2:
                routing_id = m2.group(1).upper()
                parsed = parse_routing_id_v2(routing_id)
                inquiry = self._lookup_by_routing_id(routing_id) or (
                    self._lookup_by_routing_id_v2(parsed) if parsed else None
                )
                if inquiry:
                    logger.info("deterministic_match_subject_ref_v2", extra={
                        "routing_id": routing_id, "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="subject_reference",
                        routing_id_parsed=routing_id,
                        routing_id_version="v2",
                        parsed=parsed or {},
                        confidence=0.93,
                    )

            # --- V2: Combined reference (e.g. "2025-00042/SC-00-1") ---
            cmatch2 = COMBINED_REF_V2_PATTERN.search(text)
            if cmatch2:
                aktenzeichen = cmatch2.group(1)
                prefix = cmatch2.group(2).upper()
                creditor_idx = int(cmatch2.group(3))
                letter = cmatch2.group(4)
                letter_type = "first" if letter == "1" else "second"
                inquiry = self._lookup_by_combined_ref_v2(
                    kanzlei_prefix=prefix,
                    creditor_idx=creditor_idx,
                    letter_type=letter_type,
                    aktenzeichen=aktenzeichen,
                )
                if inquiry:
                    combined_ref = f"{aktenzeichen}/{prefix}-{str(creditor_idx).zfill(2)}-{letter}"
                    logger.info("deterministic_match_combined_ref_v2", extra={
                        "combined_ref": combined_ref, "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="combined_reference",
                        routing_id_parsed=inquiry.routing_id,
                        routing_id_version="v2",
                        parsed={
                            "kanzlei_prefix": prefix,
                            "creditor_idx": creditor_idx,
                            "letter": letter,
                            "letter_type": letter_type,
                        },
                        confidence=0.94,
                    )

            # --- V1: Combined reference (e.g. "2025-00042/SC-03") ---
            cmatch = COMBINED_REF_PATTERN.search(text)
            if cmatch:
                aktenzeichen = cmatch.group(1)
                prefix = cmatch.group(2).upper()
                position = int(cmatch.group(3))
                inquiry = self._lookup_by_combined_ref(prefix, position, aktenzeichen)
                if inquiry:
                    combined_ref = f"{aktenzeichen}/{prefix}-{str(position).zfill(2)}"
                    logger.info("deterministic_match_combined_ref", extra={
                        "combined_ref": combined_ref, "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="combined_reference",
                        routing_id_parsed=inquiry.routing_id,
                        routing_id_version="v1",
                        confidence=0.94,
                    )

            # --- V1: Bare routing ID (e.g. "SC-A1221-42") ---
            match = BARE_ROUTING_ID_PATTERN.search(text)
            if match:
                routing_id = match.group(1).upper()
                inquiry = self._lookup_by_routing_id(routing_id)
                if inquiry:
                    logger.info("deterministic_match_subject_ref", extra={
                        "routing_id": routing_id, "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="subject_reference",
                        routing_id_parsed=routing_id,
                        routing_id_version="v1",
                        confidence=0.93,
                    )

        return RoutingResult(matched=False)

    def _lookup_by_routing_id_v2(
        self, parsed: Optional[Dict[str, Any]]
    ) -> Optional[CreditorInquiry]:
        """V2 lookup: match on (kanzlei_prefix, creditor_idx_snapshot, letter_type, client_hash).

        This hits the composite index ix_creditor_inquiries_v2_lookup.
        Used as a fallback when the exact routing_id string isn't stored (e.g. old Inquiries
        migrated mid-transition) but the V2 components are parseable.
        """
        if not parsed:
            return None
        query = self.db.query(CreditorInquiry).filter(
            CreditorInquiry.kanzlei_prefix == parsed["kanzlei_prefix"],
            CreditorInquiry.creditor_idx_snapshot == parsed["creditor_idx"],
            CreditorInquiry.letter_type == parsed["letter_type"],
            CreditorInquiry.client_hash == parsed["client_hash"],
        )
        if self.kanzlei_id:
            query = query.filter(CreditorInquiry.kanzlei_id == self.kanzlei_id)
        # Most recent first — handles edge cases where rand suffix was regenerated
        return query.order_by(CreditorInquiry.sent_at.desc()).first()

    def _lookup_by_combined_ref_v2(
        self,
        kanzlei_prefix: str,
        creditor_idx: int,
        letter_type: str,
        aktenzeichen: str = "",
    ) -> Optional[CreditorInquiry]:
        """V2 combined-ref lookup (no client_hash — resolve via aktenzeichen match)."""
        query = self.db.query(CreditorInquiry).filter(
            CreditorInquiry.kanzlei_prefix == kanzlei_prefix,
            CreditorInquiry.creditor_idx_snapshot == creditor_idx,
            CreditorInquiry.letter_type == letter_type,
            CreditorInquiry.routing_id_version == "v2",
        )
        if self.kanzlei_id:
            query = query.filter(CreditorInquiry.kanzlei_id == self.kanzlei_id)

        candidates = query.order_by(CreditorInquiry.sent_at.desc()).limit(50).all()
        if not candidates:
            return None
        if len(candidates) == 1 or not aktenzeichen:
            return candidates[0]

        # Disambiguate by aktenzeichen when multiple matches exist
        az_norm = aktenzeichen.strip().lower()
        for inq in candidates:
            ref = (inq.reference_number or "").strip().lower()
            if ref and (ref == az_norm or ref in az_norm or az_norm in ref):
                return inq
        # Fallback to most recent
        return candidates[0]

    def _lookup_by_combined_ref(
        self, kanzlei_prefix: str, position: int, aktenzeichen: str = ""
    ) -> Optional[CreditorInquiry]:
        """Lookup inquiry by kanzlei prefix + creditor position + aktenzeichen.

        The routing_id format is PREFIX-A{azHash}-{POS}. We match on prefix,
        position suffix, and — to avoid cross-client collisions — verify the
        aktenzeichen hash matches the routing_id's middle segment.
        """
        query = self.db.query(CreditorInquiry).filter(
            CreditorInquiry.kanzlei_prefix == kanzlei_prefix,
        )
        if self.kanzlei_id:
            query = query.filter(CreditorInquiry.kanzlei_id == self.kanzlei_id)

        pos_str = str(position).zfill(2)
        candidates = query.order_by(CreditorInquiry.sent_at.desc()).limit(100).all()

        # Extract numeric hash from aktenzeichen for verification (same logic as portal)
        az_digits = re.sub(r"\D", "", aktenzeichen)[-6:] if aktenzeichen else ""

        for inq in candidates:
            if not inq.routing_id or not inq.routing_id.endswith(f"-{pos_str}"):
                continue
            # If we have an aktenzeichen, verify the hash matches
            if az_digits and f"-A{az_digits}-" not in inq.routing_id:
                continue
            return inq
        return None

    def _stage_from_address(self, from_email: Optional[str]) -> RoutingResult:
        """
        Stage 4: Lookup from_email in creditor_inquiries.

        Only matches if EXACTLY ONE inquiry exists for this sender
        (scoped to kanzlei if known).
        Ambiguous (multiple inquiries) falls through to LLM pipeline.
        """
        if not from_email:
            return RoutingResult(matched=False)

        query = self.db.query(CreditorInquiry).filter(
            CreditorInquiry.creditor_email == from_email.lower(),
            CreditorInquiry.status == "sent",
        )
        if self.kanzlei_id:
            query = query.filter(CreditorInquiry.kanzlei_id == self.kanzlei_id)
        inquiries = query.order_by(CreditorInquiry.sent_at.desc()).limit(2).all()

        if len(inquiries) == 1:
            inquiry = inquiries[0]
            logger.info("deterministic_match_from_address", extra={
                "from_email": from_email,
                "inquiry_id": inquiry.id,
            })
            return RoutingResult(
                matched=True,
                inquiry_id=inquiry.id,
                inquiry=inquiry,
                routing_method="from_address_unique",
                routing_id_parsed=inquiry.routing_id,
                confidence=0.85,
            )

        if len(inquiries) > 1:
            logger.info("deterministic_from_address_ambiguous", extra={
                "from_email": from_email,
                "inquiry_count": len(inquiries),
            })

        return RoutingResult(matched=False)

    def _lookup_by_routing_id(self, routing_id: str) -> Optional[CreditorInquiry]:
        """Lookup CreditorInquiry by routing_id (scoped to kanzlei if known)."""
        query = self.db.query(CreditorInquiry).filter(
            CreditorInquiry.routing_id == routing_id
        )
        if self.kanzlei_id:
            query = query.filter(CreditorInquiry.kanzlei_id == self.kanzlei_id)
        return query.first()
