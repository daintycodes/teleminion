"""
File management routes for TeleMinion.
"""
import logging
from math import ceil
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..database import get_files, get_file_by_id, update_file_status
from ..worker import queue_file_for_download, queue_files_batch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

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


@router.get("")
async def list_files(
    request: Request,
    status: str = Query("PENDING", description="Filter by status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=500)
):
    """Get paginated list of files."""
    pool = request.app.state.db_pool
    
    files, total = await get_files(pool, status=status, page=page, per_page=per_page)
    
    total_pages = ceil(total / per_page) if total > 0 else 1
    
    return {
        "items": files,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1
    }


@router.get("/{file_id}")
async def get_file(request: Request, file_id: int):
    """Get a single file by ID."""
    pool = request.app.state.db_pool
    
    file = await get_file_by_id(pool, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    return file


@router.get("/{file_id}/row")
async def get_file_row(request: Request, file_id: int):
    """Get file row partial for HTMX updates."""
    pool = request.app.state.db_pool
    
    file = await get_file_by_id(pool, file_id)
    if not file:
        return HTMLResponse("")
    
    return templates.TemplateResponse(
        "partials/file_row.html",
        {"request": request, "file": file, "format_size": format_file_size}
    )


@router.get("/{file_id}/status")
async def get_file_status(request: Request, file_id: int):
    """Get file status - returns updated row for polling."""
    return await get_file_row(request, file_id)


@router.post("/{file_id}/approve")
async def approve_file(request: Request, file_id: int):
    """Approve a file for download."""
    pool = request.app.state.db_pool
    
    file = await get_file_by_id(pool, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    if file['status'] != 'PENDING':
        raise HTTPException(status_code=400, detail=f"File is already {file['status']}")
    
    # Queue for download
    await queue_file_for_download(request.app.state, file_id)
    
    # Get updated file and return row partial
    file = await get_file_by_id(pool, file_id)
    
    return templates.TemplateResponse(
        "partials/file_row.html",
        {"request": request, "file": file, "format_size": format_file_size}
    )


@router.post("/approve/batch")
async def approve_batch(request: Request):
    """Batch approve multiple files."""
    pool = request.app.state.db_pool
    
    form = await request.form()
    file_ids_str = form.get("file_ids", "")
    
    if not file_ids_str:
        raise HTTPException(status_code=400, detail="No file IDs provided")
    
    try:
        file_ids = [int(id.strip()) for id in file_ids_str.split(",") if id.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")
    
    # Queue all files
    await queue_files_batch(request.app.state, file_ids)
    
    return {"success": True, "queued": len(file_ids)}


@router.post("/approve/all")
async def approve_all_pending(request: Request):
    """Approve all pending files."""
    pool = request.app.state.db_pool
    
    # Get all pending files (no pagination limit)
    files, total = await get_files(pool, status="PENDING", page=1, per_page=1000)
    
    if not files:
        return {"success": True, "queued": 0}
    
    file_ids = [f['id'] for f in files]
    await queue_files_batch(request.app.state, file_ids)
    
    return {"success": True, "queued": len(file_ids)}


@router.post("/{file_id}/retry")
async def retry_failed(request: Request, file_id: int):
    """Retry a failed download."""
    pool = request.app.state.db_pool
    
    file = await get_file_by_id(pool, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    if file['status'] != 'FAILED':
        raise HTTPException(status_code=400, detail="Can only retry FAILED files")
    
    # Reset to PENDING first, then queue
    await update_file_status(pool, file_id, 'PENDING')
    await queue_file_for_download(request.app.state, file_id)
    
    file = await get_file_by_id(pool, file_id)
    
    return templates.TemplateResponse(
        "partials/file_row.html",
        {"request": request, "file": file, "format_size": format_file_size}
    )
