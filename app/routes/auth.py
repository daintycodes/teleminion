"""
Telegram authentication routes for TeleMinion.
Handles first-time login with verification code.
"""
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from telethon.errors import (
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError
)

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
async def auth_status(request: Request):
    """Get current authentication status."""
    client = request.app.state.telegram_client
    auth_state = getattr(request.app.state, 'auth_status', {})
    
    try:
        is_authorized = await client.is_user_authorized()
        return {
            "authenticated": is_authorized,
            "awaiting_code": auth_state.get('awaiting_code', False),
            "phone_code_hash": auth_state.get('phone_code_hash')
        }
    except Exception as e:
        logger.error(f"Auth status check failed: {e}")
        return {
            "authenticated": False,
            "error": str(e)
        }


@router.post("/send-code")
async def send_code(request: Request):
    """Request verification code to be sent to phone."""
    client = request.app.state.telegram_client
    
    try:
        # Check if already authorized
        if await client.is_user_authorized():
            return {"success": True, "message": "Already authenticated"}
        
        # Send code request
        result = await client.send_code_request(settings.TELEGRAM_PHONE)
        
        # Store the phone_code_hash for verification
        request.app.state.auth_status = {
            'awaiting_code': True,
            'phone_code_hash': result.phone_code_hash
        }
        
        logger.info("Verification code sent")
        
        return {
            "success": True,
            "message": "Verification code sent to your Telegram"
        }
        
    except Exception as e:
        logger.error(f"Failed to send code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify")
async def verify_code(request: Request):
    """Verify the received code and complete authentication."""
    client = request.app.state.telegram_client
    auth_state = getattr(request.app.state, 'auth_status', {})
    
    form = await request.form()
    code = form.get("code", "").strip()
    
    if not code:
        raise HTTPException(status_code=400, detail="Verification code required")
    
    phone_code_hash = auth_state.get('phone_code_hash')
    if not phone_code_hash:
        raise HTTPException(
            status_code=400, 
            detail="No pending verification. Please request a new code."
        )
    
    try:
        # Sign in with the code
        await client.sign_in(
            phone=settings.TELEGRAM_PHONE,
            code=code,
            phone_code_hash=phone_code_hash
        )
        
        # Clear auth state
        request.app.state.auth_status = {
            'awaiting_code': False,
            'authenticated': True
        }
        
        # Save session
        if hasattr(client.session, 'save_session'):
            await client.session.save_session()
        
        logger.info("Telegram authentication successful")
        
        return HTMLResponse("""
            <div class="p-4 bg-green-500/20 border border-green-500/50 rounded-lg text-green-400">
                <p class="font-semibold">✓ Authentication successful!</p>
                <p class="text-sm mt-1">You can now add channels and start scanning.</p>
                <script>setTimeout(() => location.reload(), 2000);</script>
            </div>
        """)
        
    except PhoneCodeInvalidError:
        raise HTTPException(status_code=400, detail="Invalid verification code")
        
    except PhoneCodeExpiredError:
        request.app.state.auth_status = {'awaiting_code': False}
        raise HTTPException(status_code=400, detail="Code expired. Please request a new one.")
        
    except SessionPasswordNeededError:
        # 2FA is enabled
        request.app.state.auth_status = {
            'awaiting_code': False,
            'awaiting_2fa': True
        }
        raise HTTPException(
            status_code=400, 
            detail="Two-factor authentication required. Please use the password endpoint."
        )
        
    except Exception as e:
        logger.error(f"Verification failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/2fa")
async def verify_2fa(request: Request):
    """Verify 2FA password if enabled."""
    client = request.app.state.telegram_client
    
    form = await request.form()
    password = form.get("password", "").strip()
    
    if not password:
        raise HTTPException(status_code=400, detail="2FA password required")
    
    try:
        await client.sign_in(password=password)
        
        request.app.state.auth_status = {
            'awaiting_code': False,
            'awaiting_2fa': False,
            'authenticated': True
        }
        
        if hasattr(client.session, 'save_session'):
            await client.session.save_session()
        
        logger.info("2FA authentication successful")
        
        return HTMLResponse("""
            <div class="p-4 bg-green-500/20 border border-green-500/50 rounded-lg text-green-400">
                <p class="font-semibold">✓ Authentication successful!</p>
                <script>setTimeout(() => location.reload(), 2000);</script>
            </div>
        """)
        
    except Exception as e:
        logger.error(f"2FA verification failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid 2FA password")
