"""
Channel management routes for TeleMinion.
"""
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from telethon.errors import (
    UsernameNotOccupiedError,
    InviteHashInvalidError,
    ChannelPrivateError
)

from ..database import (
    get_channels, 
    insert_channel, 
    delete_channel,
    get_channel_by_id
)
from ..scanner import scan_channel_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])


@router.get("")
async def list_channels(request: Request):
    """Get all channels."""
    pool = request.app.state.db_pool
    channels = await get_channels(pool, active_only=False)
    return {"channels": channels}


@router.post("")
async def add_channel(request: Request):
    """
    Add a new channel to track.
    Accepts channel username, invite link, or ID.
    """
    pool = request.app.state.db_pool
    client = request.app.state.telegram_client
    
    form = await request.form()
    identifier = form.get("identifier", "").strip()
    
    if not identifier:
        raise HTTPException(status_code=400, detail="Channel identifier required")
    
    try:
        # Try to get the entity (channel/chat)
        entity = await client.get_entity(identifier)
        
        # Extract channel info
        channel_data = {
            'id': entity.id,
            'name': getattr(entity, 'title', None) or getattr(entity, 'first_name', None),
            'username': getattr(entity, 'username', None)
        }
        
        # Insert into database
        await insert_channel(pool, channel_data)
        
        logger.info(f"Added channel: {channel_data['name']} ({channel_data['id']})")
        
        # Return the channel row partial for HTMX
        return HTMLResponse(f"""
            <tr id="channel-{channel_data['id']}">
                <td class="px-4 py-3 text-gray-200">{channel_data['name'] or 'Unknown'}</td>
                <td class="px-4 py-3 text-gray-400">@{channel_data['username'] or 'N/A'}</td>
                <td class="px-4 py-3 text-gray-400">{channel_data['id']}</td>
                <td class="px-4 py-3">
                    <span class="px-2 py-1 rounded-full text-xs bg-green-500/20 text-green-400">Active</span>
                </td>
                <td class="px-4 py-3">
                    <button hx-post="/channels/{channel_data['id']}/scan"
                            hx-swap="none"
                            class="text-blue-400 hover:text-blue-300 mr-3">
                        Scan Now
                    </button>
                    <button hx-delete="/channels/{channel_data['id']}"
                            hx-target="#channel-{channel_data['id']}"
                            hx-swap="outerHTML"
                            hx-confirm="Remove this channel?"
                            class="text-red-400 hover:text-red-300">
                        Remove
                    </button>
                </td>
            </tr>
        """)
        
    except UsernameNotOccupiedError:
        raise HTTPException(status_code=404, detail="Username not found")
    except InviteHashInvalidError:
        raise HTTPException(status_code=400, detail="Invalid invite link")
    except ChannelPrivateError:
        raise HTTPException(status_code=403, detail="Channel is private")
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{channel_id}")
async def remove_channel(request: Request, channel_id: int):
    """Remove a channel from tracking."""
    pool = request.app.state.db_pool
    
    await delete_channel(pool, channel_id)
    logger.info(f"Removed channel: {channel_id}")
    
    # Return empty content for HTMX to remove the row
    return HTMLResponse("")


@router.post("/{channel_id}/scan")
async def trigger_scan(request: Request, channel_id: int):
    """Manually trigger a scan for a channel."""
    pool = request.app.state.db_pool
    client = request.app.state.telegram_client
    
    try:
        new_files = await scan_channel_now(client, pool, channel_id)
        return {"success": True, "new_files": new_files}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error scanning channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))
