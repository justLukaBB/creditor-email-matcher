"""
Pydantic schemas for Zendesk webhook payloads
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ZendeskWebhookEmail(BaseModel):
    """
    Zendesk webhook payload for new ticket/email
    Flexible schema that accepts various formats
    """
    ticket_id: str = Field(..., description="Zendesk ticket ID")
    subject: Optional[str] = Field(None, description="Email subject")
    from_email: str = Field(..., description="Sender email address")
    from_name: Optional[str] = Field(None, description="Sender name")
    body_html: Optional[str] = Field(None, description="HTML email body")
    body_text: Optional[str] = Field(None, description="Plain text email body")
    received_at: Optional[str] = Field(None, description="When email was received (any format)")
    webhook_id: Optional[str] = Field(None, description="Unique webhook ID for deduplication")
    attachments: Optional[List[dict]] = Field(
        default=None,
        description="List of attachment metadata from Zendesk. Each dict has: url, filename, content_type, size"
    )

    class Config:
        # Allow extra fields that we don't use
        extra = "allow"


class WebhookResponse(BaseModel):
    """
    Response returned from webhook endpoint
    """
    status: str
    message: str
    email_id: Optional[int] = None
    match_status: Optional[str] = None
    confidence: Optional[float] = None
