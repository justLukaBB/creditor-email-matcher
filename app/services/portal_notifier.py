"""
Portal Webhook Notifier
Sends fire-and-forget HTTP POST to Mandanten Portal after MongoDB write.
Enables status_history tracking and real-time Canvas updates in the portal.
"""

import hmac
import hashlib
import json
import time
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

# Lazy-initialized HTTP client
_http_client = None


def _get_client():
    """Lazy-init httpx client to avoid import-time side effects."""
    global _http_client
    if _http_client is None:
        import httpx
        _http_client = httpx.Client(timeout=10.0)
    return _http_client


def notify_creditor_response(
    email_id: int,
    client_aktenzeichen: Optional[str],
    client_name: Optional[str],
    creditor_name: str,
    creditor_email: str,
    new_debt_amount: Optional[float],
    amount_source: str = "creditor_response",
    extraction_confidence: Optional[float] = None,
    match_status: str = "auto_matched",
    confidence_route: str = "unknown",
    needs_review: bool = False,
    reference_numbers: Optional[List[str]] = None,
) -> None:
    """
    Send webhook notification to Mandanten Portal (fire-and-forget).

    Args:
        email_id: IncomingEmail ID
        client_aktenzeichen: Client case number
        client_name: Client full name
        creditor_name: Creditor name or email
        creditor_email: Creditor email address
        new_debt_amount: Extracted debt amount
        amount_source: Source of the amount
        extraction_confidence: Overall confidence score (0-1)
        match_status: Matching result status
        confidence_route: Confidence routing level
        needs_review: Whether manual review is needed
        reference_numbers: Extracted reference numbers
    """
    from app.config import settings

    if not settings.portal_webhook_url:
        return  # Not configured — skip silently

    payload = {
        "event": "creditor_response_processed",
        "email_id": email_id,
        "client_aktenzeichen": client_aktenzeichen,
        "client_name": client_name,
        "creditor_name": creditor_name,
        "creditor_email": creditor_email,
        "new_debt_amount": new_debt_amount,
        "amount_source": amount_source,
        "extraction_confidence": extraction_confidence,
        "match_status": match_status,
        "confidence_route": confidence_route,
        "needs_review": needs_review,
        "reference_numbers": reference_numbers or [],
        "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    body = json.dumps(payload, default=str)

    headers = {"Content-Type": "application/json"}

    # HMAC-SHA256 signature if secret is configured
    if settings.portal_webhook_secret:
        sig = hmac.new(
            settings.portal_webhook_secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Webhook-Signature"] = sig

    try:
        client = _get_client()
        resp = client.post(settings.portal_webhook_url, content=body, headers=headers)
        logger.info(
            "portal_webhook_sent",
            extra={
                "email_id": email_id,
                "status_code": resp.status_code,
                "creditor": creditor_name,
            },
        )
    except Exception as e:
        # Fire-and-forget — never fail the pipeline for a webhook
        logger.warning(
            "portal_webhook_failed",
            extra={"email_id": email_id, "error": str(e)},
        )
