"""Database connection pool and schema initialization using asyncpg."""

import logging
from typing import Optional
import asyncpg
from app.config import Settings

logger = logging.getLogger("app.database")

SCHEMA_DDL = """
-- Extensions
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users Table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'agent',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Refresh Tokens Table
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) UNIQUE NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tickets Table
CREATE TABLE IF NOT EXISTS tickets (
    id UUID PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    customer_email VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'open',
    priority VARCHAR(50) NOT NULL DEFAULT 'medium',
    category VARCHAR(50),
    ai_summary TEXT,
    manual_triage_required BOOLEAN NOT NULL DEFAULT FALSE,
    created_by_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_to_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ticket Notes Table
CREATE TABLE IF NOT EXISTS ticket_notes (
    id UUID PRIMARY KEY,
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Reminders Table
CREATE TABLE IF NOT EXISTS reminders (
    id UUID PRIMARY KEY,
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    created_by_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scheduled_for TIMESTAMPTZ NOT NULL,
    message TEXT NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Notifications Table (event log of fired reminders)
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY,
    reminder_id UUID REFERENCES reminders(id) ON DELETE SET NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for Trigram Search & Performance
CREATE INDEX IF NOT EXISTS idx_tickets_title_trgm ON tickets USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_tickets_desc_trgm ON tickets USING gin (description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_tickets_status_priority ON tickets (status, priority);
CREATE INDEX IF NOT EXISTS idx_tickets_category ON tickets (category);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON tickets (assigned_to_id);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens (token_hash);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens (user_id);
CREATE INDEX IF NOT EXISTS idx_ticket_notes_ticket_id ON ticket_notes (ticket_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (scheduled_for, status);
CREATE INDEX IF NOT EXISTS idx_reminders_ticket_id ON reminders (ticket_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications (user_id, created_at DESC);
"""


async def create_db_pool(settings: Settings) -> asyncpg.Pool:
    """Creates a configured asyncpg connection pool."""
    logger.info("Initializing asyncpg connection pool...")
    return await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=settings.DB_POOL_MIN_SIZE,
        max_size=settings.DB_POOL_MAX_SIZE,
        command_timeout=settings.DB_POOL_TIMEOUT,
    )


async def init_db(pool: asyncpg.Pool) -> None:
    """Initializes database schema only if not already initialized (first run setup)."""
    async with pool.acquire() as connection:
        table_exists = await connection.fetchval("SELECT to_regclass('public.users')")
        if not table_exists:
            logger.info("First-time setup detected. Initializing database schema...")
            await connection.execute(SCHEMA_DDL)
            logger.info("Database schema initialized successfully.")
        else:
            logger.info("Database schema verified (persistent storage found). Skipping DDL execution.")


async def close_db_pool(pool: Optional[asyncpg.Pool]) -> None:
    """Closes the asyncpg connection pool cleanly."""
    if pool:
        logger.info("Closing asyncpg connection pool...")
        await pool.close()
        logger.info("asyncpg connection pool closed.")
