"""
Resend API Client

Synchronous client for Resend API operations.
Used by email processor to fetch attachment download URLs.
"""

import httpx
import structlog
from typing import Optional, List, Dict, Any

from app.config import settings

logger = structlog.get_logger(__name__)


def fetch_attachment_download_url(
    resend_email_id: str,
    attachment_id: str,
    timeout: float = 30.0
) -> Optional[str]:
    """
    Fetch attachment download URL from Resend Receiving API (sync).

    API: GET /emails/receiving/{email_id}/attachments/{attachment_id}

    Args:
        resend_email_id: The Resend email ID (from webhook)
        attachment_id: The attachment ID
        timeout: Request timeout in seconds

    Returns:
        The download_url for the attachment, or None if fetch fails.
    """
    if not settings.resend_api_key:
        logger.warning("resend_api_key_not_configured")
        return None

    try:
        with httpx.Client() as client:
            response = client.get(
                f"https://api.resend.com/emails/receiving/{resend_email_id}/attachments/{attachment_id}",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json"
                },
                timeout=timeout
            )

            if response.status_code == 200:
                data = response.json()
                download_url = data.get("download_url")
                logger.info(
                    "resend_attachment_url_fetched",
                    resend_email_id=resend_email_id,
                    attachment_id=attachment_id,
                    filename=data.get("filename"),
                    size=data.get("size"),
                    expires_at=data.get("expires_at")
                )
                return download_url
            else:
                logger.warning(
                    "resend_attachment_fetch_failed",
                    resend_email_id=resend_email_id,
                    attachment_id=attachment_id,
                    status=response.status_code,
                    response=response.text[:500]
                )
                return None

    except Exception as e:
        logger.error(
            "resend_attachment_fetch_error",
            resend_email_id=resend_email_id,
            attachment_id=attachment_id,
            error=str(e)
        )
        return None


def enrich_attachments_with_download_urls(
    resend_email_id: str,
    attachments: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Enrich attachment metadata with download URLs from Resend API.

    Args:
        resend_email_id: The Resend email ID
        attachments: List of attachment dicts with 'id', 'filename', 'content_type'

    Returns:
        List of attachment dicts with 'url' field added (download URL)
    """
    if not attachments:
        return []

    enriched = []
    for att in attachments:
        attachment_id = att.get("id")
        if not attachment_id:
            logger.warning("attachment_missing_id", attachment=att)
            continue

        download_url = fetch_attachment_download_url(resend_email_id, attachment_id)

        if download_url:
            enriched.append({
                **att,
                "url": download_url
            })
            logger.info(
                "attachment_enriched",
                filename=att.get("filename"),
                has_url=True
            )
        else:
            logger.warning(
                "attachment_url_fetch_failed",
                filename=att.get("filename"),
                attachment_id=attachment_id
            )

    return enriched
