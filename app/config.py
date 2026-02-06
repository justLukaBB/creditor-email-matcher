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
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-5-20250929"

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

    # Confidence Routing Configuration (Phase 7)
    # USER DECISION: Global thresholds only, stored in env vars
    confidence_high_threshold: float = 0.85  # Above this = auto-update, log only
    confidence_low_threshold: float = 0.60   # Below this = manual review queue
    # Note: MEDIUM is between LOW and HIGH thresholds

    # Circuit Breaker Configuration (Phase 9)
    # USER DECISION: 5 consecutive failures, 60 seconds auto-recovery
    circuit_breaker_fail_max: int = 5  # Consecutive failures before opening circuit
    circuit_breaker_reset_timeout: int = 60  # Seconds before auto-recovery attempt
    circuit_breaker_alert_email: Optional[str] = None  # Falls back to admin_email

    # Sentry Error Tracking (Phase 9)
    sentry_dsn: Optional[str] = None  # Sentry project DSN
    sentry_environment: Optional[str] = None  # Defaults to environment setting

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
