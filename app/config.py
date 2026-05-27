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
    mongodb_database: str = "test"  # Database name in MongoDB

    # Redis & Job Queue
    redis_url: Optional[str] = None

    # Environment
    environment: str = "development"

    # Webhook
    webhook_secret: Optional[str] = None

    # LLM
    # Provider switch: "claude" (Anthropic-direct, US-East, legacy) |
    # "vertex" (Google Vertex AI, EU/Frankfurt). See EMAIL-MATCHER-VERTEX-MIGRATION-PLAN.md
    llm_provider: str = "claude"
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    openai_api_key: Optional[str] = None  # For OpenAI fallback (optional)

    # --- Vertex AI (LLM EU-Migration) ---
    google_cloud_project: Optional[str] = None  # GCP project ID; required when llm_provider=vertex
    vertex_ai_region: str = "europe-west1"       # Belgium (EU/DSGVO). NOTE: europe-west3/Frankfurt has no Gemini 2.5 for this project (verified 404) — do not switch back without checking model availability.
    # Per-call-site Gemini model IDs — tunable via ENV without a deploy.
    gemini_model_intent: str = "gemini-2.5-flash-lite"
    gemini_model_entity: str = "gemini-2.5-flash"
    gemini_model_settlement: str = "gemini-2.5-pro"  # money-critical: no cost-saving here
    gemini_model_pdf: str = "gemini-2.5-pro"         # vision on lawyer scans
    gemini_model_image: str = "gemini-2.5-pro"       # vision

    # Vertex AI rate limiting + 429 retry (ported from creditor-process-fastapi)
    gemini_enable_rate_limiting: bool = True
    gemini_requests_per_minute: int = 60
    gemini_max_concurrent_requests: int = 5
    gemini_adaptive_throttling: bool = True
    gemini_min_request_interval_seconds: float = 0.5
    gemini_429_max_retries: int = 5
    gemini_429_base_delay_seconds: float = 2.0
    gemini_429_max_delay_seconds: float = 60.0
    gemini_429_retry_multiplier: float = 2.0

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

    # Persistent creditor attachment archive (Issue #169)
    # Tenant-isolated layout: gs://{bucket}/{prefix}/{kanzlei_id|_unassigned}/{resend_email_id}/{filename}
    gcs_attachments_prefix: str = "creditor-attachments"
    gcs_attachments_unassigned_folder: str = "_unassigned"

    # Cost Control (Phase 3: Multi-Format Document Extraction)
    max_tokens_per_job: int = 100000  # 100K tokens per extraction job
    daily_cost_limit_usd: float = 50.0  # Daily Claude API spend limit
    claude_input_cost_per_million: float = 3.0  # Sonnet 4.5 pricing: $3/M input
    claude_output_cost_per_million: float = 15.0  # Sonnet 4.5 pricing: $15/M output

    # Matching Engine Configuration (Phase 6)
    match_lookback_days: int = 90  # creditor_inquiries window (increased from 30d for late creditor responses)
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

    # Mandanten Portal Webhook (notify portal after MongoDB write)
    portal_webhook_url: Optional[str] = None  # e.g. https://portal.example.com/api/webhooks/matcher-response
    portal_webhook_secret: Optional[str] = None  # HMAC-SHA256 signing secret (legacy, body-only)
    # Phase 5.3: Portal HMAC-shared-secret for X-Matcher-Signature / X-Matcher-Timestamp.
    # Must be set IDENTICAL in both the matcher and the portal Render service.
    matcher_portal_hmac_secret: Optional[str] = None
    # Phase 5.4: Portal bounce-bridge endpoint. Derived from portal_webhook_url
    # when unset (matcher-response → matcher-bounce).
    portal_bounce_webhook_url: Optional[str] = None
    # Phase 5.6: Default raw-EML mirror to GCS for forensic archival.
    raw_eml_mirror_enabled: bool = True
    gcs_raw_emails_prefix: str = "raw-emails"

    # Resend Inbound Email
    resend_api_key: Optional[str] = None  # Resend API key for fetching email content
    resend_webhook_secret: Optional[str] = None  # Svix signing secret for webhook verification

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
