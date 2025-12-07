"""
TeleMinion V2 Configuration

All application settings loaded from environment variables.
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment variables."""
    
    # ==========================================================================
    # Database
    # ==========================================================================
    DATABASE_URL: str = "postgresql://teleminio:teleminio@postgres:5432/teleminio"
    
    # ==========================================================================
    # Telegram
    # ==========================================================================
    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: str
    TELEGRAM_PHONE: str
    TELEGRAM_SESSION_NAME: str = "teleminio"
    
    # ==========================================================================
    # Primary MinIO (File Storage)
    # ==========================================================================
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    
    # ==========================================================================
    # Backup MinIO (Separate Instance for Backups)
    # ==========================================================================
    BACKUP_MINIO_ENDPOINT: Optional[str] = None
    BACKUP_MINIO_ACCESS_KEY: Optional[str] = None
    BACKUP_MINIO_SECRET_KEY: Optional[str] = None
    BACKUP_MINIO_BUCKET: str = "teleminio-backups"
    BACKUP_MINIO_SECURE: bool = True
    
    # ==========================================================================
    # Authentication
    # ==========================================================================
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD_HASH: str = ""  # bcrypt hash
    SESSION_SECRET_KEY: str = "change-me-to-a-random-32-char-string"
    SESSION_EXPIRE_HOURS: int = 24
    
    # ==========================================================================
    # Self-Healing
    # ==========================================================================
    SELF_HEALING_INTERVAL_SECONDS: int = 3600  # Default: hourly
    MAX_RETRY_COUNT: int = 3  # After this, mark as FAILED_PERMANENT
    
    # ==========================================================================
    # n8n Integration
    # ==========================================================================
    PROCESSING_WEBHOOK_URL: Optional[str] = None  # e.g., https://n8n.example.com/webhook/teleminio
    
    # ==========================================================================
    # External Services (for reference/n8n)
    # ==========================================================================
    WEAVIATE_URL: Optional[str] = None  # e.g., http://weaviate:8080
    WHISPER_URL: Optional[str] = None   # e.g., http://whisper:9000
    
    # ==========================================================================
    # Scanner
    # ==========================================================================
    SCAN_INTERVAL: int = 60  # seconds
    DOWNLOAD_PATH: str = "/tmp/downloads"
    
    # ==========================================================================
    # Logging
    # ==========================================================================
    LOG_LEVEL: str = "INFO"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# ==========================================================================
# Category Configuration
# ==========================================================================
CATEGORIES = {
    "messages": {
        "bucket": "bucket-messages",
        "label": "Messages",
        "description": "Audio messages and sermons"
    },
    "songs": {
        "bucket": "bucket-songs",
        "label": "Songs",
        "description": "Worship songs and music"
    },
    "ror": {
        "bucket": "bucket-ror",
        "label": "Rhapsody of Realities",
        "description": "Daily devotional PDFs"
    },
    "books": {
        "bucket": "bucket-books",
        "label": "Books",
        "description": "PDF books and documents"
    },
}

# Default category per MIME type pattern
MIME_DEFAULTS = {
    "audio": "messages",  # Audio defaults to Messages
    "pdf": "ror",         # PDF defaults to Rhapsody of Realities
}

# Allowed category options per MIME type
MIME_CATEGORY_OPTIONS = {
    "audio": ["messages", "songs"],
    "pdf": ["ror", "books"],
}

# All MinIO buckets to create
ALL_BUCKETS = [cat["bucket"] for cat in CATEGORIES.values()]


def get_category_for_mime(mime_type: str) -> str:
    """Get default category based on MIME type."""
    if mime_type and mime_type.startswith("audio/"):
        return MIME_DEFAULTS["audio"]
    elif mime_type == "application/pdf":
        return MIME_DEFAULTS["pdf"]
    return None  # Unsupported type


def get_category_options(mime_type: str) -> list:
    """Get allowed category options for a MIME type."""
    if mime_type and mime_type.startswith("audio/"):
        return MIME_CATEGORY_OPTIONS["audio"]
    elif mime_type == "application/pdf":
        return MIME_CATEGORY_OPTIONS["pdf"]
    return []


def get_bucket_for_category(category: str) -> str:
    """Get MinIO bucket name for a category."""
    if category in CATEGORIES:
        return CATEGORIES[category]["bucket"]
    raise ValueError(f"Unknown category: {category}")


# Global settings instance
settings = Settings()
