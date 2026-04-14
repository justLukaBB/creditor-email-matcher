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
from dataclasses import dataclass
from typing import Optional, List

from sqlalchemy.orm import Session

from app.models.creditor_inquiry import CreditorInquiry

logger = logging.getLogger(__name__)

# Patterns — support both legacy (reply.insocore.de) and per-kanzlei ({prefix}.insocore.de) domains
REPLY_TO_PATTERN = re.compile(
    r"reply-([A-Z]{2,3}-[A-Za-z0-9]+-\d+)@(?:reply\.insocore\.de|reply\.rasolv\.ai|[a-z]{2,3}\.insocore\.de)",
    re.IGNORECASE,
)
BODY_RAV_PATTERN = re.compile(
    r"RAV-([A-Z]{2,3}-[A-Za-z0-9]+-\d+)",
)
# Matches bare routing IDs in subject/body: SC-A1221-42 (without RAV- prefix)
BARE_ROUTING_ID_PATTERN = re.compile(
    r"\b([A-Z]{2,3}-A\d+-\d{2,}(?:-[a-z])?)\b",
)
# Matches combined per-creditor reference: {AZ}/{PREFIX}-{POS}
# e.g. "2025-00042/SC-03" or "AZ-1221/ES-12"
COMBINED_REF_PATTERN = re.compile(
    r"([\w.-]*\d[\w.-]*)/([A-Z]{2,3})-(\d{2,})",
)


@dataclass
class RoutingResult:
    """Result of deterministic routing attempt."""
    matched: bool
    inquiry_id: Optional[int] = None
    inquiry: Optional[CreditorInquiry] = None
    routing_method: Optional[str] = None
    routing_id_parsed: Optional[str] = None
    confidence: float = 0.0


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
        message_id: Optional[str] = None,
        from_email: Optional[str] = None,
        subject: Optional[str] = None,
        body_text: Optional[str] = None,
        body_html: Optional[str] = None,
    ) -> RoutingResult:
        """
        Attempt deterministic routing through 5 stages.

        Returns RoutingResult with matched=True if a definitive match is found.
        """
        # Stage 1: Reply-To address
        result = self._stage_reply_to(to_addresses)
        if result.matched:
            return result

        # Stage 2: In-Reply-To / Message-ID header
        result = self._stage_message_id(in_reply_to)
        if result.matched:
            return result

        # Stage 3: Body RAV-{id} reference
        result = self._stage_body_reference(body_text, body_html)
        if result.matched:
            return result

        # Stage 3.5: Bare routing ID in subject or body (e.g. "SC-A1221-42")
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
        """Stage 1: Parse reply-{routingId}@reply.rasolv.ai from To addresses."""
        if not to_addresses:
            return RoutingResult(matched=False)

        for addr in to_addresses:
            match = REPLY_TO_PATTERN.search(addr)
            if match:
                routing_id = match.group(1).upper()
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
            return RoutingResult(
                matched=True,
                inquiry_id=inquiry.id,
                inquiry=inquiry,
                routing_method="in_reply_to_header",
                routing_id_parsed=inquiry.routing_id,
                confidence=0.98,
            )

        return RoutingResult(matched=False)

    def _stage_body_reference(
        self, body_text: Optional[str], body_html: Optional[str]
    ) -> RoutingResult:
        """Stage 3: Find RAV-{routingId} pattern in email body."""
        for body in (body_text, body_html):
            if not body:
                continue
            match = BODY_RAV_PATTERN.search(body)
            if match:
                routing_id = match.group(1).upper()
                inquiry = self._lookup_by_routing_id(routing_id)
                if inquiry:
                    logger.info("deterministic_match_body_ref", extra={
                        "routing_id": routing_id,
                        "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="body_reference",
                        routing_id_parsed=routing_id,
                        confidence=0.95,
                    )

        return RoutingResult(matched=False)

    def _stage_subject_reference(
        self, subject: Optional[str], body_text: Optional[str], body_html: Optional[str]
    ) -> RoutingResult:
        """Stage 3.5: Find routing reference in subject or body.

        Checks two patterns:
        1. Combined reference: "2025-00042/SC-03" → lookup by kanzlei_prefix + position
        2. Bare routing ID: "SC-A1221-42" → lookup by routing_id
        """
        for text in (subject, body_text, body_html):
            if not text:
                continue

            # Try combined reference first (e.g. "2025-00042/SC-03")
            cmatch = COMBINED_REF_PATTERN.search(text)
            if cmatch:
                aktenzeichen = cmatch.group(1)
                prefix = cmatch.group(2).upper()
                position = int(cmatch.group(3))
                inquiry = self._lookup_by_combined_ref(prefix, position, aktenzeichen)
                if inquiry:
                    combined_ref = f"{aktenzeichen}/{prefix}-{str(position).zfill(2)}"
                    logger.info("deterministic_match_combined_ref", extra={
                        "combined_ref": combined_ref,
                        "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="combined_reference",
                        routing_id_parsed=inquiry.routing_id,
                        confidence=0.94,
                    )

            # Fall back to bare routing ID (e.g. "SC-A1221-42")
            match = BARE_ROUTING_ID_PATTERN.search(text)
            if match:
                routing_id = match.group(1).upper()
                inquiry = self._lookup_by_routing_id(routing_id)
                if inquiry:
                    logger.info("deterministic_match_subject_ref", extra={
                        "routing_id": routing_id,
                        "inquiry_id": inquiry.id,
                    })
                    return RoutingResult(
                        matched=True,
                        inquiry_id=inquiry.id,
                        inquiry=inquiry,
                        routing_method="subject_reference",
                        routing_id_parsed=routing_id,
                        confidence=0.93,
                    )

        return RoutingResult(matched=False)

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
