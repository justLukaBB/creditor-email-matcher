"""
Intent Classification Models
Defines email intent types and classification result structure for Agent 1
"""

from enum import Enum
from pydantic import BaseModel, Field


class EmailIntent(str, Enum):
    """
    Email intent types for creditor response classification.

    Used by Agent 1 to determine processing path for incoming emails.
    """
    debt_statement = "debt_statement"  # Response with debt amount/status
    payment_plan = "payment_plan"  # Payment plan proposal/confirmation
    rejection = "rejection"  # Claim rejection or dispute
    inquiry = "inquiry"  # Question requiring manual response
    auto_reply = "auto_reply"  # Out-of-office, vacation notice
    spam = "spam"  # Marketing, unrelated content


class IntentResult(BaseModel):
    """
    Result of intent classification by Agent 1.

    Determines whether extraction should proceed and which strategy to use.
    """
    intent: EmailIntent
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Classification confidence from 0.0 to 1.0"
    )
    method: str = Field(
        description="Classification method used: 'header_auto_submitted', 'subject_regex', 'noreply_address', 'claude_haiku'"
    )
    skip_extraction: bool = Field(
        default=False,
        description="True for auto_reply and spam intents (skip extraction agents)"
    )

    class Config:
        use_enum_values = True
