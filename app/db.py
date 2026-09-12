import logging
from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.config import settings

logger = logging.getLogger(__name__)

# Main database engine
main_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
)

# Read-only database engine for safe SQL execution
readonly_engine: AsyncEngine = create_async_engine(
    settings.effective_readonly_db_url,
    echo=settings.DEBUG,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
)

# Async session factories
AsyncSessionLocal = async_sessionmaker(
    bind=main_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

AsyncReadonlySessionLocal = async_sessionmaker(
    bind=readonly_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a write-capable session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_readonly_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a read-only session."""
    async with AsyncReadonlySessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def check_db_connection() -> dict:
    """Verifies connection health against the database."""
    try:
        async with main_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            value = result.scalar()
            return {"connected": True, "ping": value == 1}
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        return {"connected": False, "error": str(e)}
