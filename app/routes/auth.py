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
            return HTMLResponse("""
                <div class="p-4 bg-green-500/20 border border-green-500/50 rounded-lg text-green-400">
                    <p class="font-semibold">✓ Already authenticated!</p>
                    <script>setTimeout(() => location.reload(), 2000);</script>
                </div>
            """)
        
        # Send code request
        result = await client.send_code_request(settings.TELEGRAM_PHONE)
        
        # Store the phone_code_hash for verification
        request.app.state.auth_status = {
            'awaiting_code': True,
            'phone_code_hash': result.phone_code_hash
        }
        
        logger.info("Verification code sent")
        
        # Return HTML form for entering the code
        return HTMLResponse("""
            <div class="space-y-4">
                <div class="p-4 bg-blue-500/20 border border-blue-500/50 rounded-lg text-blue-400">
                    <p class="font-semibold">📱 Verification code sent!</p>
                    <p class="text-sm mt-1">Check your Telegram app for the code.</p>
                </div>
                <form hx-post="/auth/verify" 
                      hx-target="#auth-container"
                      hx-swap="innerHTML"
                      class="flex gap-3 items-center">
                    <input type="text" 
                           name="code" 
                           placeholder="Enter verification code"
                           class="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg focus:outline-none focus:border-indigo-500 w-48 text-white"
                           autofocus
                           autocomplete="off"
                           pattern="[0-9]*"
                           inputmode="numeric">
                    <button type="submit" 
                            class="px-6 py-2 rounded-lg font-medium text-white"
                            style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);">
                        Verify
                    </button>
                </form>
            </div>
        """)
        
    except Exception as e:
        logger.error(f"Failed to send code: {e}")
        return HTMLResponse(f"""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p class="font-semibold">❌ Failed to send code</p>
                <p class="text-sm mt-1">{str(e)}</p>
                <button hx-post="/auth/send-code"
                        hx-target="#auth-container"
                        hx-swap="innerHTML"
                        class="mt-3 px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-white text-sm">
                    Try Again
                </button>
            </div>
        """)


@router.post("/verify")
async def verify_code(request: Request):
    """Verify the received code and complete authentication."""
    client = request.app.state.telegram_client
    auth_state = getattr(request.app.state, 'auth_status', {})
    
    form = await request.form()
    code = form.get("code", "").strip()
    
    if not code:
        return HTMLResponse("""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p>Please enter the verification code.</p>
                <form hx-post="/auth/verify" 
                      hx-target="#auth-container"
                      hx-swap="innerHTML"
                      class="flex gap-3 items-center mt-3">
                    <input type="text" name="code" placeholder="Enter code"
                           class="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg focus:outline-none focus:border-indigo-500 w-48 text-white"
                           autofocus autocomplete="off">
                    <button type="submit" 
                            class="px-6 py-2 rounded-lg font-medium text-white"
                            style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);">
                        Verify
                    </button>
                </form>
            </div>
        """)
    
    phone_code_hash = auth_state.get('phone_code_hash')
    if not phone_code_hash:
        return HTMLResponse("""
            <div class="p-4 bg-amber-500/20 border border-amber-500/50 rounded-lg text-amber-400">
                <p>Session expired. Please request a new code.</p>
                <button hx-post="/auth/send-code"
                        hx-target="#auth-container"
                        hx-swap="innerHTML"
                        class="mt-3 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm">
                    Send New Code
                </button>
            </div>
        """)
    
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
        
        # Start background tasks if not already running
        if hasattr(request.app.state, 'start_background_tasks'):
            await request.app.state.start_background_tasks()
        
        logger.info("Telegram authentication successful")
        
        return HTMLResponse("""
            <div class="p-4 bg-green-500/20 border border-green-500/50 rounded-lg text-green-400">
                <p class="font-semibold">✓ Authentication successful!</p>
                <p class="text-sm mt-1">You can now add channels and start scanning.</p>
                <script>setTimeout(() => location.reload(), 2000);</script>
            </div>
        """)
        
    except PhoneCodeInvalidError:
        return HTMLResponse("""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p class="font-semibold">❌ Invalid code</p>
                <p class="text-sm mt-1">Please check and try again.</p>
                <form hx-post="/auth/verify" 
                      hx-target="#auth-container"
                      hx-swap="innerHTML"
                      class="flex gap-3 items-center mt-3">
                    <input type="text" name="code" placeholder="Enter code"
                           class="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg focus:outline-none focus:border-indigo-500 w-48 text-white"
                           autofocus autocomplete="off">
                    <button type="submit" 
                            class="px-6 py-2 rounded-lg font-medium text-white"
                            style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);">
                        Verify
                    </button>
                </form>
            </div>
        """)
        
    except PhoneCodeExpiredError:
        request.app.state.auth_status = {'awaiting_code': False}
        return HTMLResponse("""
            <div class="p-4 bg-amber-500/20 border border-amber-500/50 rounded-lg text-amber-400">
                <p class="font-semibold">⏰ Code expired</p>
                <p class="text-sm mt-1">Please request a new verification code.</p>
                <button hx-post="/auth/send-code"
                        hx-target="#auth-container"
                        hx-swap="innerHTML"
                        class="mt-3 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm">
                    Send New Code
                </button>
            </div>
        """)
        
    except SessionPasswordNeededError:
        # 2FA is enabled
        request.app.state.auth_status = {
            'awaiting_code': False,
            'awaiting_2fa': True
        }
        return HTMLResponse("""
            <div class="space-y-4">
                <div class="p-4 bg-amber-500/20 border border-amber-500/50 rounded-lg text-amber-400">
                    <p class="font-semibold">🔐 Two-Factor Authentication Required</p>
                    <p class="text-sm mt-1">Please enter your 2FA password.</p>
                </div>
                <form hx-post="/auth/2fa"
                      hx-target="#auth-container"
                      hx-swap="innerHTML"
                      class="flex gap-3 items-center">
                    <input type="password"
                           name="password"
                           placeholder="Enter 2FA password"
                           class="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg focus:outline-none focus:border-indigo-500 w-48 text-white"
                           autofocus>
                    <button type="submit" 
                            class="px-6 py-2 rounded-lg font-medium text-white"
                            style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);">
                        Submit
                    </button>
                </form>
            </div>
        """)
        
    except Exception as e:
        logger.error(f"Verification failed: {e}")
        return HTMLResponse(f"""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p class="font-semibold">❌ Verification failed</p>
                <p class="text-sm mt-1">{str(e)}</p>
                <button hx-post="/auth/send-code"
                        hx-target="#auth-container"
                        hx-swap="innerHTML"
                        class="mt-3 px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-white text-sm">
                    Try Again
                </button>
            </div>
        """)


@router.post("/2fa")
async def verify_2fa(request: Request):
    """Verify 2FA password if enabled."""
    client = request.app.state.telegram_client
    
    form = await request.form()
    password = form.get("password", "").strip()
    
    if not password:
        return HTMLResponse("""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p>Please enter your 2FA password.</p>
                <form hx-post="/auth/2fa"
                      hx-target="#auth-container"
                      hx-swap="innerHTML"
                      class="flex gap-3 items-center mt-3">
                    <input type="password" name="password" placeholder="2FA password"
                           class="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg focus:outline-none focus:border-indigo-500 w-48 text-white"
                           autofocus>
                    <button type="submit" 
                            class="px-6 py-2 rounded-lg font-medium text-white"
                            style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);">
                        Submit
                    </button>
                </form>
            </div>
        """)
    
    try:
        await client.sign_in(password=password)
        
        request.app.state.auth_status = {
            'awaiting_code': False,
            'awaiting_2fa': False,
            'authenticated': True
        }
        
        if hasattr(client.session, 'save_session'):
            await client.session.save_session()
        
        if hasattr(request.app.state, 'start_background_tasks'):
            await request.app.state.start_background_tasks()
        
        logger.info("2FA authentication successful")
        
        return HTMLResponse("""
            <div class="p-4 bg-green-500/20 border border-green-500/50 rounded-lg text-green-400">
                <p class="font-semibold">✓ Authentication successful!</p>
                <p class="text-sm mt-1">You can now add channels and start scanning.</p>
                <script>setTimeout(() => location.reload(), 2000);</script>
            </div>
        """)
        
    except Exception as e:
        logger.error(f"2FA verification failed: {e}")
        return HTMLResponse(f"""
            <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400">
                <p class="font-semibold">❌ Invalid 2FA password</p>
                <p class="text-sm mt-1">{str(e)}</p>
                <form hx-post="/auth/2fa"
                      hx-target="#auth-container"
                      hx-swap="innerHTML"
                      class="flex gap-3 items-center mt-3">
                    <input type="password" name="password" placeholder="2FA password"
                           class="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg focus:outline-none focus:border-indigo-500 w-48 text-white"
                           autofocus>
                    <button type="submit" 
                            class="px-6 py-2 rounded-lg font-medium text-white"
                            style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);">
                        Try Again
                    </button>
                </form>
            </div>
        """)
