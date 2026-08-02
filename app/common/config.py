from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "CSI Kalamela FastAPI"
    debug: bool = False
    
    # Database Configuration
    # For Neon serverless PostgreSQL, use the POOLED connection string for better
    # performance in serverless environments. The pooler reduces cold start latency.
    #
    # Direct connection (slower cold starts):
    #   postgresql+psycopg://user:pass@ep-xxx.region.aws.neon.tech/dbname
    #
    # Pooled connection (recommended for Vercel/serverless):
    #   postgresql+psycopg://user:pass@ep-xxx-pooler.region.aws.neon.tech/dbname?sslmode=require
    #
    # Note the "-pooler" suffix in the hostname for the pooled connection.
    # Set via DATABASE_URL in .env (never commit real credentials here).
    database_url: str = ""
    secret_key: str = "change-this-secret"
    access_token_expire_minutes: int = 15  # Short-lived access tokens
    refresh_token_expire_days: int = 7  # Long-lived refresh tokens
    algorithm: str = "HS256"
    cors_origins: List[str] = ["*"]
    upload_dir: str = "storage/uploads"
    export_dir: str = "storage/exports"
    max_upload_size_mb: int = 5
    allowed_upload_extensions: List[str] = [".pdf", ".png", ".jpg", ".jpeg", ".webp"]

    # Object storage (any S3-compatible provider: OCI, Backblaze B2, ...)
    # Set STORAGE_* in the environment; no credential defaults are shipped.
    storage_endpoint: str = ""
    storage_bucket: str = ""
    storage_access_key_id: str = ""
    storage_secret_access_key: str = ""
    storage_region: str = ""
    # Legacy B2 key layout, kept so existing DB object keys resolve
    storage_key_prefix: str = "csi_youth_"

    # Pagination defaults
    default_page_size: int = 50

    # Application timezone for date/time captures and display
    app_timezone: str = "Asia/Kolkata"

    # Email / notification settings
    mail_sender: Optional[str] = None
    resend_api_key: Optional[str] = None
    admin_notification_email: Optional[str] = None

    # OCR.space (optional PDF payment amount detection)
    ocr_space_api_key: Optional[str] = None

    # Redis shared cache (multi-worker). Leave unset for in-memory local fallback.
    redis_url: Optional[str] = None

    # Observability (Urgentry + OTel). Unset = local quiet / no export.
    environment: str = "development"
    sentry_dsn: Optional[str] = None
    sentry_traces_sample_rate: float = 0.0
    sentry_release: Optional[str] = None
    otel_exporter_otlp_endpoint: Optional[str] = None
    otel_service_name: str = "csi-api"
    otel_resource_attributes: Optional[str] = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

