"""
Portal Webhook Notifier
Sends fire-and-forget HTTP POST to Mandanten Portal after MongoDB write.

Phase 5.1: Payload now carries full archive context (headers, body_html,
           body_text, separate cc/bcc, content_size_bytes, raw_eml_gcs_path).
Phase 5.3: Signs each request with X-Matcher-Signature + X-Matcher-Timestamp
           using MATCHER_PORTAL_HMAC_SECRET (shared with the portal verifier).
           Legacy X-Webhook-Signature header is still added for backward
           compat during the cutover window.
Phase 5.4: notify_portal_bounce() informs the portal of matcher-classified
           bounces so OutboundEmail.delivery_status='bounced' + cascade fires.
Phase 5.6: upload_raw_eml_to_gcs() mirrors raw MIME into the archive bucket
           under raw-emails/{kanzlei_id}/{resend_email_id}.eml. Failure-tolerant.
"""

import hmac
import hashlib
import json
import time
import logging
from typing import Optional, List, Any, Dict

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


def _build_headers(body: str, *, settings) -> Dict[str, str]:
    """
    Construct the request headers, including HMAC signatures.

    Two signature flavours coexist during the Phase-5.3 cutover:

    - Legacy X-Webhook-Signature: hex(HMAC(portal_webhook_secret, body)).
      Older portal builds verified this. Kept here so an old portal pinned
      to the legacy secret keeps working until the cutover is fully through.

    - New X-Matcher-Signature + X-Matcher-Timestamp: hex(HMAC(matcher_portal_hmac_secret,
      timestamp + body)). Matches the portal's webhookVerifier factory mount
      (server.js, Phase 4.2b). The portal rejects requests > 5 min old.
    """
    headers = {"Content-Type": "application/json"}

    legacy_secret = getattr(settings, "portal_webhook_secret", None)
    if legacy_secret:
        sig = hmac.new(legacy_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = sig

    matcher_secret = getattr(settings, "matcher_portal_hmac_secret", None)
    if matcher_secret:
        timestamp_ms = str(int(time.time() * 1000))
        signed = (timestamp_ms + body).encode()
        sig = hmac.new(matcher_secret.encode(), signed, hashlib.sha256).hexdigest()
        headers["X-Matcher-Signature"] = sig
        headers["X-Matcher-Timestamp"] = timestamp_ms

    return headers


def upload_raw_eml_to_gcs(
    *,
    raw_eml_bytes: Optional[bytes],
    kanzlei_id: Optional[str],
    resend_email_id: Optional[str],
) -> Optional[str]:
    """
    Mirror the raw MIME bytes of an inbound email into the archive bucket.

    Returns the resulting gs:// path, or None if the mirror is disabled,
    the inputs are insufficient, or the upload fails. Always failure-tolerant:
    the function logs and returns None instead of raising, so the webhook
    pipeline never blocks on storage issues.
    """
    from app.config import settings

    if not getattr(settings, "raw_eml_mirror_enabled", True):
        return None
    if not raw_eml_bytes:
        return None
    if not resend_email_id:
        logger.info("raw_eml_skipped_no_email_id")
        return None
    if not settings.gcs_bucket_name:
        logger.info("raw_eml_skipped_no_bucket")
        return None

    prefix = (getattr(settings, "gcs_raw_emails_prefix", None) or "raw-emails").strip("/")
    tenant = kanzlei_id.strip() if isinstance(kanzlei_id, str) and kanzlei_id.strip() else "_unassigned"
    # Defensive: tenant segments must never break the object-path structure.
    tenant = tenant.replace("/", "_")
    blob_path = f"{prefix}/{tenant}/{resend_email_id}.eml"

    # Upload via the shared GCS handler. Wrapped because GCS errors are
    # forensic-only — we never fail the surrounding flow.
    try:
        import tempfile
        from app.services.storage.gcs_client import GCSAttachmentHandler

        handler = GCSAttachmentHandler()
        with tempfile.NamedTemporaryFile(prefix="raw_eml_", suffix=".eml", delete=True) as tmp:
            tmp.write(raw_eml_bytes)
            tmp.flush()
            gs_url = handler.upload_file(
                local_path=tmp.name,
                dest_blob_path=blob_path,
                content_type="message/rfc822",
            )
        logger.info("raw_eml_uploaded", extra={"gs_url": gs_url, "kanzlei_id": kanzlei_id})
        return gs_url
    except Exception as e:
        logger.warning(
            "raw_eml_upload_failed",
            extra={"resend_email_id": resend_email_id, "kanzlei_id": kanzlei_id, "error": str(e)},
        )
        return None


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
    email_subject: Optional[str] = None,
    email_body_preview: Optional[str] = None,
    intent: Optional[str] = None,
    attachment_urls: Optional[List[dict]] = None,
    resend_email_id: Optional[str] = None,
    routing_method: Optional[str] = None,
    routing_id: Optional[str] = None,
    deterministic_match: bool = False,
    kanzlei_id: Optional[str] = None,
    # Phase 5.1 — full-archive payload extension.
    headers: Optional[Dict[str, Any]] = None,
    body_html: Optional[str] = None,
    body_text: Optional[str] = None,
    cc_addresses: Optional[List[str]] = None,
    bcc_addresses: Optional[List[str]] = None,
    content_size_bytes: Optional[int] = None,
    # Phase 5.6 — raw-EML mirror result (caller can pass either the bytes
    # for upload, or a precomputed gs:// path).
    raw_eml_bytes: Optional[bytes] = None,
    raw_eml_gcs_path: Optional[str] = None,
) -> None:
    """
    Send webhook notification to Mandanten Portal (fire-and-forget).
    """
    from app.config import settings

    logger.info("portal_webhook_check", extra={"url_configured": bool(settings.portal_webhook_url)})
    if not settings.portal_webhook_url:
        return  # Not configured — skip silently

    # Phase 5.6: opportunistically mirror raw EML before the notify. Caller
    # may either supply bytes (we upload), a precomputed gs:// path (we keep),
    # or nothing (field stays null and the portal renders the
    # AttachmentUnavailableBanner for any orphan attachments).
    if not raw_eml_gcs_path and raw_eml_bytes is not None:
        raw_eml_gcs_path = upload_raw_eml_to_gcs(
            raw_eml_bytes=raw_eml_bytes,
            kanzlei_id=kanzlei_id,
            resend_email_id=resend_email_id,
        )

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
        "email_subject": email_subject,
        "email_body_preview": (email_body_preview or "")[:500] or None,
        # email_body_full is the legacy body-text alias. Phase 5.1 adds the
        # explicit body_text/body_html split, but we keep email_body_full
        # populated so older portal builds continue to render the body.
        "email_body_full": body_text or email_body_preview or None,
        "intent": intent,
        "attachments": attachment_urls or [],
        "resend_email_id": resend_email_id,
        "routing_method": routing_method,
        "routing_id": routing_id,
        "deterministic_match": deterministic_match,
        "kanzlei_id": kanzlei_id,
        "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Phase 5.1 additive fields.
        "headers": headers,
        "body_html": body_html,
        "body_text": body_text,
        "cc_addresses": cc_addresses,
        "bcc_addresses": bcc_addresses,
        "content_size_bytes": content_size_bytes,
        # Phase 5.6.
        "raw_eml_gcs_path": raw_eml_gcs_path,
    }

    body = json.dumps(payload, default=str)
    headers_out = _build_headers(body, settings=settings)

    try:
        client = _get_client()
        resp = client.post(settings.portal_webhook_url, content=body, headers=headers_out)
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


def notify_settlement_response(
    email_id: int,
    client_aktenzeichen: Optional[str],
    client_name: Optional[str],
    creditor_name: str,
    creditor_email: str,
    settlement_decision: str,
    counter_offer_amount: Optional[float] = None,
    conditions: Optional[str] = None,
    confidence: Optional[float] = None,
    match_status: str = "auto_matched",
    needs_review: bool = False,
    email_subject: Optional[str] = None,
    email_body_preview: Optional[str] = None,
    attachment_urls: Optional[List[dict]] = None,
    resend_email_id: Optional[str] = None,
    # Phase 4 deterministic routing fields (must be forwarded so the portal
    # ties this audit row to the original outbound).
    routing_method: Optional[str] = None,
    routing_id: Optional[str] = None,
    deterministic_match: bool = False,
    kanzlei_id: Optional[str] = None,
    # Phase 5.1 — full-archive payload extension.
    headers: Optional[Dict[str, Any]] = None,
    body_html: Optional[str] = None,
    body_text: Optional[str] = None,
    cc_addresses: Optional[List[str]] = None,
    bcc_addresses: Optional[List[str]] = None,
    content_size_bytes: Optional[int] = None,
    # Phase 5.6.
    raw_eml_bytes: Optional[bytes] = None,
    raw_eml_gcs_path: Optional[str] = None,
) -> None:
    """Send settlement response webhook to Mandanten Portal (fire-and-forget)."""
    from app.config import settings

    if not settings.portal_webhook_url:
        return

    # Settlement uses a separate endpoint — derive from base URL
    settlement_url = settings.portal_webhook_url.replace(
        "/matcher-response", "/settlement-response"
    )

    if not raw_eml_gcs_path and raw_eml_bytes is not None:
        raw_eml_gcs_path = upload_raw_eml_to_gcs(
            raw_eml_bytes=raw_eml_bytes,
            kanzlei_id=kanzlei_id,
            resend_email_id=resend_email_id,
        )

    payload = {
        "event": "settlement_response_processed",
        "email_id": email_id,
        "client_aktenzeichen": client_aktenzeichen,
        "client_name": client_name,
        "creditor_name": creditor_name,
        "creditor_email": creditor_email,
        "settlement_decision": settlement_decision,
        "counter_offer_amount": counter_offer_amount,
        "conditions": conditions,
        "extraction_confidence": confidence,
        "match_status": match_status,
        "needs_review": needs_review,
        "email_subject": email_subject,
        "email_body_preview": (email_body_preview or "")[:500] or None,
        "email_body_full": body_text or email_body_preview or None,
        "attachments": attachment_urls or [],
        "resend_email_id": resend_email_id,
        "routing_method": routing_method,
        "routing_id": routing_id,
        "deterministic_match": deterministic_match,
        "kanzlei_id": kanzlei_id,
        "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Phase 5.1 additive fields.
        "headers": headers,
        "body_html": body_html,
        "body_text": body_text,
        "cc_addresses": cc_addresses,
        "bcc_addresses": bcc_addresses,
        "content_size_bytes": content_size_bytes,
        "raw_eml_gcs_path": raw_eml_gcs_path,
    }

    body = json.dumps(payload, default=str)
    headers_out = _build_headers(body, settings=settings)

    try:
        client = _get_client()
        resp = client.post(settlement_url, content=body, headers=headers_out)
        logger.info(
            "portal_settlement_webhook_sent",
            extra={"email_id": email_id, "status_code": resp.status_code, "decision": settlement_decision},
        )
    except Exception as e:
        logger.warning(
            "portal_settlement_webhook_failed",
            extra={"email_id": email_id, "error": str(e)},
        )


def notify_portal_bounce(
    *,
    resend_email_id: str,
    bounce_type: str,  # 'hard' | 'soft' | 'unknown_bounce'
    bounce_reason: Optional[str] = None,
    bounced_at: Optional[str] = None,  # ISO timestamp; defaults to now
    routing_id: Optional[str] = None,
    kanzlei_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> None:
    """
    Phase 5.4 — fire-and-forget notification that an outbound mail bounced.

    The portal handler (`/api/webhooks/matcher-bounce`) is HMAC-protected and
    expects the same envelope as the resend-status route: it looks up
    OutboundEmail by `resend_email_id`, marks it bounced, and cascades to the
    client/creditor side. Idempotency is keyed off `event_id`.

    `bounce_type` is normalised to {'hard', 'soft'} on the portal side; any
    classification the matcher reports as 'unknown_bounce' will be treated as
    'soft' (no cascade).
    """
    from app.config import settings

    base_url = settings.portal_webhook_url
    if not base_url:
        logger.info("matcher_bounce_skipped_no_portal_url")
        return

    bounce_url = getattr(settings, "portal_bounce_webhook_url", None) or base_url.replace(
        "/matcher-response", "/matcher-bounce"
    )

    payload = {
        "resend_email_id": resend_email_id,
        "bounce_type": bounce_type,
        "bounce_reason": bounce_reason,
        "bounced_at": bounced_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "routing_id": routing_id,
        "kanzlei_id": kanzlei_id,
        "event_id": event_id or f"matcher-bounce:{resend_email_id}",
    }
    body = json.dumps(payload, default=str)
    headers_out = _build_headers(body, settings=settings)

    try:
        client = _get_client()
        resp = client.post(bounce_url, content=body, headers=headers_out)
        logger.info(
            "matcher_bounce_webhook_sent",
            extra={
                "resend_email_id": resend_email_id,
                "status_code": resp.status_code,
                "bounce_type": bounce_type,
            },
        )
    except Exception as e:
        logger.warning(
            "matcher_bounce_webhook_failed",
            extra={"resend_email_id": resend_email_id, "error": str(e)},
        )
