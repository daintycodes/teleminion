"""
TeleMinion V2 Models

Pydantic models and enums for data structures.
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel


class FileStatus(str, Enum):
    """Status of a file in the download pipeline."""
    PENDING = "PENDING"              # Discovered, awaiting approval
    QUEUED = "QUEUED"                # Approved, in download queue
    DOWNLOADING = "DOWNLOADING"      # Being downloaded from Telegram
    UPLOADING = "UPLOADING"          # Being uploaded to MinIO
    COMPLETED = "COMPLETED"          # Successfully stored in MinIO
    FAILED = "FAILED"                # Failed, can retry
    FAILED_PERMANENT = "FAILED_PERMANENT"  # Failed 3+ times, won't retry


class ProcessingStatus(str, Enum):
    """Status of file processing for RAG pipeline."""
    PENDING_PROCESSING = "PENDING_PROCESSING"  # Awaiting n8n processing
    PROCESSING = "PROCESSING"                   # Currently being processed
    PROCESSED = "PROCESSED"                     # Successfully processed
    PROCESSING_FAILED = "PROCESSING_FAILED"     # Processing failed


class FileType(str, Enum):
    """Supported file types."""
    AUDIO = "audio"
    PDF = "pdf"


class Category(str, Enum):
    """File destination categories."""
    MESSAGES = "messages"
    SONGS = "songs"
    ROR = "ror"
    BOOKS = "books"


class File(BaseModel):
    """File model representing a discovered/downloaded file."""
    id: int
    channel_id: int
    message_id: int
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    mime_type: Optional[str] = None
    status: FileStatus = FileStatus.PENDING
    destination_category: Optional[str] = None
    minio_path: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    content_hash: Optional[str] = None
    processing_status: ProcessingStatus = ProcessingStatus.PENDING_PROCESSING
    transcript_available: bool = False
    chunk_count: int = 0
    processed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    # Joined fields
    channel_name: Optional[str] = None
    channel_username: Optional[str] = None
    
    class Config:
        from_attributes = True


class Channel(BaseModel):
    """Telegram channel model."""
    id: int
    name: Optional[str] = None
    username: Optional[str] = None
    is_active: bool = True
    last_scanned_message_id: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class PaginatedResponse(BaseModel):
    """Paginated response wrapper."""
    items: List
    total: int
    page: int
    per_page: int
    total_pages: int
    has_next: bool
    has_prev: bool


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    database: bool
    telegram: bool
    minio: bool
    backup_minio: Optional[bool] = None


class AuthRequest(BaseModel):
    """Login request."""
    username: str
    password: str


class AuthStatus(BaseModel):
    """Authentication status."""
    authenticated: bool
    awaiting_code: bool = False
    awaiting_2fa: bool = False
    error: Optional[str] = None


class BatchApprovalItem(BaseModel):
    """Single file in batch approval."""
    file_id: int
    category: str


class BatchApprovalRequest(BaseModel):
    """Batch approval request with category assignments."""
    assignments: List[BatchApprovalItem]


class FileGroupSummary(BaseModel):
    """Summary of files grouped by type for batch modal."""
    file_type: str
    count: int
    file_ids: List[int]
    default_category: str
    category_options: List[str]


class WebhookPayload(BaseModel):
    """Payload sent to n8n webhook after file upload."""
    event: str = "file_uploaded"
    file_id: int
    file_name: Optional[str]
    file_type: Optional[str]
    mime_type: Optional[str]
    file_size: Optional[int]
    minio_path: str
    minio_bucket: str
    category: str
    channel_id: int
    channel_name: Optional[str]
    content_hash: Optional[str]


class MarkProcessedRequest(BaseModel):
    """Request from n8n to mark file as processed."""
    has_transcript: bool = False
    chunk_count: int = 0
    processing_status: str = "PROCESSED"
    error_message: Optional[str] = None
