import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional
from app.config import settings

logger = logging.getLogger(__name__)

# Approximate pricing per 1k tokens for popular models
MODEL_PRICING_PER_1K = {
    "gpt-4o": {"prompt": 0.005, "completion": 0.015},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "claude-3-5-sonnet-20241022": {"prompt": 0.003, "completion": 0.015},
    "claude-3-5-sonnet-20240620": {"prompt": 0.003, "completion": 0.015},
    "claude-3-haiku-20240307": {"prompt": 0.00025, "completion": 0.00125},
}


def estimate_tokens(text_str: str) -> int:
    """Heuristic token estimation: ~4 chars per token if tokenizer is not loaded."""
    if not text_str:
        return 0
    return max(1, len(text_str) // 4)


def calculate_cost(prompt_tokens: int, completion_tokens: int, model: str = settings.LLM_MODEL) -> float:
    pricing = MODEL_PRICING_PER_1K.get(model, {"prompt": 0.003, "completion": 0.015})
    cost = (prompt_tokens / 1000.0) * pricing["prompt"] + (completion_tokens / 1000.0) * pricing["completion"]
    return round(cost, 6)


class AuditLogger:
    """Maintains an append-only SQLite database for audit and compliance using async-safe operations."""

    def __init__(self, db_path: str = settings.AUDIT_LOG_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        question TEXT NOT NULL,
                        attempt INTEGER NOT NULL,
                        reasoning_plan TEXT,
                        sql_query TEXT,
                        sql_dialect TEXT NOT NULL,
                        dialect_valid BOOLEAN NOT NULL,
                        ast_valid BOOLEAN NOT NULL,
                        execution_success BOOLEAN NOT NULL,
                        error_message TEXT,
                        latency_ms REAL,
                        tokens_used INTEGER,
                        cost_usd REAL,
                        is_write BOOLEAN DEFAULT 0,
                        user_id TEXT,
                        affected_rows INTEGER DEFAULT 0
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS consumed_tokens (
                        jti TEXT PRIMARY KEY,
                        consumed_at TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        sql_query TEXT
                    );
                """)
                # Migrations for existing DB files
                for col_def in [
                    ("is_write", "BOOLEAN DEFAULT 0"),
                    ("user_id", "TEXT"),
                    ("affected_rows", "INTEGER DEFAULT 0"),
                ]:
                    try:
                        cursor.execute(f"ALTER TABLE audit_logs ADD COLUMN {col_def[0]} {col_def[1]}")
                    except sqlite3.OperationalError:
                        pass

                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_success ON audit_logs(execution_success);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_is_write ON audit_logs(is_write);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_consumed_at ON consumed_tokens(consumed_at);")
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not initialize SQLite audit database at {self.db_path}: {e}")

    def _sync_log_attempt(
        self,
        question: str,
        attempt: int,
        reasoning_plan: Optional[str],
        sql_query: Optional[str],
        sql_dialect: str,
        dialect_valid: bool,
        ast_valid: bool,
        execution_success: bool,
        error_message: Optional[str] = None,
        latency_ms: float = 0.0,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
        is_write: bool = False,
        user_id: Optional[str] = None,
        affected_rows: int = 0,
    ) -> None:
        """Appends a structured log entry synchronously into the SQLite audit table."""
        timestamp_str = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO audit_logs (
                        timestamp, question, attempt, reasoning_plan, sql_query,
                        sql_dialect, dialect_valid, ast_valid, execution_success,
                        error_message, latency_ms, tokens_used, cost_usd,
                        is_write, user_id, affected_rows
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp_str,
                        question,
                        attempt,
                        reasoning_plan,
                        sql_query,
                        sql_dialect,
                        dialect_valid,
                        ast_valid,
                        execution_success,
                        error_message,
                        latency_ms,
                        tokens_used,
                        cost_usd,
                        1 if is_write else 0,
                        user_id,
                        affected_rows,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to record audit log: {e}")

    async def log_attempt(self, *args, **kwargs) -> None:
        """Async-safe non-blocking wrapper to record audit log attempts."""
        await asyncio.to_thread(self._sync_log_attempt, *args, **kwargs)

    def log_attempt_sync(self, *args, **kwargs) -> None:
        """Synchronous wrapper for log_attempt."""
        self._sync_log_attempt(*args, **kwargs)

    def _sync_log_write_execution(
        self,
        user_id: str,
        sql_query: str,
        sql_dialect: str,
        affected_rows: int,
        execution_success: bool,
        error_message: Optional[str] = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Appends an explicit write-operation confirmation log entry synchronously."""
        timestamp_str = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO audit_logs (
                        timestamp, question, attempt, reasoning_plan, sql_query,
                        sql_dialect, dialect_valid, ast_valid, execution_success,
                        error_message, latency_ms, tokens_used, cost_usd,
                        is_write, user_id, affected_rows
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp_str,
                        f"[WRITE CONFIRMED] User {user_id} executed mutating SQL",
                        1,
                        "Explicit write transaction confirmed and committed",
                        sql_query,
                        sql_dialect,
                        True,
                        True,
                        execution_success,
                        error_message,
                        latency_ms,
                        0,
                        0.0,
                        1,
                        user_id,
                        affected_rows,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to record write audit log: {e}")

    async def log_write_execution(self, *args, **kwargs) -> None:
        """Async-safe non-blocking wrapper to record write execution audit records."""
        await asyncio.to_thread(self._sync_log_write_execution, *args, **kwargs)

    def log_write_execution_sync(self, *args, **kwargs) -> None:
        """Synchronous wrapper for log_write_execution."""
        self._sync_log_write_execution(*args, **kwargs)

    def _sync_consume_preview_token(self, jti: str, user_id: str, sql_query: str) -> bool:
        """Atomically marks a preview token (jti) as consumed synchronously."""
        timestamp_str = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO consumed_tokens (jti, consumed_at, user_id, sql_query)
                    VALUES (?, ?, ?, ?)
                    """,
                    (jti, timestamp_str, user_id, sql_query),
                )
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            logger.error(f"Failed to record consumed token: {e}")
            return False

    async def consume_preview_token(self, jti: str, user_id: str, sql_query: str) -> bool:
        """Async-safe non-blocking token consumption check."""
        return await asyncio.to_thread(self._sync_consume_preview_token, jti, user_id, sql_query)

    def consume_preview_token_sync(self, jti: str, user_id: str, sql_query: str) -> bool:
        """Synchronous wrapper for consume_preview_token."""
        return self._sync_consume_preview_token(jti, user_id, sql_query)

    def _sync_get_recent_logs(self, limit: int = 50, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves recent audit logs synchronously, optionally filtered by user_id."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                if user_id:
                    cursor.execute(
                        "SELECT * FROM audit_logs WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                        (user_id, limit),
                    )
                else:
                    cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to fetch audit logs: {e}")
            return []

    async def get_recent_logs(self, limit: int = 50, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Async-safe non-blocking retrieval of recent audit logs."""
        return await asyncio.to_thread(self._sync_get_recent_logs, limit, user_id)

    def get_recent_logs_sync(self, limit: int = 50, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Synchronous wrapper for get_recent_logs."""
        return self._sync_get_recent_logs(limit, user_id)


# Global audit logger instance
audit_logger = AuditLogger()

