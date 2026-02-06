"""
Sentry Error Tracking
Provides error tracking with rich context for production debugging
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    """
    Initialize Sentry SDK with FastAPI integration.

    If SENTRY_DSN is not configured, logs warning and returns (disabled).
    This allows graceful degradation in development environments.
    """
    from app.config import settings

    if settings.sentry_dsn is None:
        logger.warning("Sentry DSN not configured - error tracking disabled")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment or settings.environment,
            traces_sample_rate=0.1,  # 10% of requests traced
            profiles_sample_rate=0.1,  # 10% of requests profiled
            integrations=[
                FastApiIntegration(),
            ],
        )

        logger.info(
            "Sentry initialized",
            extra={
                "environment": settings.sentry_environment or settings.environment,
                "traces_sample_rate": 0.1,
            }
        )
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}")


def set_processing_context(
    email_id: int,
    actor: str,
    correlation_id: Optional[str] = None
) -> None:
    """
    Set Sentry context for current email processing.

    This enriches error reports with:
    - email_id: Which email was being processed
    - actor: Which processing actor/stage was active
    - correlation_id: Request tracking across services

    Args:
        email_id: ID of the email being processed
        actor: Name of the actor/stage (e.g., "email_processor", "content_extractor")
        correlation_id: Optional correlation ID for request tracking
    """
    try:
        import sentry_sdk

        sentry_sdk.set_context("processing", {
            "email_id": email_id,
            "actor": actor,
            "correlation_id": correlation_id or "none"
        })
        sentry_sdk.set_tag("email_id", str(email_id))
        sentry_sdk.set_tag("actor", actor)

        if correlation_id:
            sentry_sdk.set_tag("correlation_id", correlation_id)
    except ImportError:
        # Sentry not installed or disabled
        pass


def add_breadcrumb(
    category: str,
    message: str,
    level: str = "info",
    data: Optional[dict] = None
) -> None:
    """
    Add breadcrumb to Sentry for request/processing trail.

    Breadcrumbs help understand what happened before an error occurred.

    Args:
        category: Breadcrumb category (e.g., "extraction", "matching", "validation")
        message: Human-readable message
        level: Severity level ("debug", "info", "warning", "error")
        data: Additional structured data
    """
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            category=category,
            message=message,
            level=level,
            data=data or {}
        )
    except ImportError:
        # Sentry not installed or disabled
        pass


def capture_message(message: str, level: str = "info") -> None:
    """
    Capture non-exception message in Sentry.

    Useful for tracking important events that aren't errors.

    Args:
        message: Message to capture
        level: Severity level ("debug", "info", "warning", "error", "fatal")
    """
    try:
        import sentry_sdk

        sentry_sdk.capture_message(message, level=level)
    except ImportError:
        # Sentry not installed or disabled
        pass
