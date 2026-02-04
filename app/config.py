"""
Application Configuration
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # Database
    database_url: Optional[str] = None
    mongodb_url: Optional[str] = None

    # Redis & Job Queue
    redis_url: Optional[str] = None

    # Environment
    environment: str = "development"

    # Webhook
    webhook_secret: Optional[str] = None

    # LLM
    llm_provider: str = "claude"

    # Email Notifications
    admin_email: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None

    # Worker Configuration
    worker_processes: int = 2
    worker_threads: int = 1

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
