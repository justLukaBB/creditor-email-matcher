"""
APScheduler Background Jobs

Scheduled jobs for reconciliation, prompt metrics rollup, and operational metrics rollup.
Jobs run via BackgroundScheduler in FastAPI process.
"""

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

logger = structlog.get_logger(__name__)


def run_scheduled_reconciliation():
    """
    Wrapper function for scheduled reconciliation job.

    Called by APScheduler every hour to compare PostgreSQL with MongoDB
    and repair any inconsistencies.
    """
    try:
        from app.database import SessionLocal
        from app.services.reconciliation import ReconciliationService
        from app.services.mongodb_client import mongodb_service

        if SessionLocal is None:
            logger.warning("reconciliation_skipped", reason="database_not_configured")
            return

        reconciliation_svc = ReconciliationService(
            session_factory=SessionLocal,
            mongodb_service=mongodb_service
        )
        result = reconciliation_svc.run_reconciliation()
        logger.info("reconciliation_completed", **result)

    except Exception as e:
        logger.error("reconciliation_crashed", error=str(e), exc_info=True)


def run_prompt_rollup():
    """
    Wrapper function for daily prompt metrics rollup job.

    Called by APScheduler daily at 01:00 to aggregate previous day's metrics
    and cleanup old raw data (30+ days).
    """
    try:
        from app.database import SessionLocal
        from app.services.prompt_rollup import run_daily_rollup_job

        if SessionLocal is None:
            logger.warning("prompt_rollup_skipped", reason="database_not_configured")
            return

        db = SessionLocal()
        try:
            run_daily_rollup_job(db)
        finally:
            db.close()

    except Exception as e:
        logger.error("prompt_rollup_crashed", error=str(e), exc_info=True)


def run_scheduled_operational_rollup():
    """
    Wrapper function for daily operational metrics rollup job.

    Called by APScheduler daily at 01:30 to aggregate previous day's metrics
    and cleanup old raw data (30+ days).
    """
    try:
        from app.database import SessionLocal
        from app.services.metrics_rollup import run_operational_metrics_rollup

        if SessionLocal is None:
            logger.warning("operational_rollup_skipped", reason="database_not_configured")
            return

        db = SessionLocal()
        try:
            run_operational_metrics_rollup(db)
        finally:
            db.close()

    except Exception as e:
        logger.error("operational_rollup_crashed", error=str(e), exc_info=True)


def start_scheduler(environment: str = "production") -> BackgroundScheduler:
    """
    Start background scheduler with all jobs.

    Args:
        environment: Current environment (skip scheduler in testing)

    Returns:
        BackgroundScheduler instance
    """
    scheduler = BackgroundScheduler(timezone="Europe/Berlin")

    if environment == "testing":
        logger.info("scheduler_skipped", reason="testing_environment")
        return scheduler

    # Job 1: Hourly reconciliation (PostgreSQL-MongoDB consistency)
    scheduler.add_job(
        run_scheduled_reconciliation,
        trigger=IntervalTrigger(hours=1),
        id="hourly_reconciliation",
        name="Hourly PostgreSQL-MongoDB Reconciliation",
        replace_existing=True
    )
    logger.info("job_registered", job="reconciliation", schedule="hourly")

    # Job 2: Daily prompt metrics rollup (at 01:00)
    scheduler.add_job(
        run_prompt_rollup,
        trigger=CronTrigger(hour=1, minute=0),
        id="prompt_metrics_rollup",
        name="Prompt Metrics Daily Rollup",
        replace_existing=True
    )
    logger.info("job_registered", job="prompt_metrics_rollup", schedule="daily_01:00")

    # Job 3: Daily operational metrics rollup (at 01:30)
    scheduler.add_job(
        run_scheduled_operational_rollup,
        trigger=CronTrigger(hour=1, minute=30),
        id="operational_metrics_rollup",
        name="Operational Metrics Daily Rollup",
        replace_existing=True
    )
    logger.info("job_registered", job="operational_metrics_rollup", schedule="daily_01:30")

    scheduler.start()
    logger.info("scheduler_started", jobs=["reconciliation", "prompt_metrics_rollup", "operational_metrics_rollup"])

    return scheduler


def stop_scheduler(scheduler: BackgroundScheduler):
    """
    Stop background scheduler gracefully.

    Args:
        scheduler: BackgroundScheduler instance to stop
    """
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")


__all__ = [
    "start_scheduler",
    "stop_scheduler",
    "run_scheduled_reconciliation",
    "run_prompt_rollup",
    "run_scheduled_operational_rollup"
]
