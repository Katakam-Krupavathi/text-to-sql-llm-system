import asyncio
import logging
from typing import Dict, Optional, Tuple
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from app.config import settings
from app.crypto import mask_connection_string

logger = logging.getLogger(__name__)

# Cache of initialized async engines per connection_id
_engine_cache: Dict[str, AsyncEngine] = {}


async def test_and_probe_connection(
    connection_string: str, dialect: str
) -> Tuple[bool, bool, Optional[str], Optional[str]]:
    """
    Tests database connectivity and probes for write privileges.
    Returns: (is_valid, is_read_only, warning_message, error_message)
    """
    masked_uri = mask_connection_string(connection_string)
    logger.info(f"Testing DB connection to {masked_uri} (Dialect: {dialect})")

    temp_engine = None
    try:
        # Create temporary async engine for validation
        temp_engine = create_async_engine(
            connection_string,
            pool_pre_ping=True,
            connect_args={"timeout": 5} if "sqlite" not in connection_string.lower() else {},
        )

        # Step 1: Lightweight read-only test query
        async with temp_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            val = result.scalar()
            if val != 1:
                return False, True, None, "Test query 'SELECT 1' returned unexpected result."

        # Step 2: Write-probe check (Safety Requirement #7)
        is_read_only = True
        write_warning = None
        try:
            async with temp_engine.connect() as conn:
                # Attempt to create and drop a temporary probe table
                if "sqlite" in connection_string.lower():
                    await conn.execute(text("CREATE TABLE _readonly_probe_test (id INT)"))
                    await conn.execute(text("DROP TABLE _readonly_probe_test"))
                else:
                    await conn.execute(text("CREATE TEMPORARY TABLE _readonly_probe_test (id INT)"))
                    await conn.execute(text("DROP TABLE IF EXISTS _readonly_probe_test"))
                is_read_only = False
                write_warning = (
                    "This connection has write permissions; we recommend using a read-only database user for safety"
                )
        except Exception as write_err:
            logger.debug(f"Write probe rejected as expected for read-only user: {write_err}")
            is_read_only = True

        return True, is_read_only, write_warning, None

    except Exception as e:
        err_msg = str(e)
        logger.warning(f"Connection test failed for {masked_uri}: {err_msg}")
        return False, True, None, f"Failed to connect to database: {err_msg}"
    finally:
        if temp_engine:
            try:
                await temp_engine.dispose()
            except Exception:
                pass


def get_engine_for_connection(connection_id: str, connection_string: str) -> AsyncEngine:
    """Returns or creates a cached AsyncEngine for a given connection_id."""
    if connection_id not in _engine_cache:
        is_sqlite = "sqlite" in connection_string.lower()
        kwargs = {"pool_pre_ping": True}
        if not is_sqlite:
            kwargs.update({
                "pool_size": 5,
                "max_overflow": 10,
                "pool_timeout": 15,
            })
        _engine_cache[connection_id] = create_async_engine(
            connection_string,
            **kwargs,
        )
    return _engine_cache[connection_id]


async def close_engine_for_connection(connection_id: str) -> None:
    """Disposes and removes an engine from the cache."""
    engine = _engine_cache.pop(connection_id, None)
    if engine:
        await engine.dispose()
