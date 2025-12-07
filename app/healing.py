"""
TeleMinion V2 Self-Healing Task

Background task that verifies completed files exist in MinIO.
If a file is missing, reverts its status to PENDING for re-download.
"""
import asyncio
import logging
from datetime import datetime

from .config import settings, CATEGORIES
from .database import (
    get_completed_files_for_healing,
    revert_file_to_pending
)

logger = logging.getLogger(__name__)


async def check_file_exists_in_minio(minio_client, bucket: str, object_path: str) -> bool:
    """
    Check if an object exists in MinIO.
    """
    try:
        minio_client.stat_object(bucket, object_path)
        return True
    except Exception:
        return False


async def self_healing_task(pool, minio_client):
    """
    Background task that periodically checks completed files in MinIO.
    
    - Runs at configurable interval (default: hourly)
    - Checks COMPLETED files exist in their MinIO bucket
    - Reverts missing files to PENDING for re-download
    - Skips FAILED_PERMANENT files
    """
    interval = settings.SELF_HEALING_INTERVAL_SECONDS
    logger.info(f"Self-healing task started (interval: {interval}s)")
    
    while True:
        try:
            # Wait for interval
            await asyncio.sleep(interval)
            
            logger.info("Starting self-healing check...")
            start_time = datetime.utcnow()
            
            # Get completed files
            files = await get_completed_files_for_healing(pool)
            
            if not files:
                logger.debug("No completed files to check")
                continue
            
            checked = 0
            missing = 0
            
            for file in files:
                file_id = file['id']
                minio_path = file.get('minio_path')
                category = file.get('destination_category')
                
                if not minio_path or not category:
                    continue
                
                # Parse bucket and object path from minio_path
                # Format: bucket-name/channel_id/message_id/filename
                try:
                    parts = minio_path.split('/', 1)
                    if len(parts) != 2:
                        continue
                    
                    bucket = parts[0]
                    object_path = parts[1]
                    
                    # Check if file exists
                    exists = await check_file_exists_in_minio(
                        minio_client, bucket, object_path
                    )
                    
                    checked += 1
                    
                    if not exists:
                        logger.warning(f"File {file_id} missing from MinIO: {minio_path}")
                        await revert_file_to_pending(pool, file_id)
                        missing += 1
                        
                except Exception as e:
                    logger.error(f"Error checking file {file_id}: {e}")
            
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            logger.info(
                f"Self-healing complete: checked {checked} files, "
                f"found {missing} missing ({elapsed:.1f}s)"
            )
            
        except asyncio.CancelledError:
            logger.info("Self-healing task cancelled")
            break
        except Exception as e:
            logger.error(f"Self-healing error: {e}")
            await asyncio.sleep(60)  # Wait before retry
    
    logger.info("Self-healing task stopped")
