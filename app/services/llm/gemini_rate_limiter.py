"""
Vertex AI Rate Limiter and Retry Logic.

Ported from creditor-process-fastapi (the document-processor) so the matcher
reuses the same proven adaptive-throttling behavior. Handles:
- Rate limiting (requests per minute)
- Concurrent request limiting
- 429 error retry with exponential backoff
- Adaptive throttling based on error rate
- Compatible with the google-genai Client
"""
import asyncio
import time
import logging
import random
from typing import Any, Callable, Optional, List
from collections import deque
from dataclasses import dataclass, field

# Import google-genai exceptions
try:
    from google.genai.errors import APIError, ClientError, ServerError
    GOOGLE_EXCEPTIONS_AVAILABLE = True
except ImportError:
    GOOGLE_EXCEPTIONS_AVAILABLE = False

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RateLimiterMetrics:
    """Metrics for monitoring rate limiter performance."""
    total_requests: int = 0
    total_429_errors: int = 0
    total_retries: int = 0
    successful_retries: int = 0
    average_retry_delay: float = 0.0
    last_error_time: Optional[float] = None
    window_start: float = field(default_factory=time.time)

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_429_errors / self.total_requests


class GeminiRateLimiter:
    """
    Rate limiter for Gemini API requests.

    Features:
    - Token bucket algorithm for rate limiting
    - Semaphore for concurrent request limiting
    - Minimum interval between requests (to avoid burst limits)
    - Adaptive throttling on 429 errors
    """

    def __init__(self):
        self.enabled = settings.gemini_enable_rate_limiting
        self.rpm_limit = settings.gemini_requests_per_minute
        self.max_concurrent = settings.gemini_max_concurrent_requests
        self.adaptive_throttling = settings.gemini_adaptive_throttling
        self.min_interval = getattr(settings, 'gemini_min_request_interval_seconds', 2.0)

        # Request timestamps for RPM tracking
        self._requests: deque = deque()

        # Semaphore for concurrent requests
        self._semaphore: Optional[asyncio.Semaphore] = None

        # Last request timestamp (for minimum interval enforcement)
        self._last_request_time: float = 0

        # Metrics
        self.metrics = RateLimiterMetrics()

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        logger.info(
            f"GeminiRateLimiter initialized: enabled={self.enabled}, "
            f"rpm_limit={self.rpm_limit}, max_concurrent={self.max_concurrent}, "
            f"min_interval={self.min_interval}s"
        )

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Get or create the semaphore (must be created in async context)."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    async def acquire(self) -> None:
        """
        Acquire a request slot.
        Waits if rate limit, concurrent limit, or minimum interval not met.
        """
        if not self.enabled:
            return

        # Acquire semaphore for concurrent limit
        semaphore = self._get_semaphore()
        await semaphore.acquire()

        async with self._lock:
            now = time.time()

            # Enforce minimum interval between requests (to avoid burst limits)
            time_since_last = now - self._last_request_time
            if time_since_last < self.min_interval:
                wait_time = self.min_interval - time_since_last
                logger.info(f"Enforcing minimum interval - waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
                now = time.time()

            # Remove requests older than 60 seconds
            while self._requests and self._requests[0] < now - 60:
                self._requests.popleft()

            # Wait if RPM limit reached
            if len(self._requests) >= self.rpm_limit:
                oldest = self._requests[0]
                wait_time = 60 - (now - oldest) + 0.1  # Add 100ms buffer

                if wait_time > 0:
                    logger.warning(
                        f"Rate limit reached ({len(self._requests)}/{self.rpm_limit} RPM) - "
                        f"waiting {wait_time:.1f}s"
                    )
                    await asyncio.sleep(wait_time)

                    # Clean up again after waiting
                    now = time.time()
                    while self._requests and self._requests[0] < now - 60:
                        self._requests.popleft()

            # Record this request
            self._last_request_time = time.time()
            self._requests.append(self._last_request_time)

    def release(self) -> None:
        """Release a request slot."""
        if not self.enabled:
            return

        if self._semaphore is not None:
            self._semaphore.release()

    def record_success(self) -> None:
        """Record a successful request."""
        self.metrics.total_requests += 1

        # Slowly increase limits after successful requests (adaptive throttling)
        if self.adaptive_throttling and self.metrics.total_requests % 10 == 0:
            self._increase_limits()

    def record_429_error(self) -> None:
        """Record a 429 rate limit error."""
        self.metrics.total_429_errors += 1
        self.metrics.last_error_time = time.time()

        # Reduce limits on 429 error (adaptive throttling)
        if self.adaptive_throttling:
            self._reduce_limits()

    def _reduce_limits(self) -> None:
        """Reduce rate limits after 429 error."""
        old_rpm = self.rpm_limit
        old_concurrent = self.max_concurrent

        self.rpm_limit = max(1, int(self.rpm_limit * 0.7))
        self.max_concurrent = max(1, self.max_concurrent - 1)

        # Recreate semaphore with new limit
        self._semaphore = None

        logger.warning(
            f"ADAPTIVE THROTTLING: Reducing limits - "
            f"RPM: {old_rpm} -> {self.rpm_limit}, "
            f"Concurrent: {old_concurrent} -> {self.max_concurrent}"
        )

    def _increase_limits(self) -> None:
        """Gradually increase rate limits after successful requests."""
        target_rpm = settings.gemini_requests_per_minute
        target_concurrent = settings.gemini_max_concurrent_requests

        if self.rpm_limit < target_rpm:
            self.rpm_limit = min(target_rpm, self.rpm_limit + 1)

        if self.max_concurrent < target_concurrent:
            old_concurrent = self.max_concurrent
            self.max_concurrent = min(target_concurrent, self.max_concurrent + 1)
            if old_concurrent != self.max_concurrent:
                self._semaphore = None  # Recreate semaphore

    def get_state(self) -> dict:
        """Get current rate limiter state for monitoring."""
        return {
            "enabled": self.enabled,
            "rpm_limit": self.rpm_limit,
            "max_concurrent": self.max_concurrent,
            "min_interval_seconds": self.min_interval,
            "requests_in_last_minute": len(self._requests),
            "adaptive_throttling": self.adaptive_throttling,
            "metrics": {
                "total_requests": self.metrics.total_requests,
                "total_429_errors": self.metrics.total_429_errors,
                "error_rate": f"{self.metrics.error_rate:.2%}",
                "total_retries": self.metrics.total_retries,
                "successful_retries": self.metrics.successful_retries,
            }
        }

    def reset(self) -> None:
        """Reset rate limiter to default configuration."""
        self.rpm_limit = settings.gemini_requests_per_minute
        self.max_concurrent = settings.gemini_max_concurrent_requests
        self.min_interval = getattr(settings, 'gemini_min_request_interval_seconds', 2.0)
        self._requests.clear()
        self._semaphore = None
        self._last_request_time = 0
        self.metrics = RateLimiterMetrics()
        logger.info("Rate limiter reset to defaults")


# Global rate limiter instance
_rate_limiter: Optional[GeminiRateLimiter] = None


def get_rate_limiter() -> GeminiRateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = GeminiRateLimiter()
    return _rate_limiter


def calculate_retry_delay(attempt: int, retry_after: Optional[float] = None) -> float:
    """
    Calculate retry delay with exponential backoff.

    Args:
        attempt: Current retry attempt (0-based)
        retry_after: Optional Retry-After header value in seconds

    Returns:
        Delay in seconds
    """
    base_delay = settings.gemini_429_base_delay_seconds
    max_delay = settings.gemini_429_max_delay_seconds
    multiplier = settings.gemini_429_retry_multiplier

    # Use Retry-After if provided
    if retry_after is not None and retry_after > 0:
        delay = retry_after * 1.2  # Add 20% safety margin
        return min(delay, max_delay)

    # Exponential backoff: 2s, 4s, 8s, 16s, 32s...
    delay = base_delay * (multiplier ** attempt)

    # Add jitter (+-10%) to prevent thundering herd
    jitter = delay * 0.1 * (random.random() * 2 - 1)
    delay = delay + jitter

    return min(delay, max_delay)


def is_rate_limit_error(error: Exception) -> bool:
    """Check if an error is a rate limit (429) error."""
    # google-genai exceptions
    if GOOGLE_EXCEPTIONS_AVAILABLE:
        if isinstance(error, ClientError) and getattr(error, 'code', None) == 429:
            return True

    # Check error message for rate limit indicators
    error_str = str(error).lower()
    if "429" in error_str or "resource exhausted" in error_str or "rate limit" in error_str:
        return True
    if "quota" in error_str and "exceeded" in error_str:
        return True

    return False


def is_retryable_error(error: Exception) -> bool:
    """Check if an error is retryable (429, 503, etc.)."""
    # Rate limit errors
    if is_rate_limit_error(error):
        return True

    # google-genai exceptions
    if GOOGLE_EXCEPTIONS_AVAILABLE:
        if isinstance(error, ServerError) and getattr(error, 'code', None) in (500, 502, 503, 504):
            return True

    # Check for other transient errors
    error_str = str(error).lower()
    if any(code in error_str for code in ["503", "502", "504", "500", "unavailable", "timeout"]):
        return True

    return False


def generate_content_with_retry_sync(
    client,
    model_name: str,
    content: List[Any],
    max_retries: Optional[int] = None,
    operation_name: str = "generate_content",
    config: Optional[Any] = None
) -> Any:
    """
    Synchronous google-genai generate_content with rate limiting + 429 retry.

    Args:
        client: google.genai.Client instance
        model_name: Model name string (e.g. "gemini-2.5-pro")
        content: Content list to send to the model
        max_retries: Max retry attempts (defaults to config setting)
        operation_name: Name of the operation for logging
        config: Optional google.genai.types.GenerateContentConfig

    Returns:
        Model response
    """
    if max_retries is None:
        max_retries = settings.gemini_429_max_retries

    rate_limiter = get_rate_limiter()
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            # Simple blocking check for rate limit (no async acquire)
            if rate_limiter.enabled:
                now = time.time()

                # Enforce minimum interval between requests (to avoid burst limits)
                time_since_last = now - rate_limiter._last_request_time
                if time_since_last < rate_limiter.min_interval:
                    wait_time = rate_limiter.min_interval - time_since_last
                    logger.info(f"Enforcing minimum interval - waiting {wait_time:.1f}s")
                    time.sleep(wait_time)
                    now = time.time()

                # Clean old requests
                while rate_limiter._requests and rate_limiter._requests[0] < now - 60:
                    rate_limiter._requests.popleft()

                # Wait if RPM limit reached
                if len(rate_limiter._requests) >= rate_limiter.rpm_limit:
                    oldest = rate_limiter._requests[0]
                    wait_time = 60 - (now - oldest) + 0.1
                    if wait_time > 0:
                        logger.warning(
                            f"Rate limit reached ({len(rate_limiter._requests)}/{rate_limiter.rpm_limit} RPM) - "
                            f"waiting {wait_time:.1f}s"
                        )
                        time.sleep(wait_time)

                rate_limiter._last_request_time = time.time()
                rate_limiter._requests.append(rate_limiter._last_request_time)

            # Make the API call
            response = client.models.generate_content(
                model=model_name, contents=content, config=config
            )

            # Record success
            rate_limiter.record_success()

            if attempt > 0:
                rate_limiter.metrics.successful_retries += 1
                logger.info(f"{operation_name}: Succeeded after {attempt} retries")

            return response

        except Exception as e:
            last_error = e

            if not is_retryable_error(e):
                logger.error(f"{operation_name}: Non-retryable error: {e}")
                raise

            if attempt >= max_retries:
                logger.error(
                    f"{operation_name}: Max retries ({max_retries}) exceeded. "
                    f"Last error: {e}"
                )
                raise

            if is_rate_limit_error(e):
                rate_limiter.record_429_error()

            rate_limiter.metrics.total_retries += 1

            delay = calculate_retry_delay(attempt)

            metrics = rate_limiter.metrics
            metrics.average_retry_delay = (
                (metrics.average_retry_delay * (metrics.total_retries - 1) + delay) /
                metrics.total_retries
            )

            logger.warning(
                f"429/transient error - {operation_name} - "
                f"attempt {attempt + 1}/{max_retries + 1}, waiting {delay:.1f}s. Error: {e}"
            )

            time.sleep(delay)
            logger.info(f"{operation_name}: Retrying after transient error...")

    if last_error:
        raise last_error
    raise RuntimeError(f"{operation_name}: Unexpected error in retry loop")
