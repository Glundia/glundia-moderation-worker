"""Configuration for the moderation worker."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    # GCP
    gcp_project: str
    pubsub_subscription: str
    environment: str = "dev"

    # Storage buckets
    quarantine_bucket: str
    public_bucket: str
    rejected_bucket: str

    # Database
    database_url: str

    # Moderation
    vision_api_threshold: float = 0.7

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
