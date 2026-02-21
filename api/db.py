"""
FILE: api/db.py
Role: asyncpg connection pool — shared across all routes.
Agent boundary: API — database layer (§7, §10)
Dependencies: DATABASE_URL env var (postgresql://user:pass@host:port/db)
Output: get_pool() and get_conn() helpers used by all route handlers
How to test: From FastAPI startup event, pool should connect without error.
             psql $DATABASE_URL -c 'SELECT 1'
"""

import asyncpg
import os
from typing import AsyncGenerator

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    """
    Initialise the asyncpg connection pool.
    Called once from FastAPI lifespan startup.
    """
    global _pool
    database_url = os.environ["DATABASE_URL"]
    _pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )


async def close_pool() -> None:
    """Close the pool on shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the active pool. Raises if init_pool() was never called."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_pool() at startup")
    return _pool


async def get_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    FastAPI dependency: yields a single connection from the pool.
    Usage in routes:
        async def my_endpoint(conn=Depends(get_conn)):
            row = await conn.fetchrow("SELECT ...")
    """
    async with get_pool().acquire() as conn:
        yield conn
