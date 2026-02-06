"""
Prompt Metrics Daily Rollup Service

Aggregates raw prompt_performance_metrics into prompt_performance_daily.
USER DECISION: 30-day raw retention, then aggregate to daily summaries.

Run daily at 01:00 to aggregate previous day's metrics.
"""

from datetime import date, timedelta
from sqlalchemy import func, select, cast, Integer, and_
from sqlalchemy.orm import Session
import structlog

from app.models.prompt_metrics import PromptPerformanceMetrics, PromptPerformanceDaily

logger = structlog.get_logger(__name__)


def aggregate_daily_metrics(target_date: date, db: Session) -> int:
    """
    Aggregate raw metrics for given date into daily summary.

    Args:
        target_date: Date to aggregate (typically yesterday)
        db: Database session

    Returns:
        Number of prompt versions aggregated
    """
    logger.info("daily_rollup_started", date=str(target_date))

    # Query aggregated metrics grouped by prompt_template_id
    stmt = select(
        PromptPerformanceMetrics.prompt_template_id,
        func.count().label('total_extractions'),
        func.sum(PromptPerformanceMetrics.input_tokens).label('total_input_tokens'),
        func.sum(PromptPerformanceMetrics.output_tokens).label('total_output_tokens'),
        func.sum(PromptPerformanceMetrics.api_cost_usd).label('total_api_cost_usd'),
        func.sum(cast(PromptPerformanceMetrics.extraction_success, Integer)).label('successful_extractions'),
        func.avg(PromptPerformanceMetrics.confidence_score).label('avg_confidence_score'),
        func.sum(cast(PromptPerformanceMetrics.manual_review_required, Integer)).label('manual_review_count'),
        func.avg(PromptPerformanceMetrics.execution_time_ms).label('avg_execution_time_ms'),
    ).where(
        func.date(PromptPerformanceMetrics.extracted_at) == target_date
    ).group_by(
        PromptPerformanceMetrics.prompt_template_id
    )

    results = db.execute(stmt).fetchall()

    aggregated_count = 0
    for row in results:
        # Upsert daily rollup (update if exists, insert if not)
        existing = db.query(PromptPerformanceDaily).filter(
            and_(
                PromptPerformanceDaily.prompt_template_id == row.prompt_template_id,
                PromptPerformanceDaily.date == target_date
            )
        ).first()

        if existing:
            # Update existing record
            existing.total_extractions = row.total_extractions
            existing.total_input_tokens = row.total_input_tokens or 0
            existing.total_output_tokens = row.total_output_tokens or 0
            existing.total_api_cost_usd = row.total_api_cost_usd or 0
            existing.successful_extractions = row.successful_extractions or 0
            existing.avg_confidence_score = row.avg_confidence_score
            existing.manual_review_count = row.manual_review_count or 0
            existing.avg_execution_time_ms = int(row.avg_execution_time_ms) if row.avg_execution_time_ms else 0
        else:
            # Create new record
            rollup = PromptPerformanceDaily(
                prompt_template_id=row.prompt_template_id,
                date=target_date,
                total_extractions=row.total_extractions,
                total_input_tokens=row.total_input_tokens or 0,
                total_output_tokens=row.total_output_tokens or 0,
                total_api_cost_usd=row.total_api_cost_usd or 0,
                successful_extractions=row.successful_extractions or 0,
                avg_confidence_score=row.avg_confidence_score,
                manual_review_count=row.manual_review_count or 0,
                avg_execution_time_ms=int(row.avg_execution_time_ms) if row.avg_execution_time_ms else 0,
                p95_execution_time_ms=None  # Note: p95 requires window function or percentile_cont
            )
            db.add(rollup)

        aggregated_count += 1

    db.commit()

    logger.info(
        "daily_rollup_completed",
        date=str(target_date),
        prompt_versions_aggregated=aggregated_count
    )

    return aggregated_count


def cleanup_old_raw_metrics(db: Session, retention_days: int = 30) -> int:
    """
    Delete raw metrics older than retention period.

    USER DECISION: 30-day raw retention.

    Args:
        db: Database session
        retention_days: Days to retain raw metrics (default 30)

    Returns:
        Number of records deleted
    """
    cutoff_date = date.today() - timedelta(days=retention_days)

    deleted = db.query(PromptPerformanceMetrics).filter(
        func.date(PromptPerformanceMetrics.extracted_at) < cutoff_date
    ).delete(synchronize_session=False)

    db.commit()

    logger.info(
        "raw_metrics_cleanup_completed",
        cutoff_date=str(cutoff_date),
        records_deleted=deleted
    )

    return deleted


def run_daily_rollup_job(db: Session):
    """
    Combined job: aggregate yesterday's metrics, cleanup old data.

    This is the scheduled job entry point.
    """
    yesterday = date.today() - timedelta(days=1)

    try:
        # Aggregate yesterday's metrics
        aggregated = aggregate_daily_metrics(yesterday, db)

        # Cleanup old raw metrics
        deleted = cleanup_old_raw_metrics(db, retention_days=30)

        logger.info(
            "daily_rollup_job_complete",
            date=str(yesterday),
            prompt_versions_aggregated=aggregated,
            old_records_deleted=deleted
        )

    except Exception as e:
        logger.error(
            "daily_rollup_job_failed",
            date=str(yesterday),
            error=str(e),
            exc_info=True
        )
        raise


__all__ = [
    "aggregate_daily_metrics",
    "cleanup_old_raw_metrics",
    "run_daily_rollup_job"
]
