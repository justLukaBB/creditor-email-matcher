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

    # GCS Storage (Phase 3)
    gcs_bucket_name: Optional[str] = None
    gcs_max_file_size_mb: int = 32  # Claude API 32MB limit

    # Cost Control (Phase 3: Multi-Format Document Extraction)
    max_tokens_per_job: int = 100000  # 100K tokens per extraction job
    daily_cost_limit_usd: float = 50.0  # Daily Claude API spend limit
    claude_input_cost_per_million: float = 3.0  # Sonnet 4.5 pricing: $3/M input
    claude_output_cost_per_million: float = 15.0  # Sonnet 4.5 pricing: $15/M output

    # Matching Engine Configuration (Phase 6)
    match_lookback_days: int = 30  # creditor_inquiries window
    match_threshold_high: float = 0.85  # High confidence threshold
    match_threshold_medium: float = 0.70  # Medium confidence threshold

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
