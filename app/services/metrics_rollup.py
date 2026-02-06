"""
Operational Metrics Daily Rollup Service

Aggregates raw operational_metrics into operational_metrics_daily.
USER DECISION: 30-day raw retention, then aggregate to daily summaries.

Run daily at 01:30 to aggregate previous day's metrics.
"""

from datetime import date, timedelta
from typing import Dict, List, Tuple
from sqlalchemy import func, select, and_
from sqlalchemy.orm import Session
import logging

from app.models.operational_metrics import OperationalMetrics, OperationalMetricsDaily

logger = logging.getLogger(__name__)


def extract_labels_key(labels: Dict) -> str:
    """
    Extract label key for grouping.

    Args:
        labels: Labels dictionary

    Returns:
        String key for grouping (e.g., "actor:process_email")
    """
    if not labels:
        return "all"

    # Extract most significant label for grouping
    if "actor" in labels:
        return f"actor:{labels['actor']}"
    elif "queue" in labels:
        return f"queue:{labels['queue']}"
    elif "model" in labels:
        return f"model:{labels['model']}"
    elif "bucket" in labels:
        return f"bucket:{labels['bucket']}"
    else:
        # Use first key-value pair
        key, value = next(iter(labels.items()))
        return f"{key}:{value}"


def calculate_percentile_95(values: List[float]) -> float:
    """
    Calculate 95th percentile of values.

    Args:
        values: List of metric values

    Returns:
        95th percentile value
    """
    if not values:
        return 0.0

    sorted_values = sorted(values)
    index = int(len(sorted_values) * 0.95)
    return sorted_values[min(index, len(sorted_values) - 1)]


def aggregate_metrics_for_date(target_date: date, db: Session) -> int:
    """
    Aggregate raw metrics for given date into daily summaries.

    Groups by (metric_type, labels_key) and calculates aggregates.

    Args:
        target_date: Date to aggregate (typically yesterday)
        db: Database session

    Returns:
        Number of metric groups aggregated
    """
    logger.info(f"Starting operational metrics rollup for {target_date}")

    # Query all raw metrics for target date
    metrics = db.query(OperationalMetrics).filter(
        func.date(OperationalMetrics.recorded_at) == target_date
    ).all()

    if not metrics:
        logger.info(f"No operational metrics found for {target_date}")
        return 0

    # Group metrics by (metric_type, labels_key)
    grouped: Dict[Tuple[str, str], List[float]] = {}

    for metric in metrics:
        labels_key = extract_labels_key(metric.labels)
        key = (metric.metric_type, labels_key)

        if key not in grouped:
            grouped[key] = []
        grouped[key].append(metric.metric_value)

    # Create or update daily rollup records
    aggregated_count = 0

    for (metric_type, labels_key), values in grouped.items():
        sample_count = len(values)
        sum_value = sum(values)
        avg_value = sum_value / sample_count
        min_value = min(values)
        max_value = max(values)
        p95_value = calculate_percentile_95(values)

        # Upsert daily rollup (update if exists, insert if not)
        existing = db.query(OperationalMetricsDaily).filter(
            and_(
                OperationalMetricsDaily.metric_type == metric_type,
                OperationalMetricsDaily.date == target_date,
                OperationalMetricsDaily.labels_key == labels_key
            )
        ).first()

        if existing:
            # Update existing record
            existing.sample_count = sample_count
            existing.sum_value = sum_value
            existing.avg_value = avg_value
            existing.min_value = min_value
            existing.max_value = max_value
            existing.p95_value = p95_value
        else:
            # Create new record
            rollup = OperationalMetricsDaily(
                metric_type=metric_type,
                date=target_date,
                labels_key=labels_key,
                sample_count=sample_count,
                sum_value=sum_value,
                avg_value=avg_value,
                min_value=min_value,
                max_value=max_value,
                p95_value=p95_value
            )
            db.add(rollup)

        aggregated_count += 1

    db.commit()

    logger.info(
        f"Operational metrics rollup completed for {target_date}: "
        f"{aggregated_count} metric groups aggregated from {len(metrics)} raw records"
    )

    return aggregated_count


def cleanup_old_raw_metrics(db: Session, retention_days: int = 30) -> int:
    """
    Delete raw metrics older than retention period.

    USER DECISION: 30-day raw retention (matches prompt metrics).

    Args:
        db: Database session
        retention_days: Days to retain raw metrics (default 30)

    Returns:
        Number of records deleted
    """
    cutoff_date = date.today() - timedelta(days=retention_days)

    deleted = db.query(OperationalMetrics).filter(
        func.date(OperationalMetrics.recorded_at) < cutoff_date
    ).delete(synchronize_session=False)

    db.commit()

    logger.info(
        f"Operational metrics cleanup completed: "
        f"{deleted} records deleted (older than {cutoff_date})"
    )

    return deleted


def run_operational_metrics_rollup(db: Session) -> None:
    """
    Combined job: aggregate yesterday's metrics, cleanup old data.

    This is the scheduled job entry point.

    Args:
        db: Database session
    """
    yesterday = date.today() - timedelta(days=1)

    try:
        # Aggregate yesterday's metrics
        aggregated = aggregate_metrics_for_date(yesterday, db)

        # Cleanup old raw metrics (older than 30 days)
        deleted = cleanup_old_raw_metrics(db, retention_days=30)

        logger.info(
            f"Operational metrics rollup job complete for {yesterday}: "
            f"{aggregated} groups aggregated, {deleted} old records deleted"
        )

    except Exception as e:
        logger.error(
            f"Operational metrics rollup job failed for {yesterday}: {str(e)}",
            exc_info=True
        )
        raise


__all__ = [
    "aggregate_metrics_for_date",
    "cleanup_old_raw_metrics",
    "run_operational_metrics_rollup",
]
