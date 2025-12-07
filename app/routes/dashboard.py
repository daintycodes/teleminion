"""
TeleMinion V2 Dashboard Routes

Server-side rendered dashboard with HTMX.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import (
    require_auth, 
    login_user, 
    destroy_session, 
    get_session_token,
    is_auth_enabled,
    is_authenticated
)
from ..config import settings, CATEGORIES, MIME_CATEGORY_OPTIONS
from ..database import (
    get_dashboard_stats,
    get_files_by_status,
    get_active_files,
    get_history_files,
    get_active_channels
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render login page."""
    if not is_auth_enabled():
        return RedirectResponse(url="/", status_code=303)
    
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Process login form."""
    token = await login_user(username, password)
    
    if token:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            max_age=settings.SESSION_EXPIRE_HOURS * 3600,
            samesite="lax"
        )
        return response
    
    # Login failed
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Invalid username or password"
    })


@router.get("/logout")
async def logout(request: Request):
    """Logout and clear session."""
    token = get_session_token(request)
    if token:
        destroy_session(token)
    
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response


@router.get("/", response_class=HTMLResponse)
@require_auth
async def dashboard(request: Request):
    """Main dashboard page."""
    pool = request.app.state.db_pool
    
    # Get authentication status
    auth_status = getattr(request.app.state, 'auth_status', {})
    
    # Get initial stats
    stats = await get_dashboard_stats(pool)
    
    # Check Telegram connection
    telegram_client = request.app.state.telegram_client
    telegram_connected = False
    try:
        telegram_connected = await telegram_client.is_user_authorized()
    except Exception:
        pass
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "auth_status": auth_status,
        "telegram_connected": telegram_connected,
        "categories": CATEGORIES,
        "auth_enabled": is_auth_enabled(),
        "stats": stats
    })


@router.get("/stats", response_class=HTMLResponse)
@require_auth
async def stats_partial(request: Request):
    """Stats cards partial for HTMX polling."""
    pool = request.app.state.db_pool
    stats = await get_dashboard_stats(pool)
    
    return templates.TemplateResponse("partials/stats_cards.html", {
        "request": request,
        "stats": stats
    })


@router.get("/pending", response_class=HTMLResponse)
@require_auth
async def pending_files(
    request: Request,
    page: int = 1,
    per_page: int = 50,
    sort: str = "created_at",
    order: str = "desc"
):
    """Pending files table partial."""
    pool = request.app.state.db_pool
    
    files, total = await get_files_by_status(
        pool, "PENDING", page, per_page, sort, order
    )
    
    total_pages = (total + per_page - 1) // per_page
    
    return templates.TemplateResponse("partials/pending_table.html", {
        "request": request,
        "files": files,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "sort": sort,
        "order": order,
        "categories": CATEGORIES,
        "mime_options": MIME_CATEGORY_OPTIONS
    })


@router.get("/active", response_class=HTMLResponse)
@require_auth
async def active_files(request: Request):
    """Active downloads table partial."""
    pool = request.app.state.db_pool
    files = await get_active_files(pool)
    
    return templates.TemplateResponse("partials/active_table.html", {
        "request": request,
        "files": files
    })


@router.get("/history", response_class=HTMLResponse)
@require_auth
async def history_files(
    request: Request,
    page: int = 1,
    per_page: int = 50
):
    """History table partial (completed + failed)."""
    pool = request.app.state.db_pool
    
    failed, completed, total = await get_history_files(pool, page, per_page)
    total_pages = (total + per_page - 1) // per_page
    
    return templates.TemplateResponse("partials/history_table.html", {
        "request": request,
        "failed_files": failed,
        "completed_files": completed,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages
    })


@router.get("/channels-tab", response_class=HTMLResponse)
@require_auth
async def channels_tab(request: Request):
    """Channels management table partial."""
    pool = request.app.state.db_pool
    channels = await get_active_channels(pool)
    
    return templates.TemplateResponse("partials/channels_table.html", {
        "request": request,
        "channels": channels
    })
