"""
Token Budget Tracker

Per-job token budget enforcement to prevent cost explosions during extraction.
Each extraction job has a maximum token budget (default 100K from settings).
"""

import structlog
from typing import Optional

from app.config import settings

logger = structlog.get_logger(__name__)


class TokenBudgetExceeded(Exception):
    """Raised when token budget would be exceeded by an operation."""

    def __init__(
        self,
        requested: int,
        used: int,
        max_tokens: int,
        message: Optional[str] = None
    ):
        self.requested = requested
        self.used = used
        self.max_tokens = max_tokens
        self.remaining = max_tokens - used

        if message is None:
            message = (
                f"Token budget exceeded: requested {requested} tokens, "
                f"but only {self.remaining} remaining (used {used}/{max_tokens})"
            )
        super().__init__(message)


class TokenBudgetTracker:
    """
    Per-job token budget tracking for Claude API usage.

    Usage:
        tracker = TokenBudgetTracker()  # Uses settings.max_tokens_per_job

        # Before API call, check if operation would fit in budget
        if tracker.check_budget(estimated_tokens=2000):
            response = claude_client.messages.create(...)
            tracker.add_usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens
            )

        # Check remaining budget
        print(f"Tokens remaining: {tracker.remaining()}")
    """

    def __init__(self, max_tokens: Optional[int] = None):
        """
        Initialize tracker with token budget.

        Args:
            max_tokens: Maximum tokens for this job.
                        Defaults to settings.max_tokens_per_job (100K).
        """
        self.max_tokens = max_tokens or settings.max_tokens_per_job
        self.used_tokens = 0
        self._input_tokens = 0
        self._output_tokens = 0

        logger.debug(
            "token_budget_tracker_initialized",
            max_tokens=self.max_tokens
        )

    def check_budget(self, estimated_tokens: int) -> bool:
        """
        Check if operation would fit within budget.

        Args:
            estimated_tokens: Estimated tokens for the operation.

        Returns:
            True if operation fits within budget, False otherwise.

        Note:
            Does NOT raise exception - use for soft checks.
            Use would_exceed() + raise for hard enforcement.
        """
        would_fit = (self.used_tokens + estimated_tokens) <= self.max_tokens

        if not would_fit:
            logger.warning(
                "token_budget_check_failed",
                estimated=estimated_tokens,
                used=self.used_tokens,
                max_tokens=self.max_tokens,
                remaining=self.remaining()
            )

        return would_fit

    def would_exceed(self, estimated_tokens: int) -> bool:
        """
        Check if operation would exceed budget.

        Args:
            estimated_tokens: Estimated tokens for the operation.

        Returns:
            True if operation would EXCEED budget, False if it fits.
        """
        return (self.used_tokens + estimated_tokens) > self.max_tokens

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        """
        Record token usage from Claude API response.

        Args:
            input_tokens: Input tokens from response.usage.input_tokens
            output_tokens: Output tokens from response.usage.output_tokens
        """
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self.used_tokens = self._input_tokens + self._output_tokens

        # Log warning when approaching limit (>80% used)
        usage_percent = (self.used_tokens / self.max_tokens) * 100
        if usage_percent > 80:
            logger.warning(
                "token_budget_high_usage",
                used=self.used_tokens,
                max_tokens=self.max_tokens,
                percent=round(usage_percent, 1),
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens
            )
        else:
            logger.debug(
                "token_usage_recorded",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_used=self.used_tokens,
                percent=round(usage_percent, 1)
            )

    def remaining(self) -> int:
        """Return tokens remaining in budget."""
        return max(0, self.max_tokens - self.used_tokens)

    def estimate_cost_usd(self) -> float:
        """
        Calculate estimated cost from token usage.

        Uses Claude Sonnet 4.5 pricing from settings:
        - Input: $3.00 per million tokens
        - Output: $15.00 per million tokens
        """
        input_cost = (
            self._input_tokens / 1_000_000
        ) * settings.claude_input_cost_per_million
        output_cost = (
            self._output_tokens / 1_000_000
        ) * settings.claude_output_cost_per_million

        return round(input_cost + output_cost, 6)

    @property
    def input_tokens(self) -> int:
        """Total input tokens used."""
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        """Total output tokens used."""
        return self._output_tokens

    def __repr__(self) -> str:
        return (
            f"TokenBudgetTracker(used={self.used_tokens}, "
            f"max={self.max_tokens}, remaining={self.remaining()})"
        )
