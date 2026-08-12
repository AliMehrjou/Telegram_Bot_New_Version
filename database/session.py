"""
database/session.py

v3.1 SCALING UPGRADE: Added read/write split for horizontal DB scaling.
- `engine` (primary): used for INSERT, UPDATE, DELETE
- `replica_engine` (read replica): used for SELECT queries (offloads primary)

At 200K users with ~1,500-2,500 queries/sec, a single MySQL primary cannot
keep up. Adding 2-3 read replicas distributes the load.

Usage in handlers:
    # Write
    async with async_session_factory() as session:
        session.add(user)
        await session.commit()

    # Read (use the replica)
    async with async_read_session_factory() as session:
        result = await session.execute(select(User).where(...))

For backward compat, `async_session_factory` continues to use primary.
Read-heavy queries should explicitly use `async_read_session_factory`.

FIX PHASE3-CRIT-02: connect_args["init_command"] = "SET time_zone = '+00:00'"
added to both engines so every connection explicitly sets its session
timezone to UTC. Even with `default-time-zone='+00:00'` in the MySQL cnf,
this is a defense-in-depth: if a connection is ever established to a
mis-configured MySQL server, the session timezone is still correct.

FIX PHASE3-H-08: pool_recycle reduced from 1800 to 1500 so it is strictly
less than MySQL's `wait_timeout` (now 1800 in primary.cnf). Previously
pool_recycle=1800 == wait_timeout=600 (old value) → idle connections were
killed by MySQL before SQLAlchemy recycled them, causing "MySQL server
has gone away" errors. The rule is: pool_recycle MUST be < wait_timeout
(with some margin to absorb clock drift and re-establishment latency).
"""

import os
import time
import logging
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from matching_bot_project.services.metrics import metrics

logger = logging.getLogger(__name__)

# FIX PHASE3-H-08: pool_recycle must be strictly less than MySQL's wait_timeout.
# We read wait_timeout from env (default 1800, matching primary.cnf) and
# subtract a 300-second safety margin. This guarantees SQLAlchemy recycles
# connections BEFORE MySQL kills them.
_WAIT_TIMEOUT = int(os.getenv("DB_WAIT_TIMEOUT", "1800"))
_POOL_RECYCLE = max(60, _WAIT_TIMEOUT - 300)  # never less than 60s


def _build_database_url() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        if explicit.startswith("mysql+aiomysql://"):
            return explicit.replace("mysql+aiomysql://", "mysql+asyncmy://", 1)
        return explicit
    user = os.getenv("DB_USER", "match_bot_user")
    password = os.getenv("DB_PASSWORD", "match_bot_password")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "3306")
    name = os.getenv("DB_NAME", "match_bot_db")
    return f"mysql+asyncmy://{user}:{password}@{host}:{port}/{name}"


def _build_replica_url() -> str | None:
    """Build read replica URL. Returns None if no replica configured."""
    explicit = os.getenv("DB_REPLICA_URL")
    if explicit:
        if explicit.startswith("mysql+aiomysql://"):
            return explicit.replace("mysql+aiomysql://", "mysql+asyncmy://", 1)
        return explicit
    # Try DB_REPLICA_HOST (uses same DB name/credentials as primary)
    replica_host = os.getenv("DB_REPLICA_HOST")
    if not replica_host:
        return None
    user = os.getenv("DB_USER", "match_bot_user")
    password = os.getenv("DB_PASSWORD", "match_bot_password")
    port = os.getenv("DB_PORT", "3306")
    name = os.getenv("DB_NAME", "match_bot_db")
    return f"mysql+asyncmy://{user}:{password}@{replica_host}:{port}/{name}"


DATABASE_URL = _build_database_url()
REPLICA_URL = _build_replica_url()

# FIX PHASE3-CRIT-02: per-connection timezone init. asyncmy supports
# `init_command` via connect_args, which is executed once when the connection
# is established. This guarantees every session uses UTC regardless of the
# MySQL server's global default.
_CONNECT_ARGS = {
    "init_command": "SET time_zone = '+00:00'",
}

# ─── Primary engine (for writes) ────────────────────────────────────────────
# v3.1: Larger pool for high-throughput production. At 200K users with 4
# FastAPI workers × 50 concurrent requests each = 200 active connections.
# pool_size=30 + max_overflow=70 = up to 100 connections per worker.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    # تغییر مقادیر پیش‌فرض به 15 و 30 برای حفظ سقف 45 کانکشن در هر پروسه
    pool_size=int(os.getenv("DB_POOL_SIZE", "15")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "30")),
    pool_recycle=_POOL_RECYCLE,  
    pool_pre_ping=True,
    pool_timeout=30,
    connect_args=_CONNECT_ARGS,  
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# ─── Replica engine (for reads) ─────────────────────────────────────────────
# If no replica is configured, fall back to primary (single-node dev mode).
replica_engine = None
async_read_session_factory = async_session_factory  # default fallback

if REPLICA_URL:
    replica_engine = create_async_engine(
        REPLICA_URL,
        echo=False,
        pool_size=int(os.getenv("DB_REPLICA_POOL_SIZE", "50")),
        max_overflow=int(os.getenv("DB_REPLICA_MAX_OVERFLOW", "100")),
        pool_recycle=_POOL_RECYCLE,
        pool_pre_ping=True,
        pool_timeout=30,
        connect_args=_CONNECT_ARGS,
    )
    async_read_session_factory = async_sessionmaker(
        bind=replica_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    logger.info(f"Read replica configured: {REPLICA_URL.split('@')[-1] if '@' in REPLICA_URL else 'unknown'}")
else:
    logger.info("No DB_REPLICA_URL configured — reads will use primary (single-node mode).")

logger.info(f"DB pool_recycle={_POOL_RECYCLE}s (MySQL wait_timeout={_WAIT_TIMEOUT}s)")


# ─── Metrics Event Listeners ────────────────────────────────────────────────
# شنودگرها را بعد از ساخته شدن Engine قرار دادیم و نام‌ها اصلاح شده‌اند.

@event.listens_for(engine.sync_engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._query_start_time = time.time()

@event.listens_for(engine.sync_engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    total_time = time.time() - context._query_start_time
    operation = statement.split()[0].upper() if statement else "UNKNOWN"
    metrics.record_db_query(operation=operation, duration_sec=total_time)

if replica_engine:
    @event.listens_for(replica_engine.sync_engine, "before_cursor_execute")
    def before_cursor_execute_replica(conn, cursor, statement, parameters, context, executemany):
        context._query_start_time = time.time()

    @event.listens_for(replica_engine.sync_engine, "after_cursor_execute")
    def after_cursor_execute_replica(conn, cursor, statement, parameters, context, executemany):
        total_time = time.time() - context._query_start_time
        operation = statement.split()[0].upper() if statement else "UNKNOWN"
        metrics.record_db_query(operation=operation, duration_sec=total_time)


Base = declarative_base()

async def get_db_session():
    """Context manager for write session (primary)."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_read_db_session():
    """Context manager for read session (replica or primary fallback).

    FIX PHASE3-M-07: previously the exception path did NOT call rollback,
    so a failed SELECT would leave the session in a dirty state and the
    connection would be returned to the pool with an open transaction.
    Now we rollback on exception (even though read-only, the transaction
    state needs to be cleaned up before the connection is returned).
    """
    async with async_read_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            # Read-only — no commit needed, but ensure the transaction is
            # closed before the connection returns to the pool.
            try:
                await session.rollback()
            except Exception:
                pass


async def dispose_engine() -> None:
    """Properly dispose all engines on shutdown."""
    await engine.dispose()
    if replica_engine:
        await replica_engine.dispose()
    logger.info("All DB engines disposed.")