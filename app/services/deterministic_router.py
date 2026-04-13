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

    def __init__(self, db: Session):
        self.db = db

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
        body_text: Optional[str] = None,
        body_html: Optional[str] = None,
    ) -> RoutingResult:
        """
        Attempt deterministic routing through all 4 stages.

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

        # Stage 4: From-address DB lookup
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

        inquiry = self.db.query(CreditorInquiry).filter(
            CreditorInquiry.resend_message_id == clean_id
        ).first()

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

    def _stage_from_address(self, from_email: Optional[str]) -> RoutingResult:
        """
        Stage 4: Lookup from_email in creditor_inquiries.

        Only matches if EXACTLY ONE inquiry exists for this sender.
        Ambiguous (multiple inquiries) falls through to LLM pipeline.
        """
        if not from_email:
            return RoutingResult(matched=False)

        inquiries = self.db.query(CreditorInquiry).filter(
            CreditorInquiry.creditor_email == from_email.lower(),
            CreditorInquiry.status == "sent",
        ).order_by(CreditorInquiry.sent_at.desc()).limit(2).all()

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
        """Lookup CreditorInquiry by routing_id."""
        return self.db.query(CreditorInquiry).filter(
            CreditorInquiry.routing_id == routing_id
        ).first()
