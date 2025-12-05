"""
Download worker for TeleMinion.
Sequential download processor that handles the QUEUED -> DOWNLOADING -> UPLOADING -> COMPLETED pipeline.
"""
import asyncio
import logging
import traceback
from pathlib import Path
from typing import Optional
import os

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from .config import settings
from .database import get_file_by_id, update_file_status
from .minio_client import get_minio_client, upload_file, get_bucket_for_file_type

logger = logging.getLogger(__name__)


def ensure_download_dir():
    """Ensure download directory exists."""
    settings.DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for filesystem safety."""
    # Remove or replace problematic characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    # Limit length
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200-len(ext)] + ext
    return filename


async def download_file_from_telegram(
    client: TelegramClient,
    channel_id: int,
    message_id: int,
    file_name: str
) -> Path:
    """
    Download a file from Telegram to local temp storage.
    Returns the local file path.
    """
    ensure_download_dir()
    
    # Create unique local filename
    safe_name = sanitize_filename(file_name)
    local_path = settings.DOWNLOAD_PATH / f"{message_id}_{safe_name}"
    
    # Get the message
    message = await client.get_messages(channel_id, ids=message_id)
    
    if not message or not message.document:
        raise ValueError(f"Message {message_id} not found or has no document")
    
    # Download with progress callback (optional logging)
    logger.info(f"Downloading: {file_name}")
    
    downloaded_path = await client.download_media(
        message,
        file=str(local_path)
    )
    
    if not downloaded_path:
        raise ValueError("Download returned None")
    
    return Path(downloaded_path)


async def process_file(app_state, file_id: int):
    """
    Process a single file through the download pipeline.
    
    Pipeline:
    1. QUEUED -> DOWNLOADING (fetch from Telegram)
    2. DOWNLOADING -> UPLOADING (upload to MinIO)
    3. UPLOADING -> COMPLETED (cleanup)
    
    On error: set status to FAILED with error message
    """
    pool = app_state.db_pool
    client = app_state.telegram_client
    minio = get_minio_client()
    
    local_path: Optional[Path] = None
    
    try:
        # Get file info
        file_info = await get_file_by_id(pool, file_id)
        if not file_info:
            logger.error(f"File {file_id} not found in database")
            return
        
        # Check if already processed
        if file_info['status'] not in ('QUEUED', 'DOWNLOADING'):
            logger.warning(f"File {file_id} status is {file_info['status']}, skipping")
            return
        
        file_name = file_info['file_name'] or f"file_{file_id}"
        channel_id = file_info['channel_id']
        message_id = file_info['message_id']
        file_type = file_info['file_type'] or 'pdf'
        
        # Step 1: Update to DOWNLOADING
        await update_file_status(pool, file_id, 'DOWNLOADING')
        logger.info(f"[{file_id}] Starting download: {file_name}")
        
        # Step 2: Download from Telegram
        try:
            local_path = await download_file_from_telegram(
                client, channel_id, message_id, file_name
            )
        except FloodWaitError as e:
            logger.warning(f"FloodWait during download: sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
            # Retry once
            local_path = await download_file_from_telegram(
                client, channel_id, message_id, file_name
            )
        
        logger.info(f"[{file_id}] Downloaded to: {local_path}")
        
        # Step 3: Update to UPLOADING
        await update_file_status(pool, file_id, 'UPLOADING')
        
        # Step 4: Upload to MinIO
        bucket = get_bucket_for_file_type(file_type)
        object_name = f"{channel_id}/{sanitize_filename(file_name)}"
        
        minio_path = upload_file(
            minio,
            local_path,
            bucket,
            object_name,
            content_type=file_info.get('mime_type')
        )
        
        logger.info(f"[{file_id}] Uploaded to MinIO: {minio_path}")
        
        # Step 5: Update to COMPLETED
        await update_file_status(pool, file_id, 'COMPLETED', minio_path=minio_path)
        logger.info(f"[{file_id}] Completed successfully")
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        tb = traceback.format_exc()
        logger.error(f"[{file_id}] Failed: {error_msg}\n{tb}")
        
        await update_file_status(
            pool, file_id, 'FAILED',
            error_message=error_msg
        )
    
    finally:
        # Cleanup: delete local temp file
        if local_path and local_path.exists():
            try:
                local_path.unlink()
                logger.debug(f"Deleted temp file: {local_path}")
            except Exception as e:
                logger.warning(f"Failed to delete temp file {local_path}: {e}")


async def download_worker(app_state):
    """
    Background worker that processes download queue sequentially.
    
    This prevents OOM crashes by processing one file at a time.
    Files are added to app_state.download_queue when user clicks "Approve".
    """
    logger.info("Download worker started")
    queue = app_state.download_queue
    
    while True:
        try:
            # Wait for next file ID from queue
            file_id = await queue.get()
            logger.info(f"Worker picked up file {file_id}")
            
            try:
                await process_file(app_state, file_id)
            finally:
                queue.task_done()
                
        except asyncio.CancelledError:
            logger.info("Download worker stopped")
            break
            
        except Exception as e:
            logger.error(f"Worker error: {e}")
            # Continue processing next item


async def queue_file_for_download(app_state, file_id: int):
    """
    Add a file to the download queue.
    Updates status to QUEUED and adds to async queue.
    """
    pool = app_state.db_pool
    queue = app_state.download_queue
    
    # Update status to QUEUED
    await update_file_status(pool, file_id, 'QUEUED')
    
    # Add to queue
    await queue.put(file_id)
    logger.info(f"File {file_id} queued for download")


async def queue_files_batch(app_state, file_ids: list[int]):
    """Queue multiple files for download."""
    for file_id in file_ids:
        await queue_file_for_download(app_state, file_id)
