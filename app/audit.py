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
    """Maintains an append-only SQLite database for audit and compliance."""

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
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_success ON audit_logs(execution_success);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_is_write ON audit_logs(is_write);")
                
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
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not initialize SQLite audit database at {self.db_path}: {e}")

    def log_attempt(
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
        """Appends a structured log entry into the SQLite audit table."""
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

    def log_write_execution(
        self,
        user_id: str,
        sql_query: str,
        sql_dialect: str,
        affected_rows: int,
        execution_success: bool,
        error_message: Optional[str] = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Appends an explicit write-operation confirmation log entry."""
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

    def get_recent_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves recent audit logs."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to fetch audit logs: {e}")
            return []


# Global audit logger instance
audit_logger = AuditLogger()
