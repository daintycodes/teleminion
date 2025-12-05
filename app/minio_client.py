"""
MinIO client operations for TeleMinion.
Handles bucket creation and file uploads.
"""
import logging
from pathlib import Path
from typing import Optional

from minio import Minio
from minio.error import S3Error

from .config import settings

logger = logging.getLogger(__name__)


def get_minio_client() -> Minio:
    """Create and return MinIO client."""
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE
    )


async def init_minio_buckets(client: Optional[Minio] = None):
    """Initialize MinIO buckets if they don't exist."""
    if client is None:
        client = get_minio_client()
    
    buckets = [settings.PDF_BUCKET, settings.AUDIO_BUCKET]
    
    for bucket in buckets:
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
                logger.info(f"Created MinIO bucket: {bucket}")
            else:
                logger.debug(f"MinIO bucket exists: {bucket}")
        except S3Error as e:
            logger.error(f"Error creating bucket {bucket}: {e}")
            raise


def get_bucket_for_file_type(file_type: str) -> str:
    """Determine which bucket to use based on file type."""
    if file_type == "audio":
        return settings.AUDIO_BUCKET
    elif file_type == "pdf":
        return settings.PDF_BUCKET
    else:
        # Default to PDF bucket for other documents
        return settings.PDF_BUCKET


def upload_file(
    client: Minio,
    file_path: Path,
    bucket: str,
    object_name: str,
    content_type: Optional[str] = None
) -> str:
    """
    Upload a file to MinIO.
    
    Args:
        client: MinIO client
        file_path: Local path to the file
        bucket: Target bucket name
        object_name: Object name in the bucket
        content_type: Optional content type
    
    Returns:
        The full path in format "bucket/object_name"
    """
    try:
        client.fput_object(
            bucket,
            object_name,
            str(file_path),
            content_type=content_type
        )
        minio_path = f"{bucket}/{object_name}"
        logger.info(f"Uploaded file to MinIO: {minio_path}")
        return minio_path
    except S3Error as e:
        logger.error(f"Error uploading to MinIO: {e}")
        raise


def delete_file(client: Minio, bucket: str, object_name: str):
    """Delete a file from MinIO."""
    try:
        client.remove_object(bucket, object_name)
        logger.info(f"Deleted from MinIO: {bucket}/{object_name}")
    except S3Error as e:
        logger.error(f"Error deleting from MinIO: {e}")
        raise


def check_minio_connection(client: Optional[Minio] = None) -> bool:
    """Check if MinIO is accessible."""
    if client is None:
        client = get_minio_client()
    
    try:
        # Try to list buckets as a health check
        client.list_buckets()
        return True
    except Exception as e:
        logger.error(f"MinIO connection check failed: {e}")
        return False
