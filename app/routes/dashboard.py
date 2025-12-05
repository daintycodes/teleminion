"""
Dashboard routes for TeleMinion.
Server-Side Rendering with Jinja2 + HTMX.
"""
import logging
from math import ceil
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..database import get_files, get_channels, get_channel_stats
from ..minio_client import check_minio_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

templates = Jinja2Templates(directory="templates")


def format_file_size(size: int) -> str:
    """Format file size in human readable format."""
    if size is None:
        return "Unknown"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# Add custom filters to Jinja2
templates.env.filters['filesizeformat'] = format_file_size


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    pool = request.app.state.db_pool
    
    # Get stats
    stats = await get_channel_stats(pool)
    channels = await get_channels(pool, active_only=True)
    
    # Check Telegram connection
    telegram_connected = False
    auth_status = getattr(request.app.state, 'auth_status', {})
    
    if hasattr(request.app.state, 'telegram_client'):
        client = request.app.state.telegram_client
        try:
            telegram_connected = client.is_connected() and await client.is_user_authorized()
        except:
            pass
    
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "channels": channels,
            "telegram_connected": telegram_connected,
            "auth_status": auth_status,
            "format_size": format_file_size
        }
    )


@router.get("/pending", response_class=HTMLResponse)
async def pending_files(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=500)
):
    """Get pending files table partial."""
    pool = request.app.state.db_pool
    
    files, total = await get_files(pool, status="PENDING", page=page, per_page=per_page)
    total_pages = ceil(total / per_page) if total > 0 else 1
    
    return templates.TemplateResponse(
        "partials/pending_table.html",
        {
            "request": request,
            "files": files,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "format_size": format_file_size
        }
    )


@router.get("/active", response_class=HTMLResponse)
async def active_files(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=500)
):
    """Get active (queued/downloading/uploading) files table partial."""
    pool = request.app.state.db_pool
    
    files, total = await get_files(pool, status="ACTIVE", page=page, per_page=per_page)
    total_pages = ceil(total / per_page) if total > 0 else 1
    
    return templates.TemplateResponse(
        "partials/active_table.html",
        {
            "request": request,
            "files": files,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "format_size": format_file_size
        }
    )


@router.get("/history", response_class=HTMLResponse)
async def history_files(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=500)
):
    """Get completed/failed files table partial."""
    pool = request.app.state.db_pool
    
    # Get completed files
    completed, completed_total = await get_files(pool, status="COMPLETED", page=page, per_page=per_page)
    # Get failed files
    failed, failed_total = await get_files(pool, status="FAILED", page=1, per_page=100)
    
    total = completed_total + failed_total
    total_pages = ceil(completed_total / per_page) if completed_total > 0 else 1
    
    return templates.TemplateResponse(
        "partials/history_table.html",
        {
            "request": request,
            "completed_files": completed,
            "failed_files": failed,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "format_size": format_file_size
        }
    )


@router.get("/channels/list", response_class=HTMLResponse)
async def channels_list(request: Request):
    """Get channels table partial."""
    pool = request.app.state.db_pool
    channels = await get_channels(pool, active_only=False)
    
    return templates.TemplateResponse(
        "partials/channels_table.html",
        {
            "request": request,
            "channels": channels
        }
    )


@router.get("/stats", response_class=HTMLResponse)
async def stats_partial(request: Request):
    """Get stats cards partial for live updates."""
    pool = request.app.state.db_pool
    stats = await get_channel_stats(pool)
    
    return templates.TemplateResponse(
        "partials/stats_cards.html",
        {
            "request": request,
            "stats": stats
        }
    )


@router.get("/health")
async def health_check(request: Request):
    """Health check endpoint."""
    pool = request.app.state.db_pool
    
    # Check database
    db_ok = False
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            db_ok = True
    except:
        pass
    
    # Check Telegram
    telegram_ok = False
    if hasattr(request.app.state, 'telegram_client'):
        try:
            telegram_ok = request.app.state.telegram_client.is_connected()
        except:
            pass
    
    # Check MinIO
    minio_ok = check_minio_connection()
    
    return {
        "status": "healthy" if (db_ok and minio_ok) else "degraded",
        "database_connected": db_ok,
        "telegram_connected": telegram_ok,
        "minio_connected": minio_ok
    }
