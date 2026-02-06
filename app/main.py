"""
Creditor Email Matcher - Main Application
FastAPI Entry Point with APScheduler for Reconciliation
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import logging

from app.config import settings
from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.routers.webhook import router as webhook_router
from app.routers.jobs import router as jobs_router
from app.routers.manual_review import router as manual_review_router
from app.middleware.correlation_id import CorrelationIdMiddleware
from app.services.monitoring.logging import setup_logging
from app.services.monitoring.error_tracking import init_sentry

# Setup JSON logging with correlation ID
setup_logging()
logger = logging.getLogger(__name__)

# FastAPI App
app = FastAPI(
    title="Creditor Email Matcher",
    description="AI-gestützter Microservice für automatische Gläubiger-Email Zuordnung",
    version="0.3.0",
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url="/redoc" if settings.environment == "development" else None,
)

# Add Correlation ID Middleware (BEFORE routers for context availability)
app.add_middleware(CorrelationIdMiddleware)

# APScheduler instance (module level)
scheduler = None

# Register routers
app.include_router(webhook_router)
app.include_router(jobs_router)
app.include_router(manual_review_router)


@app.on_event("startup")
async def startup_event():
    """Application Startup"""
    global scheduler

    logger.info("startup", extra={"environment": settings.environment})

    # Initialize Sentry for error tracking
    sentry_enabled = init_sentry()
    logger.info("monitoring_initialized", extra={"sentry_enabled": sentry_enabled})

    # Initialize database connection
    init_db()
    logger.info("database_initialized")

    # Start scheduler with all jobs (reconciliation + prompt rollup)
    scheduler = start_scheduler(environment=settings.environment)


@app.on_event("shutdown")
async def shutdown_event():
    """Application Shutdown"""
    logger.info("shutdown")

    # Stop scheduler
    stop_scheduler(scheduler)


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
        from app.scheduler import run_scheduled_reconciliation

        # Run reconciliation synchronously
        run_scheduled_reconciliation()

        return {
            "status": "completed",
            "message": "Reconciliation job executed. Check logs for results."
        }

    except Exception as e:
        logger.error("manual_reconciliation_error", extra={"error": str(e)}, exc_info=True)
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
