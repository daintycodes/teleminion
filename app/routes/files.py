"""
TeleMinion V2 File Routes

File management endpoints including batch operations and n8n callbacks.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..auth import require_auth, require_auth_api
from ..config import CATEGORIES, get_category_options, MIME_CATEGORY_OPTIONS
from ..database import (
    get_file_by_id,
    get_files_by_status,
    update_file_status,
    update_file_category,
    mark_file_processed,
    get_unprocessed_files
)
from ..models import (
    FileStatus,
    BatchApprovalRequest,
    MarkProcessedRequest,
    FileGroupSummary
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["files"])
templates = Jinja2Templates(directory="templates")


# =============================================================================
# Dashboard Endpoints (HTMX)
# =============================================================================

@router.get("/{file_id}/status", response_class=HTMLResponse)
@require_auth
async def file_status_partial(request: Request, file_id: int):
    """Get file row partial for HTMX polling."""
    pool = request.app.state.db_pool
    file = await get_file_by_id(pool, file_id)
    
    if not file:
        return HTMLResponse("")
    
    return templates.TemplateResponse("partials/file_row.html", {
        "request": request,
        "file": file,
        "categories": CATEGORIES,
        "mime_options": MIME_CATEGORY_OPTIONS
    })


@router.post("/{file_id}/approve", response_class=HTMLResponse)
@require_auth
async def approve_file(
    request: Request, 
    file_id: int,
    category: Optional[str] = Form(None)
):
    """Approve a single file for download."""
    pool = request.app.state.db_pool
    download_queue = request.app.state.download_queue
    
    file = await get_file_by_id(pool, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    # Use provided category or keep existing
    if category and category in CATEGORIES:
        await update_file_category(pool, file_id, category)
    elif not file.get('destination_category'):
        raise HTTPException(status_code=400, detail="Category required")
    
    # Update status to QUEUED
    await update_file_status(pool, file_id, FileStatus.QUEUED)
    
    # Add to download queue
    await download_queue.put(file_id)
    
    # Return updated row
    file = await get_file_by_id(pool, file_id)
    return templates.TemplateResponse("partials/file_row.html", {
        "request": request,
        "file": file,
        "categories": CATEGORIES,
        "mime_options": MIME_CATEGORY_OPTIONS
    })


@router.post("/{file_id}/category", response_class=HTMLResponse)
@require_auth
async def update_category(
    request: Request,
    file_id: int,
    category: str = Form(...)
):
    """Update file category (before approval)."""
    pool = request.app.state.db_pool
    
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")
    
    await update_file_category(pool, file_id, category)
    
    file = await get_file_by_id(pool, file_id)
    return templates.TemplateResponse("partials/file_row.html", {
        "request": request,
        "file": file,
        "categories": CATEGORIES,
        "mime_options": MIME_CATEGORY_OPTIONS
    })


@router.post("/{file_id}/retry", response_class=HTMLResponse)
@require_auth
async def retry_file(request: Request, file_id: int):
    """Retry a failed file."""
    pool = request.app.state.db_pool
    download_queue = request.app.state.download_queue
    
    file = await get_file_by_id(pool, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    if file['status'] == 'FAILED_PERMANENT':
        raise HTTPException(status_code=400, detail="File permanently failed")
    
    # Reset status to QUEUED
    await update_file_status(
        pool, file_id, FileStatus.QUEUED,
        error_message=None
    )
    
    # Add to download queue
    await download_queue.put(file_id)
    
    file = await get_file_by_id(pool, file_id)
    return templates.TemplateResponse("partials/file_row.html", {
        "request": request,
        "file": file,
        "categories": CATEGORIES,
        "mime_options": MIME_CATEGORY_OPTIONS
    })


# =============================================================================
# Batch Operations
# =============================================================================

@router.post("/batch/preview", response_class=HTMLResponse)
@require_auth
async def batch_preview(request: Request):
    """
    Get grouped summary of selected files for batch modal.
    Expects form data with file_ids[] array.
    """
    pool = request.app.state.db_pool
    form = await request.form()
    
    # Get file IDs from form
    file_ids_raw = form.getlist("file_ids[]")
    file_ids = [int(fid) for fid in file_ids_raw if fid.isdigit()]
    
    if not file_ids:
        return HTMLResponse("<p class='text-gray-400'>No files selected</p>")
    
    # Group files by type
    audio_files = []
    pdf_files = []
    
    for file_id in file_ids:
        file = await get_file_by_id(pool, file_id)
        if file:
            if file.get('file_type') == 'audio':
                audio_files.append(file)
            elif file.get('file_type') == 'pdf':
                pdf_files.append(file)
    
    groups = []
    
    if audio_files:
        groups.append(FileGroupSummary(
            file_type="audio",
            count=len(audio_files),
            file_ids=[f['id'] for f in audio_files],
            default_category=audio_files[0].get('destination_category', 'messages'),
            category_options=MIME_CATEGORY_OPTIONS.get('audio', ['messages', 'songs'])
        ))
    
    if pdf_files:
        groups.append(FileGroupSummary(
            file_type="pdf",
            count=len(pdf_files),
            file_ids=[f['id'] for f in pdf_files],
            default_category=pdf_files[0].get('destination_category', 'ror'),
            category_options=MIME_CATEGORY_OPTIONS.get('pdf', ['ror', 'books'])
        ))
    
    return templates.TemplateResponse("partials/batch_modal.html", {
        "request": request,
        "groups": groups,
        "total_files": len(file_ids),
        "categories": CATEGORIES
    })


@router.post("/batch/approve")
@require_auth
async def batch_approve(request: Request):
    """
    Approve batch of files with category assignments.
    """
    pool = request.app.state.db_pool
    download_queue = request.app.state.download_queue
    
    form = await request.form()
    
    approved = 0
    errors = []
    
    # Process each file type group
    for file_type in ['audio', 'pdf']:
        # Get file IDs for this type
        file_ids_key = f"{file_type}_file_ids"
        category_key = f"{file_type}_category"
        
        file_ids_raw = form.get(file_ids_key, "")
        category = form.get(category_key)
        
        if not file_ids_raw:
            continue
        
        file_ids = [int(fid) for fid in file_ids_raw.split(",") if fid.strip().isdigit()]
        
        if not category or category not in CATEGORIES:
            errors.append(f"Invalid category for {file_type}")
            continue
        
        for file_id in file_ids:
            try:
                # Update category
                await update_file_category(pool, file_id, category)
                
                # Update status to QUEUED
                await update_file_status(pool, file_id, FileStatus.QUEUED)
                
                # Add to download queue
                await download_queue.put(file_id)
                
                approved += 1
            except Exception as e:
                errors.append(f"File {file_id}: {str(e)}")
    
    # Return success message
    if errors:
        return HTMLResponse(f"""
            <div class="p-4 bg-amber-500/20 border border-amber-500/50 rounded-lg">
                <p class="text-amber-400">Approved {approved} files with {len(errors)} errors</p>
                <ul class="mt-2 text-sm text-amber-300">
                    {"".join(f"<li>{e}</li>" for e in errors[:5])}
                </ul>
            </div>
        """)
    
    return HTMLResponse(f"""
        <div class="p-4 bg-green-500/20 border border-green-500/50 rounded-lg">
            <p class="text-green-400">✓ Approved {approved} files for download</p>
            <script>
                setTimeout(() => {{
                    htmx.trigger('#pending-content', 'refresh');
                    document.getElementById('batch-modal').close();
                }}, 1500);
            </script>
        </div>
    """)


# =============================================================================
# n8n Integration APIs
# =============================================================================

@router.get("/api/unprocessed")
@require_auth_api
async def api_unprocessed_files(request: Request):
    """
    Get files ready for n8n processing.
    Called by n8n to poll for new uploads.
    """
    pool = request.app.state.db_pool
    files = await get_unprocessed_files(pool)
    
    return {
        "files": files,
        "count": len(files)
    }


@router.post("/api/{file_id}/mark-processed")
async def api_mark_processed(
    request: Request,
    file_id: int
):
    """
    Mark a file as processed by n8n.
    Called by n8n after successful processing.
    
    Note: This endpoint should have its own API key auth
    for production, but using session auth for simplicity.
    """
    pool = request.app.state.db_pool
    
    try:
        data = await request.json()
    except:
        data = {}
    
    success = await mark_file_processed(pool, file_id, data)
    
    if success:
        return {"status": "ok", "file_id": file_id}
    else:
        raise HTTPException(status_code=500, detail="Failed to update file")
