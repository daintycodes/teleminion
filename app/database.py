"""
Database layer for TeleMinion.
Includes PostgresSession for Telethon and asyncpg pool management.
"""
import asyncio
import logging
from typing import Optional, Any
from datetime import datetime

import asyncpg
from telethon.sessions import MemorySession
from telethon.crypto import AuthKey
from telethon.tl.types import (
    InputPeerUser, InputPeerChat, InputPeerChannel,
    PeerUser, PeerChat, PeerChannel
)

from .config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# PostgresSession - Custom Telethon Session Storage
# =============================================================================

class PostgresSession(MemorySession):
    """
    Custom Telethon session that stores authentication data in PostgreSQL.
    
    Inherits from MemorySession for in-memory operations during runtime,
    and persists to PostgreSQL for durability across container restarts.
    
    This avoids SQLite "database is locked" errors in Docker environments.
    """
    
    def __init__(self, session_id: str, pool: asyncpg.Pool):
        super().__init__()
        self._session_id = session_id
        self._pool = pool
        self._loop = None
    
    def _get_loop(self):
        """Get or create event loop for sync operations."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            return self._loop
    
    def _run_sync(self, coro):
        """Run async operation synchronously."""
        loop = self._get_loop()
        if loop.is_running():
            # Create a new task and wait for it
            future = asyncio.ensure_future(coro, loop=loop)
            # We need to return immediately and handle this differently
            # For now, we'll use a thread-safe approach
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                new_loop = asyncio.new_event_loop()
                future = executor.submit(new_loop.run_until_complete, coro)
                return future.result(timeout=30)
        else:
            return loop.run_until_complete(coro)
    
    async def load_session(self) -> bool:
        """Load session data from PostgreSQL."""
        try:
            row = await self._pool.fetchrow(
                """
                SELECT dc_id, server_address, port, auth_key, takeout_id
                FROM telegram_sessions
                WHERE session_id = $1
                """,
                self._session_id
            )
            
            if row:
                self._dc_id = row['dc_id']
                self._server_address = row['server_address']
                self._port = row['port']
                if row['auth_key']:
                    self._auth_key = AuthKey(row['auth_key'])
                self._takeout_id = row['takeout_id']
                logger.info(f"Loaded session for {self._session_id}")
                return True
            
            logger.info(f"No existing session found for {self._session_id}")
            return False
            
        except Exception as e:
            logger.error(f"Error loading session: {e}")
            return False
    
    async def save_session(self):
        """Save session data to PostgreSQL."""
        try:
            auth_key_bytes = self._auth_key.key if self._auth_key else None
            
            await self._pool.execute(
                """
                INSERT INTO telegram_sessions 
                    (session_id, dc_id, server_address, port, auth_key, takeout_id, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (session_id) DO UPDATE SET
                    dc_id = EXCLUDED.dc_id,
                    server_address = EXCLUDED.server_address,
                    port = EXCLUDED.port,
                    auth_key = EXCLUDED.auth_key,
                    takeout_id = EXCLUDED.takeout_id,
                    updated_at = EXCLUDED.updated_at
                """,
                self._session_id,
                self._dc_id,
                self._server_address,
                self._port,
                auth_key_bytes,
                self._takeout_id,
                datetime.utcnow()
            )
            logger.debug(f"Saved session for {self._session_id}")
            
        except Exception as e:
            logger.error(f"Error saving session: {e}")
            raise
    
    def set_dc(self, dc_id: int, server_address: str, port: int):
        """Set DC info and trigger save."""
        super().set_dc(dc_id, server_address, port)
        # Schedule async save
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(self.save_session())
        except RuntimeError:
            pass  # No running loop, will be saved later
    
    @property
    def auth_key(self):
        return self._auth_key
    
    @auth_key.setter
    def auth_key(self, value):
        self._auth_key = value
        # Schedule async save
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(self.save_session())
        except RuntimeError:
            pass
    
    def save(self):
        """Sync save - create task in running loop."""
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(self.save_session())
        except RuntimeError:
            # No running loop
            pass
    
    def delete(self):
        """Delete session from database."""
        async def _delete():
            await self._pool.execute(
                "DELETE FROM telegram_sessions WHERE session_id = $1",
                self._session_id
            )
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(_delete())
        except RuntimeError:
            pass
    
    def clone(self, to_instance=None):
        cloned = PostgresSession(self._session_id, self._pool)
        cloned._dc_id = self._dc_id
        cloned._server_address = self._server_address  
        cloned._port = self._port
        cloned._auth_key = self._auth_key
        cloned._takeout_id = self._takeout_id
        return cloned


# =============================================================================
# Database Pool and Schema Management
# =============================================================================

async def create_db_pool() -> asyncpg.Pool:
    """Create asyncpg connection pool."""
    # Parse DATABASE_URL
    pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=60
    )
    logger.info("Database connection pool created")
    return pool


async def init_database(pool: asyncpg.Pool):
    """Initialize database schema."""
    async with pool.acquire() as conn:
        # Create telegram_sessions table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_sessions (
                session_id VARCHAR(255) PRIMARY KEY,
                dc_id INTEGER,
                server_address VARCHAR(255),
                port INTEGER,
                auth_key BYTEA,
                takeout_id INTEGER,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Create channels table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id BIGINT PRIMARY KEY,
                name VARCHAR(255),
                username VARCHAR(255),
                last_scanned_message_id BIGINT DEFAULT 0,
                is_active BOOLEAN DEFAULT true,
                added_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Create files table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id SERIAL PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                file_name VARCHAR(512),
                file_size BIGINT,
                file_type VARCHAR(50),
                mime_type VARCHAR(100),
                status VARCHAR(20) DEFAULT 'PENDING',
                minio_path VARCHAR(512),
                error_message TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(channel_id, message_id)
            )
        """)
        
        # Create indexes
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_channel ON files(channel_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at DESC)
        """)
        
        logger.info("Database schema initialized")


# =============================================================================
# File Operations
# =============================================================================

async def get_files(
    pool: asyncpg.Pool,
    status: Optional[str] = None,
    channel_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 50
) -> tuple[list[dict], int]:
    """Get paginated files with optional filtering."""
    offset = (page - 1) * per_page
    
    # Build query
    where_clauses = []
    params = []
    param_count = 0
    
    if status:
        param_count += 1
        if status == "ACTIVE":
            where_clauses.append(f"f.status IN ('QUEUED', 'DOWNLOADING', 'UPLOADING')")
        else:
            where_clauses.append(f"f.status = ${param_count}")
            params.append(status)
    
    if channel_id:
        param_count += 1
        where_clauses.append(f"f.channel_id = ${param_count}")
        params.append(channel_id)
    
    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    
    async with pool.acquire() as conn:
        # Get total count
        count_query = f"SELECT COUNT(*) FROM files f WHERE {where_sql}"
        total = await conn.fetchval(count_query, *params)
        
        # Get files with channel name
        query = f"""
            SELECT f.*, c.name as channel_name, c.username as channel_username
            FROM files f
            LEFT JOIN channels c ON f.channel_id = c.id
            WHERE {where_sql}
            ORDER BY f.created_at DESC
            LIMIT ${param_count + 1} OFFSET ${param_count + 2}
        """
        params.extend([per_page, offset])
        
        rows = await conn.fetch(query, *params)
        files = [dict(row) for row in rows]
        
    return files, total


async def get_file_by_id(pool: asyncpg.Pool, file_id: int) -> Optional[dict]:
    """Get a single file by ID."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT f.*, c.name as channel_name, c.username as channel_username
            FROM files f
            LEFT JOIN channels c ON f.channel_id = c.id
            WHERE f.id = $1
            """,
            file_id
        )
        return dict(row) if row else None


async def update_file_status(
    pool: asyncpg.Pool,
    file_id: int,
    status: str,
    minio_path: Optional[str] = None,
    error_message: Optional[str] = None
):
    """Update file status."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE files 
            SET status = $2, 
                minio_path = COALESCE($3, minio_path),
                error_message = $4,
                updated_at = NOW()
            WHERE id = $1
            """,
            file_id, status, minio_path, error_message
        )


async def insert_file(pool: asyncpg.Pool, file_data: dict) -> int:
    """Insert a new file record. Returns file ID."""
    async with pool.acquire() as conn:
        file_id = await conn.fetchval(
            """
            INSERT INTO files (channel_id, message_id, file_name, file_size, file_type, mime_type)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (channel_id, message_id) DO NOTHING
            RETURNING id
            """,
            file_data['channel_id'],
            file_data['message_id'],
            file_data.get('file_name'),
            file_data.get('file_size'),
            file_data.get('file_type'),
            file_data.get('mime_type')
        )
        return file_id


# =============================================================================
# Channel Operations
# =============================================================================

async def get_channels(pool: asyncpg.Pool, active_only: bool = True) -> list[dict]:
    """Get all channels."""
    async with pool.acquire() as conn:
        query = "SELECT * FROM channels"
        if active_only:
            query += " WHERE is_active = true"
        query += " ORDER BY added_at DESC"
        
        rows = await conn.fetch(query)
        return [dict(row) for row in rows]


async def get_channel_by_id(pool: asyncpg.Pool, channel_id: int) -> Optional[dict]:
    """Get channel by ID."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM channels WHERE id = $1",
            channel_id
        )
        return dict(row) if row else None


async def insert_channel(pool: asyncpg.Pool, channel_data: dict):
    """Insert or update a channel."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO channels (id, name, username)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                username = EXCLUDED.username,
                is_active = true
            """,
            channel_data['id'],
            channel_data.get('name'),
            channel_data.get('username')
        )


async def update_channel_last_scanned(pool: asyncpg.Pool, channel_id: int, message_id: int):
    """Update last scanned message ID."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE channels 
            SET last_scanned_message_id = $2
            WHERE id = $1
            """,
            channel_id, message_id
        )


async def delete_channel(pool: asyncpg.Pool, channel_id: int):
    """Soft delete a channel (set inactive)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE channels SET is_active = false WHERE id = $1",
            channel_id
        )


async def get_channel_stats(pool: asyncpg.Pool) -> dict:
    """Get overall channel and file statistics."""
    async with pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT 
                (SELECT COUNT(*) FROM channels WHERE is_active = true) as active_channels,
                (SELECT COUNT(*) FROM files WHERE status = 'PENDING') as pending_files,
                (SELECT COUNT(*) FROM files WHERE status IN ('QUEUED', 'DOWNLOADING', 'UPLOADING')) as active_files,
                (SELECT COUNT(*) FROM files WHERE status = 'COMPLETED') as completed_files,
                (SELECT COUNT(*) FROM files WHERE status = 'FAILED') as failed_files
        """)
        return dict(stats)
