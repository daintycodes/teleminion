"""
TeleMinion V2 Download Worker

Background worker that processes the download queue.
Features: Queue persistence, bucket routing, retry logic, n8n webhook.
"""
import asyncio
import hashlib
import logging
import os
from datetime import datetime
from typing import Optional

import aiofiles
import httpx
from telethon import TelegramClient
from telethon.errors import FloodWaitError

from .config import settings, get_bucket_for_category, CATEGORIES
from .database import (
    get_file_by_id, 
    update_file_status, 
    increment_retry_count,
    get_queued_file_ids,
    reset_downloading_files,
    check_content_hash_exists
)
from .models import FileStatus, WebhookPayload

logger = logging.getLogger(__name__)


async def calculate_content_hash(file_path: str) -> str:
    """Calculate SHA-256 hash of file content."""
    sha256 = hashlib.sha256()
    async with aiofiles.open(file_path, 'rb') as f:
        while chunk := await f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


async def notify_webhook(payload: WebhookPayload) -> bool:
    """Send webhook notification to n8n after successful upload."""
    if not settings.PROCESSING_WEBHOOK_URL:
        logger.debug("No webhook URL configured, skipping notification")
        return True
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                settings.PROCESSING_WEBHOOK_URL,
                json=payload.model_dump(),
                headers={"Content-Type": "application/json"}
            )
            if response.status_code == 200:
                logger.info(f"Webhook notification sent for file {payload.file_id}")
                return True
            else:
                logger.warning(f"Webhook returned status {response.status_code}")
                return False
    except Exception as e:
        logger.error(f"Failed to send webhook notification: {e}")
        return False


async def download_and_upload_file(
    telegram_client: TelegramClient,
    minio_client,
    pool,
    file_id: int
) -> bool:
    """
    Download a file from Telegram and upload to MinIO.
    Returns True on success, False on failure.
    """
    file_data = await get_file_by_id(pool, file_id)
    if not file_data:
        logger.error(f"File {file_id} not found in database")
        return False
    
    # Check if category is assigned
    category = file_data.get('destination_category')
    if not category or category not in CATEGORIES:
        logger.error(f"File {file_id} has invalid category: {category}")
        await update_file_status(
            pool, file_id, FileStatus.FAILED,
            error_message="No valid category assigned"
        )
        return False
    
    bucket = get_bucket_for_category(category)
    channel_id = file_data['channel_id']
    message_id = file_data['message_id']
    file_name = file_data.get('file_name', f"file_{message_id}")
    
    # Create safe filename
    safe_name = "".join(c if c.isalnum() or c in '.-_' else '_' for c in file_name)
    local_path = os.path.join(settings.DOWNLOAD_PATH, f"{file_id}_{safe_name}")
    
    try:
        # Update status to DOWNLOADING
        await update_file_status(pool, file_id, FileStatus.DOWNLOADING)
        logger.info(f"Downloading file {file_id}: {file_name}")
        
        # Get the channel entity first (required for entity resolution after restart)
        # Get the channel entity first (required for entity resolution after restart)
        try:
            # 1. Try direct ID resolution
            entity = await telegram_client.get_entity(channel_id)
        except Exception as e1:
            logger.warning(f"Direct entity resolution failed for {channel_id}: {e1}")
            
            # 2. Try PeerChannel
            try:
                from telethon.tl.types import PeerChannel
                entity = await telegram_client.get_entity(PeerChannel(channel_id))
            except Exception as e2:
                logger.warning(f"PeerChannel resolution failed for {channel_id}: {e2}")
                
                # 3. Try Username if available
                username = file_data.get('channel_username')
                if username:
                    try:
                        logger.info(f"Trying resolution by username: {username}")
                        entity = await telegram_client.get_entity(username)
                    except Exception as e3:
                        logger.error(f"Username resolution failed for {username}: {e3}")
                        raise ValueError(f"Cannot resolve channel {channel_id} (username: {username})")
                else:
                    raise ValueError(f"Cannot resolve channel {channel_id} and no username available")
        
        # Get the message from Telegram using the resolved entity
        try:
            message = await telegram_client.get_messages(entity, ids=message_id)
            if not message or not message.media:
                raise ValueError("Message not found or has no media")
        except FloodWaitError as e:
            logger.warning(f"FloodWait: sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
            message = await telegram_client.get_messages(entity, ids=message_id)
        
        # Download to local temp
        await telegram_client.download_media(message, local_path)
        
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Downloaded file not found: {local_path}")
        
        # Calculate content hash for deduplication
        content_hash = await calculate_content_hash(local_path)
        
        # Check for duplicate
        existing_id = await check_content_hash_exists(pool, content_hash)
        if existing_id and existing_id != file_id:
            logger.warning(f"File {file_id} is duplicate of {existing_id}")
            os.remove(local_path)
            await update_file_status(
                pool, file_id, FileStatus.FAILED_PERMANENT,
                error_message=f"Duplicate of file {existing_id}",
                content_hash=content_hash
            )
            return False
        
        # Update status to UPLOADING
        await update_file_status(
            pool, file_id, FileStatus.UPLOADING,
            content_hash=content_hash
        )
        logger.info(f"Uploading file {file_id} to bucket: {bucket}")
        
        # Upload to MinIO
        file_size = os.path.getsize(local_path)
        minio_path = f"{channel_id}/{message_id}/{safe_name}"
        
        # Ensure bucket exists
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)
        
        minio_client.fput_object(
            bucket,
            minio_path,
            local_path,
            content_type=file_data.get('mime_type', 'application/octet-stream')
        )
        
        # Clean up local file
        os.remove(local_path)
        
        # Update status to COMPLETED
        full_path = f"{bucket}/{minio_path}"
        await update_file_status(
            pool, file_id, FileStatus.COMPLETED,
            minio_path=full_path,
            content_hash=content_hash
        )
        logger.info(f"File {file_id} uploaded successfully to {full_path}")
        
        # Send webhook notification to n8n
        payload = WebhookPayload(
            file_id=file_id,
            file_name=file_name,
            file_type=file_data.get('file_type'),
            mime_type=file_data.get('mime_type'),
            file_size=file_size,
            minio_path=minio_path,
            minio_bucket=bucket,
            category=category,
            channel_id=channel_id,
            channel_name=file_data.get('channel_name'),
            content_hash=content_hash
        )
        await notify_webhook(payload)
        
        return True
        
    except FloodWaitError as e:
        logger.warning(f"FloodWait for file {file_id}: {e.seconds}s")
        await update_file_status(
            pool, file_id, FileStatus.QUEUED,
            error_message=f"FloodWait: retry after {e.seconds}s"
        )
        await asyncio.sleep(e.seconds)
        return False
        
    except Exception as e:
        logger.error(f"Failed to process file {file_id}: {e}")
        
        # Increment retry count
        retry_count = await increment_retry_count(pool, file_id)
        
        # Check if max retries exceeded
        if retry_count >= settings.MAX_RETRY_COUNT:
            await update_file_status(
                pool, file_id, FileStatus.FAILED_PERMANENT,
                error_message=f"Max retries exceeded: {str(e)}"
            )
            logger.error(f"File {file_id} marked as FAILED_PERMANENT after {retry_count} retries")
        else:
            await update_file_status(
                pool, file_id, FileStatus.FAILED,
                error_message=str(e)
            )
        
        # Clean up temp file if exists
        if os.path.exists(local_path):
            os.remove(local_path)
        
        return False


async def recover_queue(pool, download_queue: asyncio.Queue):
    """
    Recover queued files from database on startup.
    Called during app lifespan initialization.
    """
    # Reset stuck files
    await reset_downloading_files(pool)
    
    # Get queued file IDs
    queued_ids = await get_queued_file_ids(pool)
    
    if queued_ids:
        logger.info(f"Recovering {len(queued_ids)} queued files")
        for file_id in queued_ids:
            await download_queue.put(file_id)


async def download_worker(
    telegram_client: TelegramClient,
    minio_client,
    pool,
    download_queue: asyncio.Queue
):
    """
    Background worker that processes the download queue.
    Runs continuously, processing one file at a time.
    """
    logger.info("Download worker started")
    
    while True:
        try:
            # Circuit Breaker: Check connection before processing
            if not telegram_client.is_connected():
                logger.warning("Worker paused: Telegram client disconnected. Waiting 5s...")
                await asyncio.sleep(5)
                try:
                    await telegram_client.connect()
                except Exception as e:
                    logger.error(f"Worker failed to reconnect: {e}")
                continue

            # Wait for a file ID from the queue
            file_id = await download_queue.get()
            logger.info(f"Processing file {file_id} from queue")
            
            try:
                await download_and_upload_file(
                    telegram_client,
                    minio_client,
                    pool,
                    file_id
                )
            except ConnectionError:
                logger.error(f"Connection lost while processing file {file_id}. Re-queueing.")
                await download_queue.put(file_id) # Put back in queue
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error processing file {file_id}: {e}")
            finally:
                download_queue.task_done()
            
            # Small delay between files
            await asyncio.sleep(1)
            
        except asyncio.CancelledError:
            logger.info("Download worker cancelled")
            break
        except Exception as e:
            logger.error(f"Download worker error: {e}")
            await asyncio.sleep(5)
    
    logger.info("Download worker stopped")
