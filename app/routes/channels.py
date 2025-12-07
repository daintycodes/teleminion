"""
TeleMinion V2 Channel Routes

Channel management endpoints.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from telethon.errors import UsernameNotOccupiedError, ChannelPrivateError

from ..auth import require_auth
from ..database import (
    get_active_channels,
    get_channel_by_id,
    insert_channel,
    deactivate_channel
)
from ..scanner import scan_channel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/channels", tags=["channels"])
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
@require_auth
async def list_channels(request: Request):
    """List all active channels."""
    pool = request.app.state.db_pool
    channels = await get_active_channels(pool)
    
    return templates.TemplateResponse("partials/channels_table.html", {
        "request": request,
        "channels": channels
    })


@router.post("/add", response_class=HTMLResponse)
@require_auth
async def add_channel(
    request: Request,
    channel_input: str = Form(...)
):
    """
    Add a new channel by username, invite link, or ID.
    """
    pool = request.app.state.db_pool
    telegram_client = request.app.state.telegram_client
    
    # Check if Telegram is connected
    if not await telegram_client.is_user_authorized():
        return HTMLResponse("""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p>Telegram not connected. Please authenticate first.</p>
            </div>
        """)
    
    try:
        # Clean input
        channel_input = channel_input.strip()
        
        # Handle different input formats
        if channel_input.startswith("https://t.me/"):
            # Invite link or username link
            channel_input = channel_input.replace("https://t.me/", "")
            if channel_input.startswith("+"):
                # Private invite link
                channel_input = channel_input
        elif channel_input.startswith("@"):
            channel_input = channel_input[1:]
        
        # Try to get entity
        entity = await telegram_client.get_entity(channel_input)
        
        # Insert channel
        channel_data = {
            "id": entity.id,
            "name": getattr(entity, 'title', None) or getattr(entity, 'first_name', 'Unknown'),
            "username": getattr(entity, 'username', None)
        }
        
        await insert_channel(pool, channel_data)
        
        logger.info(f"Added channel: {channel_data['name']} (ID: {entity.id})")
        
        # Refresh channel list
        channels = await get_active_channels(pool)
        return templates.TemplateResponse("partials/channels_table.html", {
            "request": request,
            "channels": channels,
            "success_message": f"Added: {channel_data['name']}"
        })
        
    except UsernameNotOccupiedError:
        return HTMLResponse("""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p>Channel/username not found.</p>
            </div>
        """)
    except ChannelPrivateError:
        return HTMLResponse("""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p>Channel is private. Join it first.</p>
            </div>
        """)
    except Exception as e:
        logger.error(f"Failed to add channel: {e}")
        return HTMLResponse(f"""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p>Error: {str(e)}</p>
            </div>
        """)


@router.post("/{channel_id}/scan", response_class=HTMLResponse)
@require_auth
async def scan_channel_now(request: Request, channel_id: int):
    """Trigger an immediate scan of a channel."""
    pool = request.app.state.db_pool
    telegram_client = request.app.state.telegram_client
    
    channel = await get_channel_by_id(pool, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    try:
        new_count, _ = await scan_channel(
            telegram_client,
            pool,
            channel_id,
            channel.get('last_scanned_message_id', 0)
        )
        
        return HTMLResponse(f"""
            <div class="p-3 bg-green-500/20 border border-green-500/50 rounded text-green-400 text-sm">
                Found {new_count} new files
            </div>
        """)
        
    except Exception as e:
        logger.error(f"Manual scan failed: {e}")
        return HTMLResponse(f"""
            <div class="p-3 bg-red-500/20 border border-red-500/50 rounded text-red-400 text-sm">
                Scan failed: {str(e)}
            </div>
        """)


@router.post("/{channel_id}/remove", response_class=HTMLResponse)
@require_auth
async def remove_channel(request: Request, channel_id: int):
    """Remove (deactivate) a channel."""
    pool = request.app.state.db_pool
    
    success = await deactivate_channel(pool, channel_id)
    
    if success:
        channels = await get_active_channels(pool)
        return templates.TemplateResponse("partials/channels_table.html", {
            "request": request,
            "channels": channels
        })
    else:
        raise HTTPException(status_code=500, detail="Failed to remove channel")
