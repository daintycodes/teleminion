"""
Configuration settings for TeleMinion application.
Uses Pydantic Settings for environment variable management.
"""
from pydantic_settings import BaseSettings
from pathlib import Path
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Database
    DATABASE_URL: str = "postgresql://teleminio:teleminio@postgres:5432/teleminio"
    
    # Telegram API
    TELEGRAM_API_ID: int = 0
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_PHONE: str = ""
    TELEGRAM_SESSION_NAME: str = "teleminio"
    
    # MinIO
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    
    # Application
    DOWNLOAD_PATH: Path = Path("/tmp/downloads")
    SCAN_INTERVAL: int = 60  # seconds
    LOG_LEVEL: str = "INFO"
    
    # Buckets
    PDF_BUCKET: str = "pdf-storage"
    AUDIO_BUCKET: str = "audio-storage"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
