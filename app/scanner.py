"""
Channel scanner for TeleMinion.
Background task that scans Telegram channels for new files.
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime

from telethon import TelegramClient
from telethon.tl.types import (
    Message, 
    DocumentAttributeFilename,
    DocumentAttributeAudio
)
from telethon.errors import (
    FloodWaitError, 
    ChannelPrivateError,
    ChatAdminRequiredError
)

from .config import settings
from .database import (
    get_channels, 
    insert_file, 
    update_channel_last_scanned
)

logger = logging.getLogger(__name__)

# Supported MIME types
AUDIO_MIME_TYPES = {
    'audio/mpeg', 'audio/mp3', 'audio/ogg', 'audio/wav',
    'audio/flac', 'audio/aac', 'audio/m4a', 'audio/x-m4a'
}

PDF_MIME_TYPE = 'application/pdf'


def get_file_info(message: Message) -> Optional[dict]:
    """
    Extract file information from a Telegram message.
    Returns None if the message doesn't contain a supported file.
    """
    if not message.document:
        return None
    
    doc = message.document
    mime_type = doc.mime_type or ''
    
    # Determine file type
    file_type = None
    if mime_type in AUDIO_MIME_TYPES:
        file_type = 'audio'
    elif mime_type == PDF_MIME_TYPE:
        file_type = 'pdf'
    elif mime_type.startswith('audio/'):
        file_type = 'audio'
    else:
        # Not a supported file type
        return None
    
    # Get file name
    file_name = None
    for attr in doc.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            file_name = attr.file_name
            break
        elif isinstance(attr, DocumentAttributeAudio):
            # Use title if available for audio
            if attr.title:
                performer = attr.performer or "Unknown"
                file_name = f"{performer} - {attr.title}"
    
    if not file_name:
        # Generate name from message ID
        ext = '.mp3' if file_type == 'audio' else '.pdf'
        file_name = f"file_{message.id}{ext}"
    
    return {
        'channel_id': message.chat_id,
        'message_id': message.id,
        'file_name': file_name,
        'file_size': doc.size,
        'file_type': file_type,
        'mime_type': mime_type
    }


async def scan_channel(client: TelegramClient, pool, channel: dict) -> int:
    """
    Scan a single channel for new files.
    Returns the number of new files found.
    """
    channel_id = channel['id']
    last_scanned = channel.get('last_scanned_message_id', 0)
    new_files = 0
    last_message_id = last_scanned
    
    try:
        logger.info(f"Scanning channel {channel_id} from message {last_scanned}")
        
        async for message in client.iter_messages(
            channel_id,
            min_id=last_scanned,
            reverse=True,  # Oldest first
            limit=500  # Limit per scan
        ):
            if message.id > last_message_id:
                last_message_id = message.id
            
            file_info = get_file_info(message)
            if file_info:
                file_id = await insert_file(pool, file_info)
                if file_id:  # New file was inserted
                    new_files += 1
                    logger.debug(f"Found new file: {file_info['file_name']}")
        
        # Update last scanned message ID
        if last_message_id > last_scanned:
            await update_channel_last_scanned(pool, channel_id, last_message_id)
            logger.info(f"Updated last scanned for channel {channel_id} to {last_message_id}")
        
    except FloodWaitError as e:
        logger.warning(f"FloodWait for channel {channel_id}: sleeping {e.seconds}s")
        await asyncio.sleep(e.seconds)
        
    except ChannelPrivateError:
        logger.error(f"Channel {channel_id} is private or we're not a member")
        
    except ChatAdminRequiredError:
        logger.error(f"Admin rights required for channel {channel_id}")
        
    except Exception as e:
        logger.error(f"Error scanning channel {channel_id}: {e}")
    
    return new_files


async def channel_scanner(client: TelegramClient, pool):
    """
    Background task that continuously scans channels for new files.
    This is the discovery phase - files are inserted with PENDING status.
    """
    logger.info("Channel scanner started")
    
    while True:
        try:
            # Get all active channels
            channels = await get_channels(pool, active_only=True)
            
            if not channels:
                logger.debug("No channels to scan")
            else:
                total_new_files = 0
                
                for channel in channels:
                    new_files = await scan_channel(client, pool, channel)
                    total_new_files += new_files
                    
                    # Small delay between channels to avoid rate limits
                    await asyncio.sleep(2)
                
                if total_new_files > 0:
                    logger.info(f"Scan complete: found {total_new_files} new files")
            
            # Wait before next scan cycle
            await asyncio.sleep(settings.SCAN_INTERVAL)
            
        except asyncio.CancelledError:
            logger.info("Channel scanner stopped")
            break
            
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            await asyncio.sleep(30)  # Wait before retry


async def scan_channel_now(client: TelegramClient, pool, channel_id: int) -> int:
    """
    Manually trigger a scan for a specific channel.
    Returns number of new files found.
    """
    from .database import get_channel_by_id
    
    channel = await get_channel_by_id(pool, channel_id)
    if not channel:
        raise ValueError(f"Channel {channel_id} not found")
    
    return await scan_channel(client, pool, channel)
