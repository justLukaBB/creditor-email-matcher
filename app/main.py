"""
Creditor Email Matcher - Main Application
FastAPI Entry Point with APScheduler for Reconciliation
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import structlog

from app.config import settings
from app.database import init_db
from app.routers.webhook import router as webhook_router
from app.routers.jobs import router as jobs_router
from app.routers.manual_review import router as manual_review_router

# Structured Logging Setup
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer()
    ]
)
logger = structlog.get_logger()

# FastAPI App
app = FastAPI(
    title="Creditor Email Matcher",
    description="AI-gestützter Microservice für automatische Gläubiger-Email Zuordnung",
    version="0.3.0",
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url="/redoc" if settings.environment == "development" else None,
)

# APScheduler instance (module level)
scheduler = BackgroundScheduler(timezone="Europe/Berlin")

# Register routers
app.include_router(webhook_router)
app.include_router(jobs_router)
app.include_router(manual_review_router)


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


@app.on_event("startup")
async def startup_event():
    """Application Startup"""
    logger.info("startup", environment=settings.environment)

    # Initialize database connection
    init_db()
    logger.info("database_initialized")

    # Start reconciliation scheduler (skip in testing)
    if settings.environment != "testing":
        scheduler.add_job(
            run_scheduled_reconciliation,
            trigger=IntervalTrigger(hours=1),
            id="hourly_reconciliation",
            name="Hourly PostgreSQL-MongoDB Reconciliation",
            replace_existing=True
        )
        scheduler.start()
        logger.info("scheduler_started", interval="hourly", job="reconciliation")


@app.on_event("shutdown")
async def shutdown_event():
    """Application Shutdown"""
    logger.info("shutdown")

    # Stop reconciliation scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")


@app.get("/")
async def root():
    """Root Endpoint"""
    return {
        "message": "Creditor Email Matcher API",
        "version": "0.3.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """
    Health Check Endpoint
    Prüft ob die Anwendung läuft
    """
    health_status = {
        "status": "healthy",
        "environment": settings.environment,
        "services": {
            "api": "running",
            "scheduler": "running" if scheduler.running else "stopped"
        }
    }

    # Optional: Database Check
    if settings.database_url:
        health_status["services"]["database"] = "configured"

    # Optional: MongoDB Check
    if settings.mongodb_url:
        health_status["services"]["mongodb"] = "configured"

    return JSONResponse(
        content=health_status,
        status_code=200
    )


@app.post("/api/v1/admin/reconciliation/trigger")
async def trigger_reconciliation():
    """
    Manually trigger reconciliation job.

    For testing and operational purposes. Runs reconciliation immediately
    instead of waiting for the hourly schedule.

    Returns:
        dict: Reconciliation run results with counts and status
    """
    try:
        from app.database import SessionLocal
        from app.services.reconciliation import ReconciliationService
        from app.services.mongodb_client import mongodb_service

        if SessionLocal is None:
            return {
                "status": "error",
                "message": "Database not configured"
            }

        reconciliation_svc = ReconciliationService(
            session_factory=SessionLocal,
            mongodb_service=mongodb_service
        )
        result = reconciliation_svc.run_reconciliation()

        return {
            "status": "completed",
            "result": result
        }

    except Exception as e:
        logger.error("manual_reconciliation_error", error=str(e), exc_info=True)
        return {
            "status": "error",
            "message": str(e)
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.environment == "development"
    )
