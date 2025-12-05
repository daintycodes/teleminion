"""
TeleMinion - Main FastAPI Application

A self-hosted automation system for downloading Audio/PDFs from 
Telegram channels to MinIO storage.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from telethon import TelegramClient

from .config import settings
from .database import (
    create_db_pool, 
    init_database, 
    PostgresSession
)
from .minio_client import init_minio_buckets, get_minio_client
from .scanner import channel_scanner
from .worker import download_worker
from .routes import dashboard, channels, files, auth

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def create_telegram_client(pool) -> TelegramClient:
    """Create and configure Telegram client with PostgresSession."""
    session = PostgresSession(settings.TELEGRAM_SESSION_NAME, pool)
    
    # Load existing session if available
    await session.load_session()
    
    client = TelegramClient(
        session,
        settings.TELEGRAM_API_ID,
        settings.TELEGRAM_API_HASH,
        connection_retries=5,
        retry_delay=1,
        auto_reconnect=True
    )
    
    return client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.
    Handles startup and shutdown of all components.
    """
    logger.info("Starting TeleMinion...")
    
    # =========================================================================
    # STARTUP
    # =========================================================================
    
    # 1. Create database pool
    logger.info("Connecting to PostgreSQL...")
    app.state.db_pool = await create_db_pool()
    
    # 2. Initialize database schema
    await init_database(app.state.db_pool)
    logger.info("Database initialized")
    
    # 3. Initialize MinIO buckets
    try:
        logger.info("Initializing MinIO buckets...")
        await init_minio_buckets()
        logger.info("MinIO buckets ready")
    except Exception as e:
        logger.error(f"MinIO initialization failed: {e}")
        # Continue anyway, MinIO might come up later
    
    # 4. Create Telegram client
    logger.info("Creating Telegram client...")
    app.state.telegram_client = await create_telegram_client(app.state.db_pool)
    
    # 5. Connect Telegram client
    try:
        await app.state.telegram_client.connect()
        
        # Check if already authorized
        if await app.state.telegram_client.is_user_authorized():
            logger.info("Telegram client connected and authorized")
            app.state.auth_status = {'authenticated': True}
        else:
            logger.info("Telegram client connected, awaiting authentication")
            app.state.auth_status = {'authenticated': False, 'awaiting_code': False}
            
    except Exception as e:
        logger.error(f"Telegram connection failed: {e}")
        app.state.auth_status = {'authenticated': False, 'error': str(e)}
    
    # 6. Create download queue
    app.state.download_queue = asyncio.Queue(maxsize=100)
    
    # 7. Start background tasks (only if authenticated)
    app.state.scanner_task = None
    app.state.worker_task = None
    
    async def start_background_tasks():
        """Start scanner and worker if authenticated."""
        if await app.state.telegram_client.is_user_authorized():
            if app.state.scanner_task is None:
                app.state.scanner_task = asyncio.create_task(
                    channel_scanner(app.state.telegram_client, app.state.db_pool)
                )
                logger.info("Channel scanner started")
            
            if app.state.worker_task is None:
                app.state.worker_task = asyncio.create_task(
                    download_worker(app.state)
                )
                logger.info("Download worker started")
    
    # Store the function for later use after auth
    app.state.start_background_tasks = start_background_tasks
    
    # Start if already authenticated
    await start_background_tasks()
    
    logger.info("TeleMinion started successfully")
    
    # =========================================================================
    # YIELD - Application runs here
    # =========================================================================
    yield
    
    # =========================================================================
    # SHUTDOWN
    # =========================================================================
    logger.info("Shutting down TeleMinion...")
    
    # Cancel background tasks
    if app.state.scanner_task:
        app.state.scanner_task.cancel()
        try:
            await app.state.scanner_task
        except asyncio.CancelledError:
            pass
    
    if app.state.worker_task:
        app.state.worker_task.cancel()
        try:
            await app.state.worker_task
        except asyncio.CancelledError:
            pass
    
    # Disconnect Telegram
    if app.state.telegram_client:
        # Save session before disconnect
        if hasattr(app.state.telegram_client.session, 'save_session'):
            await app.state.telegram_client.session.save_session()
        await app.state.telegram_client.disconnect()
    
    # Close database pool
    if app.state.db_pool:
        await app.state.db_pool.close()
    
    logger.info("TeleMinion shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="TeleMinion",
    description="Telegram to MinIO automation system",
    version="1.0.0",
    lifespan=lifespan
)

# Mount static files
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except:
    pass  # Static directory might not exist

# Include routers
app.include_router(dashboard.router)
app.include_router(channels.router)
app.include_router(files.router)
app.include_router(auth.router)


@app.get("/api/queue-status")
async def queue_status():
    """Get current queue status."""
    queue = app.state.download_queue
    return {
        "queue_size": queue.qsize(),
        "queue_full": queue.full()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
