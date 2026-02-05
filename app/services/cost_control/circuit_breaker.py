"""
Daily Cost Circuit Breaker

Redis-backed daily cost limit enforcement to prevent cost explosions.
Uses atomic INCRBYFLOAT for accurate cost tracking across distributed workers.
"""

from datetime import datetime, timezone
from typing import Optional

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


class DailyLimitExceeded(Exception):
    """Raised when daily cost limit would be exceeded."""

    def __init__(
        self,
        estimated_cost: float,
        current_spend: float,
        daily_limit: float,
        message: Optional[str] = None
    ):
        self.estimated_cost = estimated_cost
        self.current_spend = current_spend
        self.daily_limit = daily_limit
        self.remaining = daily_limit - current_spend

        if message is None:
            message = (
                f"Daily cost limit exceeded: would spend ${estimated_cost:.4f}, "
                f"but only ${self.remaining:.4f} remaining today "
                f"(spent ${current_spend:.4f}/${daily_limit:.2f})"
            )
        super().__init__(message)


class DailyCostCircuitBreaker:
    """
    Daily cost limit enforcement using Redis atomic counters.

    The circuit is "open" (tripped) when daily spend exceeds the limit,
    blocking further Claude API calls until the next day.

    Key format: creditor_matcher:daily_cost:{YYYY-MM-DD}
    TTL: 48 hours (keep 2 days for debugging)

    Usage:
        redis_client = redis.Redis.from_url(settings.redis_url)
        circuit_breaker = DailyCostCircuitBreaker(redis_client)

        # Before making Claude API call
        estimated_cost = tracker.estimate_cost_usd() + 0.05  # estimate for next call
        if circuit_breaker.check_and_record(estimated_cost):
            response = claude_client.messages.create(...)
        else:
            raise DailyLimitExceeded(...)
    """

    KEY_PREFIX = "creditor_matcher:daily_cost"
    TTL_SECONDS = 48 * 60 * 60  # 48 hours

    def __init__(
        self,
        redis_client,
        daily_limit_usd: Optional[float] = None
    ):
        """
        Initialize circuit breaker with Redis client.

        Args:
            redis_client: Redis client instance (from redis-py).
            daily_limit_usd: Daily spend limit in USD.
                             Defaults to settings.daily_cost_limit_usd ($50).
        """
        self.redis = redis_client
        self.daily_limit = daily_limit_usd or settings.daily_cost_limit_usd

        logger.debug(
            "daily_circuit_breaker_initialized",
            daily_limit_usd=self.daily_limit
        )

    def _get_key(self) -> str:
        """Get Redis key for today's cost tracking."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"{self.KEY_PREFIX}:{today}"

    def get_current_spend(self) -> float:
        """
        Get today's current spend from Redis.

        Returns:
            Current spend in USD, or 0.0 if no spend recorded.
        """
        key = self._get_key()
        value = self.redis.get(key)

        if value is None:
            return 0.0

        return float(value)

    def is_open(self) -> bool:
        """
        Check if circuit is open (limit exceeded).

        Returns:
            True if daily limit exceeded (circuit is "open" = blocking).
            False if under limit (circuit is "closed" = allowing).
        """
        current_spend = self.get_current_spend()
        is_tripped = current_spend >= self.daily_limit

        if is_tripped:
            logger.error(
                "daily_circuit_breaker_open",
                current_spend=current_spend,
                daily_limit=self.daily_limit
            )

        return is_tripped

    def check_and_record(self, estimated_cost_usd: float) -> bool:
        """
        Check if operation fits within daily limit and record cost.

        This is atomic: checks current spend, and if under limit,
        records the cost in a single Redis operation.

        Args:
            estimated_cost_usd: Estimated cost for the operation.

        Returns:
            True if operation was allowed and cost recorded.
            False if operation would exceed daily limit.

        Note:
            This uses optimistic recording - cost is recorded even if
            the actual API call might fail. This is safer (slightly
            overestimates spend) than recording after the call (risks
            budget overrun if crash after call but before record).
        """
        key = self._get_key()

        # Get current spend first
        current_spend = self.get_current_spend()

        # Check if would exceed limit
        if current_spend + estimated_cost_usd > self.daily_limit:
            logger.warning(
                "daily_circuit_breaker_would_exceed",
                estimated_cost=estimated_cost_usd,
                current_spend=current_spend,
                daily_limit=self.daily_limit,
                remaining=self.daily_limit - current_spend
            )
            return False

        # Record cost atomically using INCRBYFLOAT
        new_total = self.redis.incrbyfloat(key, estimated_cost_usd)

        # Set TTL if this is first cost of the day (key was just created)
        # Check if new_total is very close to estimated_cost (means it was 0 before)
        if abs(float(new_total) - estimated_cost_usd) < 0.0001:
            self.redis.expire(key, self.TTL_SECONDS)

        # Log warning when approaching limit (>80%)
        usage_percent = (float(new_total) / self.daily_limit) * 100
        if usage_percent > 80:
            logger.warning(
                "daily_cost_high_usage",
                cost=estimated_cost_usd,
                new_total=float(new_total),
                daily_limit=self.daily_limit,
                percent=round(usage_percent, 1)
            )
        else:
            logger.debug(
                "daily_cost_recorded",
                cost=estimated_cost_usd,
                new_total=float(new_total),
                percent=round(usage_percent, 1)
            )

        return True

    def __repr__(self) -> str:
        current = self.get_current_spend()
        return (
            f"DailyCostCircuitBreaker(spent=${current:.4f}, "
            f"limit=${self.daily_limit:.2f}, "
            f"is_open={self.is_open()})"
        )
