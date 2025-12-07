"""
TeleMinion V2 Channel Scanner

Background task that scans Telegram channels for new files.
Features: MIME type detection, auto-category assignment, deduplication.
"""
import asyncio
import logging
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError, ChannelPrivateError
from telethon.tl.types import (
    DocumentAttributeFilename,
    DocumentAttributeAudio,
    MessageMediaDocument
)

from .config import settings, get_category_for_mime
from .database import (
    get_active_channels,
    update_channel_last_scanned,
    insert_file
)

logger = logging.getLogger(__name__)


def get_file_info(message) -> Optional[dict]:
    """
    Extract file information from a Telegram message.
    Returns None if not a supported file type (audio/PDF only).
    """
    if not message.media or not isinstance(message.media, MessageMediaDocument):
        return None
    
    document = message.media.document
    if not document:
        return None
    
    # Get MIME type
    mime_type = document.mime_type or ""
    
    # Check if supported type
    is_audio = mime_type.startswith("audio/")
    is_pdf = mime_type == "application/pdf"
    
    if not is_audio and not is_pdf:
        # Skip unsupported file types
        return None
    
    # Get file type
    file_type = "audio" if is_audio else "pdf"
    
    # Get filename
    file_name = None
    is_voice = False
    duration = None
    
    for attr in document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            file_name = attr.file_name
        elif isinstance(attr, DocumentAttributeAudio):
            is_voice = attr.voice
            duration = attr.duration
    
    # Generate filename if not present
    if not file_name:
        ext = "mp3" if is_audio else "pdf"
        file_name = f"{file_type}_{message.id}.{ext}"
    
    # Get default category based on MIME type
    default_category = get_category_for_mime(mime_type)
    
    return {
        "file_name": file_name,
        "file_size": document.size,
        "file_type": file_type,
        "mime_type": mime_type,
        "is_voice": is_voice,
        "duration": duration,
        "destination_category": default_category
    }


async def scan_channel(
    client: TelegramClient,
    pool,
    channel_id: int,
    last_message_id: int = 0,
    full_scan: bool = False
) -> tuple[int, int]:
    """
    Scan a channel for new audio/PDF files.
    Returns (new_files_count, last_message_id).
    
    If full_scan=True, scans ALL messages (for initial import).
    Otherwise, only scans messages newer than last_message_id.
    """
    new_files = 0
    max_message_id = last_message_id
    
    # For full scan, don't use min_id filter and increase limit
    scan_limit = None if full_scan else 500  # None = no limit
    min_id = 0 if full_scan else last_message_id
    
    try:
        # Get the channel entity (required for proper entity resolution after restart)
        try:
            # 1. Try direct ID resolution
            entity = await client.get_entity(channel_id)
        except Exception as e1:
            logger.warning(f"Direct entity resolution failed for {channel_id}: {e1}")
            
            # 2. Try PeerChannel
            try:
                from telethon.tl.types import PeerChannel
                entity = await client.get_entity(PeerChannel(channel_id))
            except Exception as e2:
                logger.warning(f"PeerChannel resolution failed for {channel_id}: {e2}")
                
                # 3. Try Username if available (we don't have username in args, need to fetch from DB)
                # In scanner, we can query the DB for the username
                try:
                    from .database import get_channel_by_id
                    channel_data = await get_channel_by_id(pool, channel_id)
                    username = channel_data.get('username') if channel_data else None
                    
                    if username:
                        logger.info(f"Trying resolution by username: {username}")
                        entity = await client.get_entity(username)
                    else:
                        raise ValueError("No username available")
                except Exception as e3:
                    logger.error(f"Username resolution failed: {e3}")
                    raise ValueError(f"Cannot resolve channel {channel_id}")
        
        logger.info(f"Scanning channel {channel_id} (full_scan={full_scan}, min_id={min_id})")
        
        # Iterate through messages (newest first)
        async for message in client.iter_messages(
            entity,
            limit=scan_limit,
            min_id=min_id
        ):
            max_message_id = max(max_message_id, message.id)
            
            # Extract file info
            file_info = get_file_info(message)
            if not file_info:
                continue
            
            # Insert file record
            file_data = {
                "channel_id": channel_id,
                "message_id": message.id,
                **file_info
            }
            
            file_id = await insert_file(pool, file_data)
            if file_id:
                new_files += 1
                if new_files % 100 == 0:
                    logger.info(f"Progress: {new_files} files discovered...")
        
        logger.info(f"Scan complete: {new_files} audio/PDF files found")
        
    except FloodWaitError as e:
        logger.warning(f"FloodWait scanning channel {channel_id}: {e.seconds}s")
        await asyncio.sleep(e.seconds)
    except ChannelPrivateError:
        logger.error(f"Channel {channel_id} is private or access denied")
    except Exception as e:
        logger.error(f"Error scanning channel {channel_id}: {e}")
    
    return new_files, max_message_id


async def channel_scanner(
    client: TelegramClient,
    pool
):
    """
    Background task that periodically scans all active channels.
    """
    logger.info("Channel scanner started")
    
    while True:
        try:
            # Check if client is authorized
            if not await client.is_user_authorized():
                logger.warning("Telegram client not authorized, skipping scan")
                await asyncio.sleep(settings.SCAN_INTERVAL)
                continue
            
            # Get active channels
            channels = await get_active_channels(pool)
            
            if not channels:
                logger.debug("No active channels to scan")
            else:
                total_new = 0
                for channel in channels:
                    channel_id = channel['id']
                    last_id = channel.get('last_scanned_message_id', 0)
                    
                    logger.debug(f"Scanning channel {channel.get('name', channel_id)}")
                    
                    new_count, max_id = await scan_channel(
                        client, pool, channel_id, last_id
                    )
                    
                    if max_id > last_id:
                        await update_channel_last_scanned(pool, channel_id, max_id)
                    
                    total_new += new_count
                    
                    # Small delay between channels
                    await asyncio.sleep(1)
                
                if total_new > 0:
                    logger.info(f"Scan complete: {total_new} new files discovered")
            
            # Wait for next scan interval
            await asyncio.sleep(settings.SCAN_INTERVAL)
            
        except asyncio.CancelledError:
            logger.info("Channel scanner cancelled")
            break
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            await asyncio.sleep(settings.SCAN_INTERVAL)
    
    logger.info("Channel scanner stopped")
