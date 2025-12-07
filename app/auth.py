"""
TeleMinion V2 Authentication

Simple session-based authentication for single admin user.
"""
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional
from functools import wraps

from fastapi import Request, Response, HTTPException
from fastapi.responses import RedirectResponse
import bcrypt

from .config import settings

logger = logging.getLogger(__name__)

# In-memory session store (simple approach for single user)
# Key: session_token, Value: {"username": str, "expires": datetime}
sessions: dict = {}


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def create_session(username: str) -> str:
    """Create a new session and return the token."""
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=settings.SESSION_EXPIRE_HOURS)
    sessions[token] = {
        "username": username,
        "expires": expires
    }
    logger.info(f"Session created for user: {username}")
    return token


def validate_session(token: str) -> Optional[dict]:
    """Validate a session token and return session data if valid."""
    if not token or token not in sessions:
        return None
    
    session = sessions[token]
    if datetime.utcnow() > session["expires"]:
        # Session expired
        del sessions[token]
        return None
    
    return session


def destroy_session(token: str) -> bool:
    """Destroy a session."""
    if token in sessions:
        del sessions[token]
        return True
    return False


def get_session_token(request: Request) -> Optional[str]:
    """Extract session token from request cookies."""
    return request.cookies.get("session_token")


def is_authenticated(request: Request) -> bool:
    """Check if the current request is authenticated."""
    token = get_session_token(request)
    return validate_session(token) is not None


async def login_user(username: str, password: str) -> Optional[str]:
    """
    Authenticate user and return session token if successful.
    """
    # Check if auth is configured
    if not settings.ADMIN_PASSWORD_HASH:
        logger.warning("ADMIN_PASSWORD_HASH not configured, auth disabled")
        return None
    
    # Verify credentials
    if username != settings.ADMIN_USERNAME:
        logger.warning(f"Login attempt with invalid username: {username}")
        return None
    
    if not verify_password(password, settings.ADMIN_PASSWORD_HASH):
        logger.warning(f"Login attempt with invalid password for user: {username}")
        return None
    
    # Create session
    token = create_session(username)
    return token


def require_auth(func):
    """
    Decorator to require authentication for a route.
    Redirects to /login if not authenticated.
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not is_auth_enabled():
            return await func(request, *args, **kwargs)
        
        if not is_authenticated(request):
            return RedirectResponse(url="/login", status_code=303)
        
        return await func(request, *args, **kwargs)
    
    return wrapper


def require_auth_api(func):
    """
    Decorator to require authentication for API routes.
    Returns 401 if not authenticated.
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not is_auth_enabled():
            return await func(request, *args, **kwargs)
        
        if not is_authenticated(request):
            raise HTTPException(status_code=401, detail="Not authenticated")
        
        return await func(request, *args, **kwargs)
    
    return wrapper


def is_auth_enabled() -> bool:
    """Check if authentication is enabled (password hash is set)."""
    return bool(settings.ADMIN_PASSWORD_HASH)


def get_current_user(request: Request) -> Optional[str]:
    """Get the current authenticated username."""
    token = get_session_token(request)
    session = validate_session(token)
    return session["username"] if session else None


# Cleanup expired sessions periodically
async def cleanup_expired_sessions():
    """Remove expired sessions from memory."""
    now = datetime.utcnow()
    expired = [
        token for token, data in sessions.items()
        if now > data["expires"]
    ]
    for token in expired:
        del sessions[token]
    
    if expired:
        logger.info(f"Cleaned up {len(expired)} expired sessions")
