"""
Circuit Breaker Implementation for External Service Dependencies

Protects against cascading failures by opening circuits after consecutive failures
and automatically attempting recovery after a timeout period.

Services protected:
- Claude API (LLM calls)
- MongoDB (document database)
- Google Cloud Storage (attachment storage)
"""

import functools
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import pybreaker

from app.config import settings

logger = logging.getLogger(__name__)


class CircuitBreakerEmailListener(pybreaker.CircuitBreakerListener):
    """
    Email notification listener for circuit breaker state changes.

    Sends alert emails when a circuit breaker opens, indicating that
    an external service is experiencing failures and has been isolated.
    """

    def __init__(self, admin_email: str):
        """
        Initialize the email listener.

        Args:
            admin_email: Email address to receive circuit breaker alerts
        """
        self.admin_email = admin_email

    def state_change(self, cb: pybreaker.CircuitBreaker, old_state: pybreaker.CircuitBreakerState, new_state: pybreaker.CircuitBreakerState):
        """
        Handle circuit breaker state changes.

        Args:
            cb: The circuit breaker instance
            old_state: Previous state
            new_state: New state
        """
        logger.warning(
            f"Circuit breaker state change: {cb.name} transitioned from {old_state.name} to {new_state.name}",
            extra={
                "circuit_breaker": cb.name,
                "old_state": old_state.name,
                "new_state": new_state.name,
                "fail_count": cb.fail_counter
            }
        )

        # Send email alert when circuit opens
        if new_state == pybreaker.STATE_OPEN:
            self._send_alert_email(cb)

    def _send_alert_email(self, cb: pybreaker.CircuitBreaker):
        """
        Send email alert for opened circuit breaker.

        Args:
            cb: The circuit breaker that opened
        """
        try:
            # Skip email if SMTP not configured
            if not settings.smtp_host or not self.admin_email:
                logger.warning(
                    f"Cannot send circuit breaker alert: SMTP not configured (circuit: {cb.name})"
                )
                return

            # Compose email
            subject = f"ALERT: Circuit Breaker Opened - {cb.name}"
            body = f"""
CIRCUIT BREAKER ALERT

Service: {cb.name}
Status: OPEN (service is now isolated)
Failure Count: {cb.fail_counter}
Reset Timeout: {cb.reset_timeout} seconds

The circuit breaker has opened after {cb.fail_counter} consecutive failures.
Requests to this service will be blocked until the circuit automatically
attempts recovery after {cb.reset_timeout} seconds.

ACTION REQUIRED:
1. Investigate the root cause of failures for {cb.name}
2. Check service health and availability
3. Review application logs for error details
4. Monitor for automatic recovery or take manual action

The circuit breaker will automatically attempt to close after the timeout period.
If failures continue, the circuit will open again.

Environment: {settings.environment}
            """.strip()

            msg = MIMEMultipart()
            msg["From"] = settings.smtp_username or settings.admin_email
            msg["To"] = self.admin_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            # Send via SMTP
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                if settings.smtp_username and settings.smtp_password:
                    server.starttls()
                    server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(msg)

            logger.info(
                f"Circuit breaker alert email sent to {self.admin_email}",
                extra={"circuit_breaker": cb.name, "recipient": self.admin_email}
            )

        except Exception as e:
            # Don't crash on email failure - log and continue
            logger.error(
                f"Failed to send circuit breaker alert email: {e}",
                extra={"circuit_breaker": cb.name, "error": str(e)},
                exc_info=True
            )


def _create_breaker(name: str, listener: CircuitBreakerEmailListener) -> pybreaker.CircuitBreaker:
    """
    Create a circuit breaker with configured thresholds.

    Args:
        name: Service name for the circuit breaker
        listener: Email notification listener

    Returns:
        Configured CircuitBreaker instance
    """
    return pybreaker.CircuitBreaker(
        name=name,
        fail_max=settings.circuit_breaker_fail_max,
        reset_timeout=settings.circuit_breaker_reset_timeout,
        listeners=[listener]
    )


# Module-level instances (lazy initialization)
_email_listener: Optional[CircuitBreakerEmailListener] = None
_claude_breaker: Optional[pybreaker.CircuitBreaker] = None
_mongodb_breaker: Optional[pybreaker.CircuitBreaker] = None
_gcs_breaker: Optional[pybreaker.CircuitBreaker] = None


def get_breaker(service_name: str) -> pybreaker.CircuitBreaker:
    """
    Get circuit breaker for a specific service.

    Lazy initializes breakers on first access to avoid import-time side effects.

    Args:
        service_name: Service name ("claude", "mongodb", or "gcs")

    Returns:
        Circuit breaker instance for the service

    Raises:
        ValueError: If service_name is not recognized
    """
    global _email_listener, _claude_breaker, _mongodb_breaker, _gcs_breaker

    # Lazy initialize email listener
    if _email_listener is None:
        admin_email = settings.circuit_breaker_alert_email or settings.admin_email
        if admin_email:
            _email_listener = CircuitBreakerEmailListener(admin_email)
        else:
            # Create dummy listener if no email configured
            _email_listener = CircuitBreakerEmailListener("noreply@example.com")
            logger.warning("Circuit breaker email alerts disabled: no admin email configured")

    # Lazy initialize breakers
    if service_name == "claude":
        if _claude_breaker is None:
            _claude_breaker = _create_breaker("claude_api", _email_listener)
            logger.info("Initialized Claude API circuit breaker")
        return _claude_breaker

    elif service_name == "mongodb":
        if _mongodb_breaker is None:
            _mongodb_breaker = _create_breaker("mongodb", _email_listener)
            logger.info("Initialized MongoDB circuit breaker")
        return _mongodb_breaker

    elif service_name == "gcs":
        if _gcs_breaker is None:
            _gcs_breaker = _create_breaker("google_cloud_storage", _email_listener)
            logger.info("Initialized GCS circuit breaker")
        return _gcs_breaker

    else:
        raise ValueError(f"Unknown service name: {service_name}. Must be 'claude', 'mongodb', or 'gcs'")


def get_claude_breaker() -> pybreaker.CircuitBreaker:
    """
    Get circuit breaker for Claude API.

    Returns:
        Circuit breaker instance for Claude API
    """
    return get_breaker("claude")


def get_mongodb_breaker() -> pybreaker.CircuitBreaker:
    """
    Get circuit breaker for MongoDB.

    Returns:
        Circuit breaker instance for MongoDB
    """
    return get_breaker("mongodb")


def get_gcs_breaker() -> pybreaker.CircuitBreaker:
    """
    Get circuit breaker for Google Cloud Storage.

    Returns:
        Circuit breaker instance for GCS
    """
    return get_breaker("gcs")


def with_circuit_breaker(service_name: str):
    """
    Decorator to wrap function with circuit breaker protection.

    Usage:
        @with_circuit_breaker("claude")
        def call_claude_api(payload):
            # API call here
            pass

    Args:
        service_name: Service name ("claude", "mongodb", or "gcs")

    Returns:
        Decorator function

    Raises:
        CircuitBreakerError: If circuit is open (service unavailable)
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            breaker = get_breaker(service_name)
            return breaker.call(func, *args, **kwargs)
        return wrapper
    return decorator


# Re-export exception for caller handling
from pybreaker import CircuitBreakerError

__all__ = [
    "CircuitBreakerEmailListener",
    "get_claude_breaker",
    "get_mongodb_breaker",
    "get_gcs_breaker",
    "with_circuit_breaker",
    "CircuitBreakerError",
]
