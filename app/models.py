"""
Pydantic models for TeleMinion.
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from enum import Enum


class FileStatus(str, Enum):
    """File processing status."""
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FileType(str, Enum):
    """Supported file types."""
    AUDIO = "audio"
    PDF = "pdf"
    DOCUMENT = "document"


class FileBase(BaseModel):
    """Base file model."""
    channel_id: int
    message_id: int
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    mime_type: Optional[str] = None


class FileCreate(FileBase):
    """Model for creating a new file record."""
    pass


class FileResponse(FileBase):
    """File response model."""
    id: int
    status: FileStatus = FileStatus.PENDING
    minio_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    channel_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class ChannelBase(BaseModel):
    """Base channel model."""
    id: int
    name: Optional[str] = None
    username: Optional[str] = None


class ChannelCreate(BaseModel):
    """Model for adding a channel."""
    identifier: str  # Can be username, invite link, or channel ID


class ChannelResponse(ChannelBase):
    """Channel response model."""
    last_scanned_message_id: int = 0
    is_active: bool = True
    added_at: datetime
    file_count: Optional[int] = None
    
    class Config:
        from_attributes = True


class PaginatedResponse(BaseModel):
    """Paginated response wrapper."""
    items: list
    total: int
    page: int
    per_page: int
    total_pages: int
    has_next: bool
    has_prev: bool


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    telegram_connected: bool
    database_connected: bool
    minio_connected: bool


class AuthRequest(BaseModel):
    """Telegram auth code request."""
    code: str
    phone_code_hash: Optional[str] = None


class AuthStatus(BaseModel):
    """Telegram auth status."""
    authenticated: bool
    phone_registered: bool = False
    awaiting_code: bool = False
    phone_code_hash: Optional[str] = None
