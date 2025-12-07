"""
TeleMinion V2 Database Layer

PostgreSQL database operations using asyncpg.
Includes custom PostgresSession for Telethon.
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import asyncpg
from telethon.sessions import MemorySession

from .config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# PostgresSession - Custom Telethon Session Storage
# =============================================================================

class PostgresSession(MemorySession):
    """
    Custom Telethon session that stores auth data in PostgreSQL.
    Inherits from MemorySession for runtime operations.
    """
    
    def __init__(self, session_id: str, pool: asyncpg.Pool):
        super().__init__()
        self._session_id = session_id
        self._pool = pool
    
    async def load_session(self):
        """Load session data from database."""
        try:
            row = await self._pool.fetchrow(
                "SELECT dc_id, server_address, port, auth_key FROM telegram_sessions WHERE session_id = $1",
                self._session_id
            )
            if row:
                self._dc_id = row['dc_id']
                self._server_address = row['server_address']
                self._port = row['port']
                if row['auth_key']:
                    from telethon.crypto import AuthKey
                    self._auth_key = AuthKey(row['auth_key'])
                logger.info(f"Loaded session: {self._session_id}")
        except Exception as e:
            logger.error(f"Failed to load session: {e}")
    
    async def save_session(self):
        """Save session data to database."""
        try:
            auth_key_bytes = self._auth_key.key if self._auth_key else None
            await self._pool.execute("""
                INSERT INTO telegram_sessions (session_id, dc_id, server_address, port, auth_key, updated_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    dc_id = EXCLUDED.dc_id,
                    server_address = EXCLUDED.server_address,
                    port = EXCLUDED.port,
                    auth_key = EXCLUDED.auth_key,
                    updated_at = NOW()
            """, self._session_id, self._dc_id, self._server_address, self._port, auth_key_bytes)
            logger.info(f"Saved session: {self._session_id}")
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
    
    def set_dc(self, dc_id, server_address, port):
        """Override to track DC changes."""
        super().set_dc(dc_id, server_address, port)
        self._dc_id = dc_id
        self._server_address = server_address
        self._port = port


# =============================================================================
# Database Connection Pool
# =============================================================================

async def create_db_pool() -> asyncpg.Pool:
    """Create and return a connection pool."""
    dsn = settings.DATABASE_URL
    pool = await asyncpg.create_pool(
        dsn,
        min_size=2,
        max_size=10,
        command_timeout=60
    )
    logger.info("Database pool created")
    return pool


# =============================================================================
# Schema Initialization
# =============================================================================

async def init_database(pool: asyncpg.Pool):
    """Initialize database schema with V2 columns."""
    async with pool.acquire() as conn:
        # Telegram sessions table
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
        
        # Channels table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id BIGINT PRIMARY KEY,
                name VARCHAR(255),
                username VARCHAR(255),
                is_active BOOLEAN DEFAULT true,
                last_scanned_message_id BIGINT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Files table with V2 columns
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id SERIAL PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                file_name VARCHAR(512),
                file_size BIGINT,
                file_type VARCHAR(50),
                mime_type VARCHAR(100),
                status VARCHAR(30) DEFAULT 'PENDING',
                destination_category VARCHAR(50),
                minio_path VARCHAR(512),
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                content_hash VARCHAR(64),
                processing_status VARCHAR(50) DEFAULT 'PENDING_PROCESSING',
                transcript_available BOOLEAN DEFAULT false,
                chunk_count INTEGER DEFAULT 0,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(channel_id, message_id)
            )
        """)
        
        # V2 schema migrations - add columns if they don't exist
        migrations = [
            ("files", "mime_type", "VARCHAR(100)"),
            ("files", "destination_category", "VARCHAR(50)"),
            ("files", "retry_count", "INTEGER DEFAULT 0"),
            ("files", "content_hash", "VARCHAR(64)"),
            ("files", "processing_status", "VARCHAR(50) DEFAULT 'PENDING_PROCESSING'"),
            ("files", "transcript_available", "BOOLEAN DEFAULT false"),
            ("files", "chunk_count", "INTEGER DEFAULT 0"),
            ("files", "processed_at", "TIMESTAMP"),
            # Channels table migrations
            ("channels", "created_at", "TIMESTAMP DEFAULT NOW()"),
            ("channels", "updated_at", "TIMESTAMP DEFAULT NOW()"),
        ]
        
        for table, column, col_type in migrations:
            try:
                await conn.execute(f"""
                    ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}
                """)
            except Exception as e:
                logger.debug(f"Column {column} may already exist: {e}")
        
        # Create indexes for performance
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)",
            "CREATE INDEX IF NOT EXISTS idx_files_status_created ON files(status, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_files_processing_status ON files(processing_status)",
            "CREATE INDEX IF NOT EXISTS idx_files_content_hash ON files(content_hash)",
            "CREATE INDEX IF NOT EXISTS idx_files_channel_id ON files(channel_id)",
            "CREATE INDEX IF NOT EXISTS idx_files_category ON files(destination_category)",
            "CREATE INDEX IF NOT EXISTS idx_channels_active ON channels(is_active)",
        ]
        
        for idx_sql in indexes:
            try:
                await conn.execute(idx_sql)
            except Exception as e:
                logger.debug(f"Index may already exist: {e}")
        
        logger.info("Database schema initialized")


# =============================================================================
# File Operations
# =============================================================================

async def insert_file(pool: asyncpg.Pool, data: Dict[str, Any]) -> Optional[int]:
    """Insert a new file record."""
    try:
        row = await pool.fetchrow("""
            INSERT INTO files (channel_id, message_id, file_name, file_size, file_type, mime_type, 
                               destination_category, content_hash)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (channel_id, message_id) DO NOTHING
            RETURNING id
        """, 
            data.get('channel_id'),
            data.get('message_id'),
            data.get('file_name'),
            data.get('file_size'),
            data.get('file_type'),
            data.get('mime_type'),
            data.get('destination_category'),
            data.get('content_hash')
        )
        return row['id'] if row else None
    except Exception as e:
        logger.error(f"Failed to insert file: {e}")
        return None


async def get_file_by_id(pool: asyncpg.Pool, file_id: int) -> Optional[Dict]:
    """Get a file by ID with channel info."""
    row = await pool.fetchrow("""
        SELECT f.*, c.name as channel_name, c.username as channel_username
        FROM files f
        LEFT JOIN channels c ON f.channel_id = c.id
        WHERE f.id = $1
    """, file_id)
    return dict(row) if row else None


async def get_files_by_status(
    pool: asyncpg.Pool, 
    status: str,
    page: int = 1,
    per_page: int = 50,
    sort_by: str = "created_at",
    sort_order: str = "desc"
) -> tuple[List[Dict], int]:
    """Get files by status with pagination and sorting."""
    # Validate sort column
    allowed_sorts = ["created_at", "file_name", "file_size", "file_type", "destination_category"]
    if sort_by not in allowed_sorts:
        sort_by = "created_at"
    if sort_order.lower() not in ["asc", "desc"]:
        sort_order = "desc"
    
    offset = (page - 1) * per_page
    
    # Get total count
    total = await pool.fetchval(
        "SELECT COUNT(*) FROM files WHERE status = $1",
        status
    )
    
    # Get paginated results
    rows = await pool.fetch(f"""
        SELECT f.*, c.name as channel_name, c.username as channel_username
        FROM files f
        LEFT JOIN channels c ON f.channel_id = c.id
        WHERE f.status = $1
        ORDER BY f.{sort_by} {sort_order}
        LIMIT $2 OFFSET $3
    """, status, per_page, offset)
    
    return [dict(r) for r in rows], total


async def get_active_files(pool: asyncpg.Pool) -> List[Dict]:
    """Get files with active statuses (QUEUED, DOWNLOADING, UPLOADING)."""
    rows = await pool.fetch("""
        SELECT f.*, c.name as channel_name, c.username as channel_username
        FROM files f
        LEFT JOIN channels c ON f.channel_id = c.id
        WHERE f.status IN ('QUEUED', 'DOWNLOADING', 'UPLOADING')
        ORDER BY f.updated_at DESC
    """)
    return [dict(r) for r in rows]


async def get_history_files(
    pool: asyncpg.Pool,
    page: int = 1,
    per_page: int = 50
) -> tuple[List[Dict], List[Dict], int]:
    """Get completed and failed files for history view."""
    offset = (page - 1) * per_page
    
    # Failed files (always show all)
    failed_rows = await pool.fetch("""
        SELECT f.*, c.name as channel_name, c.username as channel_username
        FROM files f
        LEFT JOIN channels c ON f.channel_id = c.id
        WHERE f.status IN ('FAILED', 'FAILED_PERMANENT')
        ORDER BY f.updated_at DESC
    """)
    
    # Completed files (paginated)
    completed_rows = await pool.fetch("""
        SELECT f.*, c.name as channel_name, c.username as channel_username
        FROM files f
        LEFT JOIN channels c ON f.channel_id = c.id
        WHERE f.status = 'COMPLETED'
        ORDER BY f.updated_at DESC
        LIMIT $1 OFFSET $2
    """, per_page, offset)
    
    # Total completed
    total = await pool.fetchval(
        "SELECT COUNT(*) FROM files WHERE status = 'COMPLETED'"
    )
    
    return [dict(r) for r in failed_rows], [dict(r) for r in completed_rows], total


async def update_file_status(
    pool: asyncpg.Pool, 
    file_id: int, 
    status: str, 
    **kwargs
) -> bool:
    """Update file status and optional fields."""
    updates = ["status = $2", "updated_at = NOW()"]
    values = [file_id, status]
    param_idx = 3
    
    for key, value in kwargs.items():
        if key in ['minio_path', 'error_message', 'destination_category', 'content_hash', 
                   'processing_status', 'retry_count']:
            updates.append(f"{key} = ${param_idx}")
            values.append(value)
            param_idx += 1
    
    query = f"UPDATE files SET {', '.join(updates)} WHERE id = $1"
    
    try:
        await pool.execute(query, *values)
        return True
    except Exception as e:
        logger.error(f"Failed to update file {file_id}: {e}")
        return False


async def increment_retry_count(pool: asyncpg.Pool, file_id: int) -> int:
    """Increment retry count and return new value."""
    row = await pool.fetchrow("""
        UPDATE files 
        SET retry_count = retry_count + 1, updated_at = NOW()
        WHERE id = $1
        RETURNING retry_count
    """, file_id)
    return row['retry_count'] if row else 0


async def update_file_category(pool: asyncpg.Pool, file_id: int, category: str) -> bool:
    """Update file destination category."""
    try:
        await pool.execute("""
            UPDATE files SET destination_category = $2, updated_at = NOW()
            WHERE id = $1
        """, file_id, category)
        return True
    except Exception as e:
        logger.error(f"Failed to update category for file {file_id}: {e}")
        return False


async def mark_file_processed(pool: asyncpg.Pool, file_id: int, data: Dict) -> bool:
    """Mark file as processed by n8n."""
    try:
        await pool.execute("""
            UPDATE files SET 
                processing_status = $2,
                transcript_available = $3,
                chunk_count = $4,
                processed_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
        """, 
            file_id, 
            data.get('processing_status', 'PROCESSED'),
            data.get('has_transcript', False),
            data.get('chunk_count', 0)
        )
        return True
    except Exception as e:
        logger.error(f"Failed to mark file {file_id} as processed: {e}")
        return False


async def get_unprocessed_files(pool: asyncpg.Pool) -> List[Dict]:
    """Get files ready for n8n processing (uploaded but not processed)."""
    rows = await pool.fetch("""
        SELECT f.*, c.name as channel_name, c.username as channel_username
        FROM files f
        LEFT JOIN channels c ON f.channel_id = c.id
        WHERE f.status = 'COMPLETED' 
        AND f.processing_status = 'PENDING_PROCESSING'
        ORDER BY f.created_at ASC
        LIMIT 100
    """)
    return [dict(r) for r in rows]


async def check_content_hash_exists(pool: asyncpg.Pool, content_hash: str) -> Optional[int]:
    """Check if a file with this content hash already exists."""
    row = await pool.fetchrow(
        "SELECT id FROM files WHERE content_hash = $1",
        content_hash
    )
    return row['id'] if row else None


# =============================================================================
# Queue Recovery
# =============================================================================

async def get_queued_file_ids(pool: asyncpg.Pool) -> List[int]:
    """Get IDs of files with QUEUED status for queue recovery on restart."""
    rows = await pool.fetch(
        "SELECT id FROM files WHERE status = 'QUEUED' ORDER BY updated_at ASC"
    )
    return [r['id'] for r in rows]


async def reset_downloading_files(pool: asyncpg.Pool) -> int:
    """Reset files stuck in DOWNLOADING/UPLOADING to QUEUED on restart."""
    result = await pool.execute("""
        UPDATE files 
        SET status = 'QUEUED', updated_at = NOW()
        WHERE status IN ('DOWNLOADING', 'UPLOADING')
    """)
    count = int(result.split()[-1]) if result else 0
    if count > 0:
        logger.info(f"Reset {count} stuck files to QUEUED")
    return count


# =============================================================================
# Self-Healing
# =============================================================================

async def get_completed_files_for_healing(pool: asyncpg.Pool) -> List[Dict]:
    """Get completed files for self-healing check."""
    rows = await pool.fetch("""
        SELECT id, minio_path, destination_category
        FROM files 
        WHERE status = 'COMPLETED'
        AND status != 'FAILED_PERMANENT'
    """)
    return [dict(r) for r in rows]


async def revert_file_to_pending(pool: asyncpg.Pool, file_id: int) -> bool:
    """Revert a file to PENDING status (for self-healing)."""
    try:
        await pool.execute("""
            UPDATE files 
            SET status = 'PENDING', minio_path = NULL, updated_at = NOW()
            WHERE id = $1
        """, file_id)
        logger.info(f"Reverted file {file_id} to PENDING (self-healing)")
        return True
    except Exception as e:
        logger.error(f"Failed to revert file {file_id}: {e}")
        return False


# =============================================================================
# Channel Operations
# =============================================================================

async def get_active_channels(pool: asyncpg.Pool) -> List[Dict]:
    """Get all active channels."""
    rows = await pool.fetch(
        "SELECT * FROM channels WHERE is_active = true ORDER BY name"
    )
    return [dict(r) for r in rows]


async def get_channel_by_id(pool: asyncpg.Pool, channel_id: int) -> Optional[Dict]:
    """Get a channel by ID."""
    row = await pool.fetchrow("SELECT * FROM channels WHERE id = $1", channel_id)
    return dict(row) if row else None


async def insert_channel(pool: asyncpg.Pool, data: Dict) -> bool:
    """Insert or update a channel."""
    try:
        await pool.execute("""
            INSERT INTO channels (id, name, username)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                username = EXCLUDED.username,
                is_active = true,
                updated_at = NOW()
        """, data['id'], data.get('name'), data.get('username'))
        return True
    except Exception as e:
        logger.error(f"Failed to insert channel: {e}")
        return False


async def update_channel_last_scanned(pool: asyncpg.Pool, channel_id: int, message_id: int):
    """Update the last scanned message ID for a channel."""
    await pool.execute("""
        UPDATE channels SET last_scanned_message_id = $2, updated_at = NOW()
        WHERE id = $1
    """, channel_id, message_id)


async def deactivate_channel(pool: asyncpg.Pool, channel_id: int) -> bool:
    """Deactivate a channel."""
    try:
        await pool.execute("""
            UPDATE channels SET is_active = false, updated_at = NOW()
            WHERE id = $1
        """, channel_id)
        return True
    except Exception as e:
        logger.error(f"Failed to deactivate channel: {e}")
        return False


# =============================================================================
# Statistics
# =============================================================================

async def get_dashboard_stats(pool: asyncpg.Pool) -> Dict:
    """Get statistics for dashboard."""
    stats = await pool.fetchrow("""
        SELECT 
            COUNT(*) FILTER (WHERE status = 'PENDING') as pending_files,
            COUNT(*) FILTER (WHERE status IN ('QUEUED', 'DOWNLOADING', 'UPLOADING')) as active_files,
            COUNT(*) FILTER (WHERE status = 'COMPLETED') as completed_files,
            COUNT(*) FILTER (WHERE status IN ('FAILED', 'FAILED_PERMANENT')) as failed_files,
            (SELECT COUNT(*) FROM channels WHERE is_active = true) as active_channels
        FROM files
    """)
    return dict(stats) if stats else {
        'pending_files': 0,
        'active_files': 0,
        'completed_files': 0,
        'failed_files': 0,
        'active_channels': 0
    }
