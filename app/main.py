"""
Creditor Email Matcher - Main Application
FastAPI Entry Point
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.config import settings
from app.database import init_db
from app.routers import webhook, inquiries
import logging

# Logging Setup
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# FastAPI App
app = FastAPI(
    title="Creditor Email Matcher",
    description="AI-gestützter Microservice für automatische Gläubiger-Email Zuordnung",
    version="0.1.0",
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url="/redoc" if settings.environment == "development" else None,
)

# Include routers
app.include_router(webhook.router)
app.include_router(inquiries.router)


@app.on_event("startup")
async def startup_event():
    """Application Startup"""
    logger.info(f"Starting Creditor Email Matcher in {settings.environment} mode")
    logger.info(f"Log Level: {settings.log_level}")

    # Initialize database connection
    init_db()
    logger.info("Database initialization complete")


@app.on_event("shutdown")
async def shutdown_event():
    """Application Shutdown"""
    logger.info("Shutting down Creditor Email Matcher")


@app.get("/")
async def root():
    """Root Endpoint"""
    return {
        "message": "Creditor Email Matcher API",
        "version": "0.1.0",
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
        }
    }

    # Optional: Database Check (später)
    if settings.database_url:
        health_status["services"]["database"] = "not_configured_yet"

    # Optional: OpenAI Check (später)
    if settings.openai_api_key:
        health_status["services"]["openai"] = "not_configured_yet"

    return JSONResponse(
        content=health_status,
        status_code=200
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.environment == "development"
    )
