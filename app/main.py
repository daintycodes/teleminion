"""
TeleMinion V2 Main Application

FastAPI application with lifespan management for all services.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from minio import Minio
from telethon import TelegramClient

from .config import settings, ALL_BUCKETS, CATEGORIES
from .database import create_db_pool, init_database, PostgresSession
from .worker import download_worker, recover_queue
from .scanner import channel_scanner
from .healing import self_healing_task
from .backup import backup_task
from .routes import dashboard, channels, files, auth

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_minio_client() -> Minio:
    """Create and return MinIO client."""
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE
    )


def ensure_buckets_exist(minio_client: Minio):
    """Ensure all required MinIO buckets exist."""
    for bucket in ALL_BUCKETS:
        try:
            if not minio_client.bucket_exists(bucket):
                minio_client.make_bucket(bucket)
                logger.info(f"Created bucket: {bucket}")
        except Exception as e:
            logger.error(f"Failed to create bucket {bucket}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Startup:
    - Initialize database pool
    - Initialize MinIO client and buckets
    - Create Telegram client with PostgresSession
    - Recover download queue from database
    - Start background tasks (scanner, worker, healing, backup)
    
    Shutdown:
    - Cancel all background tasks
    - Disconnect Telegram client
    - Close database pool
    """
    logger.info("TeleMinion V2 starting up...")
    
    # Initialize database
    logger.info("Connecting to database...")
    app.state.db_pool = await create_db_pool()
    await init_database(app.state.db_pool)
    
    # Initialize MinIO
    logger.info("Connecting to MinIO...")
    app.state.minio_client = create_minio_client()
    ensure_buckets_exist(app.state.minio_client)
    
    # Initialize Telegram client with PostgresSession
    logger.info("Initializing Telegram client...")
    session = PostgresSession(settings.TELEGRAM_SESSION_NAME, app.state.db_pool)
    await session.load_session()
    
    app.state.telegram_client = TelegramClient(
        session,
        settings.TELEGRAM_API_ID,
        settings.TELEGRAM_API_HASH
    )
    await app.state.telegram_client.connect()
    
    # Auth status
    app.state.auth_status = {
        'awaiting_code': False,
        'awaiting_2fa': False,
        'authenticated': await app.state.telegram_client.is_user_authorized()
    }
    
    # Initialize download queue
    app.state.download_queue = asyncio.Queue(maxsize=1000)
    
    # List to track background tasks
    app.state.background_tasks = []
    
    # Function to start background tasks (called after Telegram auth)
    async def start_background_tasks():
        if app.state.background_tasks:
            # Already running
            return
        
        logger.info("Starting background tasks...")
        
        # Warm up entity cache by fetching dialogs
        # This fixes "Could not find the input entity" errors after restart
        try:
            logger.info("Warming up Telegram entity cache...")
            await app.state.telegram_client.get_dialogs(limit=None)
            logger.info("Entity cache warmed up")
        except Exception as e:
            logger.warning(f"Failed to warm up entity cache: {e}")
        
        # Recover queue from database
        await recover_queue(app.state.db_pool, app.state.download_queue)
        
        # Start channel scanner
        scanner_task = asyncio.create_task(
            channel_scanner(app.state.telegram_client, app.state.db_pool)
        )
        app.state.background_tasks.append(scanner_task)
        
        # Start download worker
        worker_task = asyncio.create_task(
            download_worker(
                app.state.telegram_client,
                app.state.minio_client,
                app.state.db_pool,
                app.state.download_queue
            )
        )
        app.state.background_tasks.append(worker_task)
        
        # Start self-healing task
        healing_task = asyncio.create_task(
            self_healing_task(app.state.db_pool, app.state.minio_client)
        )
        app.state.background_tasks.append(healing_task)
        
        # Start backup task
        backup_task_coro = asyncio.create_task(backup_task())
        app.state.background_tasks.append(backup_task_coro)
        
        logger.info(f"Started {len(app.state.background_tasks)} background tasks")
    
    app.state.start_background_tasks = start_background_tasks
    
    # Start tasks if already authenticated
    if app.state.auth_status['authenticated']:
        await start_background_tasks()
    else:
        logger.info("Waiting for Telegram authentication before starting tasks")
    
    logger.info("TeleMinion V2 startup complete")
    
    yield
    
    # Shutdown
    logger.info("TeleMinion V2 shutting down...")
    
    # Cancel background tasks
    for task in app.state.background_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    # Save Telegram session
    if hasattr(app.state.telegram_client.session, 'save_session'):
        await app.state.telegram_client.session.save_session()
    
    # Disconnect Telegram
    await app.state.telegram_client.disconnect()
    
    # Close database pool
    await app.state.db_pool.close()
    
    logger.info("TeleMinion V2 shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="TeleMinion V2",
    description="Production-hardened Telegram to MinIO downloader with RAG pipeline integration",
    version="2.0.0",
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(dashboard.router)
app.include_router(channels.router)
app.include_router(files.router)
app.include_router(auth.router)


@app.get("/health")
async def health_check(request: Request):
    """Health check endpoint for Docker/Coolify."""
    try:
        # Check database
        db_ok = False
        try:
            await request.app.state.db_pool.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            pass
        
        # Check Telegram (verify session with server)
        tg_ok = False
        try:
            if request.app.state.telegram_client.is_connected():
                # Actually ping the server to check if session is valid
                # is_user_authorized only checks local key presence
                me = await request.app.state.telegram_client.get_me()
                tg_ok = me is not None
            else:
                # Try to reconnect if disconnected
                await request.app.state.telegram_client.connect()
                tg_ok = await request.app.state.telegram_client.is_user_authorized()
        except Exception:
            tg_ok = False
        
        # Check MinIO
        minio_ok = False
        try:
            request.app.state.minio_client.list_buckets()
            minio_ok = True
        except Exception:
            pass
        
        status = "healthy" if (db_ok and minio_ok) else "degraded"
        
        return {
            "status": status,
            "database": db_ok,
            "telegram": tg_ok,
            "minio": minio_ok,
            "queue_size": request.app.state.download_queue.qsize()
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "unhealthy", "error": str(e)}
        )


@app.get("/api/queue-status")
async def queue_status(request: Request):
    """Get download queue status."""
    return {
        "queue_size": request.app.state.download_queue.qsize(),
        "max_size": request.app.state.download_queue.maxsize,
        "tasks_running": len(request.app.state.background_tasks)
    }
