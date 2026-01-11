"""
Application Configuration
Verwendet pydantic-settings für Environment Variables
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """
    Application Settings
    Lädt automatisch aus .env Datei
    """

    # Application
    environment: str = "development"
    log_level: str = "INFO"
    api_key: str = "dev_key_please_change"

    # Database
    database_url: Optional[str] = None

    # LLM Configuration
    llm_provider: str = "claude"  # "claude" or "openai"

    # OpenAI
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"  # or gpt-4o-mini for cost savings

    # Anthropic (Claude)
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-3-sonnet-20240229"  # or claude-3-haiku-20240307 for cost savings

    # Zendesk
    zendesk_subdomain: Optional[str] = None
    zendesk_email: Optional[str] = None
    zendesk_api_token: Optional[str] = None

    # MongoDB (for updating creditor debt amounts)
    mongodb_url: Optional[str] = None
    mongodb_database: str = "test"

    # SMTP (for email notifications)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None

    # Security
    webhook_secret: Optional[str] = None

    # Matching Engine Configuration
    match_threshold_high: float = 0.80  # Auto-assign threshold
    match_threshold_medium: float = 0.60  # Review queue threshold
    match_lookback_days: int = 60  # How far back to search for inquiries

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


# Global Settings Instance
settings = Settings()
