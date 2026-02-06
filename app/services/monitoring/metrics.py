"""
Operational Metrics Collection Service

Provides methods for recording pipeline health metrics:
- Queue depth
- Processing time per stage
- Error counts
- Token usage
- Confidence distribution

Follows DualDatabaseWriter pattern: does NOT commit, caller controls transaction.
"""

from typing import Optional
from sqlalchemy.orm import Session
from app.models.operational_metrics import OperationalMetrics


class MetricsCollector:
    """
    Service for recording operational metrics.

    Does NOT commit - caller controls transaction (same pattern as DualDatabaseWriter).
    """

    def __init__(self, db: Session):
        """
        Initialize metrics collector.

        Args:
            db: Database session (caller-managed)
        """
        self.db = db

    def record_queue_depth(self, queue_name: str, depth: int) -> None:
        """
        Record current queue depth.

        Args:
            queue_name: Name of the queue (e.g., "email_processing")
            depth: Number of items in queue
        """
        metric = OperationalMetrics(
            metric_type="queue_depth",
            metric_value=float(depth),
            labels={"queue": queue_name}
        )
        self.db.add(metric)

    def record_processing_time(
        self,
        actor_name: str,
        stage: str,
        duration_ms: int,
        email_id: Optional[int] = None
    ) -> None:
        """
        Record processing time for a stage.

        Args:
            actor_name: Name of the actor (e.g., "process_email")
            stage: Processing stage (e.g., "extraction", "matching")
            duration_ms: Duration in milliseconds
            email_id: Optional link to specific email
        """
        metric = OperationalMetrics(
            metric_type="processing_time_ms",
            metric_value=float(duration_ms),
            labels={"actor": actor_name, "stage": stage},
            email_id=email_id
        )
        self.db.add(metric)

    def record_error(
        self,
        actor_name: str,
        error_type: str,
        email_id: Optional[int] = None
    ) -> None:
        """
        Record error occurrence.

        Args:
            actor_name: Name of the actor (e.g., "process_email")
            error_type: Type of error (e.g., "TimeoutError", "ValidationError")
            email_id: Optional link to specific email
        """
        metric = OperationalMetrics(
            metric_type="error_count",
            metric_value=1.0,  # Count as 1 error
            labels={"actor": actor_name, "error_type": error_type},
            email_id=email_id
        )
        self.db.add(metric)

    def record_token_usage(
        self,
        model: str,
        operation: str,
        tokens: int,
        email_id: Optional[int] = None
    ) -> None:
        """
        Record Claude API token usage.

        Args:
            model: Model name (e.g., "claude-sonnet", "claude-haiku")
            operation: Operation type (e.g., "extraction", "classification")
            tokens: Total tokens used (input + output)
            email_id: Optional link to specific email
        """
        metric = OperationalMetrics(
            metric_type="token_usage",
            metric_value=float(tokens),
            labels={"model": model, "operation": operation},
            email_id=email_id
        )
        self.db.add(metric)

    def record_confidence(
        self,
        bucket: str,
        score: float,
        email_id: Optional[int] = None
    ) -> None:
        """
        Record confidence score distribution.

        Args:
            bucket: Confidence bucket ("high", "medium", "low")
            score: Confidence score (0.0 - 1.0)
            email_id: Optional link to specific email
        """
        if bucket not in ("high", "medium", "low"):
            raise ValueError(f"Invalid confidence bucket: {bucket}")

        metric = OperationalMetrics(
            metric_type="confidence_score",
            metric_value=score,
            labels={"bucket": bucket},
            email_id=email_id
        )
        self.db.add(metric)


def get_metrics_collector(db: Session) -> MetricsCollector:
    """
    Convenience factory for MetricsCollector.

    Args:
        db: Database session

    Returns:
        MetricsCollector instance
    """
    return MetricsCollector(db)


__all__ = [
    "MetricsCollector",
    "get_metrics_collector",
]
