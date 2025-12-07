"""
TeleMinion V2 Backup Task

Daily backup of PostgreSQL database to separate MinIO instance.
Features: Automatic backup, 7-day retention, configurable target.
"""
import asyncio
import logging
import os
import subprocess
from datetime import datetime, timedelta
from typing import Optional

from minio import Minio
import aiofiles

from .config import settings

logger = logging.getLogger(__name__)


def create_backup_minio_client() -> Optional[Minio]:
    """
    Create MinIO client for backup storage.
    Returns None if backup MinIO is not configured.
    """
    if not settings.BACKUP_MINIO_ENDPOINT:
        logger.info("Backup MinIO not configured")
        return None
    
    return Minio(
        settings.BACKUP_MINIO_ENDPOINT,
        access_key=settings.BACKUP_MINIO_ACCESS_KEY,
        secret_key=settings.BACKUP_MINIO_SECRET_KEY,
        secure=settings.BACKUP_MINIO_SECURE
    )


async def run_pg_dump() -> Optional[str]:
    """
    Run pg_dump and return the path to the backup file.
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_name = f"teleminio_backup_{timestamp}.sql"
    backup_path = os.path.join(settings.DOWNLOAD_PATH, backup_name)
    
    # Parse DATABASE_URL for pg_dump
    # Format: postgresql://user:password@host:port/database
    db_url = settings.DATABASE_URL
    
    try:
        # Use pg_dump with DATABASE_URL
        env = os.environ.copy()
        env['PGPASSWORD'] = db_url.split(':')[2].split('@')[0]  # Extract password
        
        # Build connection string
        user = db_url.split('://')[1].split(':')[0]
        host = db_url.split('@')[1].split(':')[0]
        port = db_url.split('@')[1].split(':')[1].split('/')[0]
        dbname = db_url.split('/')[-1]
        
        cmd = [
            'pg_dump',
            '-h', host,
            '-p', port,
            '-U', user,
            '-d', dbname,
            '-f', backup_path,
            '--no-password'
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"pg_dump failed: {stderr.decode()}")
            return None
        
        logger.info(f"pg_dump completed: {backup_path}")
        return backup_path
        
    except Exception as e:
        logger.error(f"pg_dump error: {e}")
        return None


async def upload_backup_to_minio(
    backup_client: Minio,
    backup_path: str
) -> bool:
    """
    Upload backup file to backup MinIO instance.
    """
    try:
        bucket = settings.BACKUP_MINIO_BUCKET
        
        # Ensure bucket exists
        if not backup_client.bucket_exists(bucket):
            backup_client.make_bucket(bucket)
        
        # Upload file
        object_name = os.path.basename(backup_path)
        backup_client.fput_object(
            bucket,
            object_name,
            backup_path,
            content_type="application/sql"
        )
        
        logger.info(f"Backup uploaded to {bucket}/{object_name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to upload backup: {e}")
        return False


async def cleanup_old_backups(
    backup_client: Minio,
    retention_days: int = 7
):
    """
    Remove backups older than retention period.
    """
    try:
        bucket = settings.BACKUP_MINIO_BUCKET
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
        
        objects = backup_client.list_objects(bucket)
        deleted = 0
        
        for obj in objects:
            if obj.last_modified.replace(tzinfo=None) < cutoff_date:
                backup_client.remove_object(bucket, obj.object_name)
                logger.info(f"Deleted old backup: {obj.object_name}")
                deleted += 1
        
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old backups")
            
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


async def backup_task():
    """
    Daily backup task.
    
    - Creates pg_dump of database
    - Uploads to backup MinIO instance
    - Cleans up local temp file
    - Removes backups older than 7 days
    """
    backup_client = create_backup_minio_client()
    
    if not backup_client:
        logger.info("Backup task not running - backup MinIO not configured")
        return
    
    logger.info("Backup task started (daily at midnight UTC)")
    
    while True:
        try:
            # Calculate time until next midnight UTC
            now = datetime.utcnow()
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            seconds_until_midnight = (tomorrow - now).total_seconds()
            
            logger.debug(f"Next backup in {seconds_until_midnight:.0f} seconds")
            await asyncio.sleep(seconds_until_midnight)
            
            logger.info("Starting daily backup...")
            
            # Run pg_dump
            backup_path = await run_pg_dump()
            
            if backup_path and os.path.exists(backup_path):
                # Upload to backup MinIO
                success = await upload_backup_to_minio(backup_client, backup_path)
                
                # Cleanup local file
                os.remove(backup_path)
                
                if success:
                    # Cleanup old backups
                    await cleanup_old_backups(backup_client)
                    logger.info("Daily backup completed successfully")
                else:
                    logger.error("Daily backup failed to upload")
            else:
                logger.error("Daily backup failed - pg_dump error")
            
        except asyncio.CancelledError:
            logger.info("Backup task cancelled")
            break
        except Exception as e:
            logger.error(f"Backup task error: {e}")
            await asyncio.sleep(3600)  # Wait 1 hour before retry
    
    logger.info("Backup task stopped")
