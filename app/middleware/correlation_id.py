"""
Correlation ID Middleware
Provides automatic correlation ID injection for request tracing across async pipeline
"""

from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id

# Re-export CorrelationIdMiddleware for convenience
__all__ = ["CorrelationIdMiddleware", "get_correlation_id"]


def get_correlation_id() -> str:
    """
    Get current correlation ID from async context.

    Returns the correlation ID for the current request/task context.
    Used by Dramatiq actors to manually propagate correlation ID
    when async context is lost during background task execution.

    Returns:
        str: The correlation ID or 'none' if not available
    """
    return correlation_id.get() or 'none'
