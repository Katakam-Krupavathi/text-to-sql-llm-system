import asyncio
import datetime
import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional
from app.config import settings


def _get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(settings.AUTH_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(settings.AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db():
    """Initializes the users and database_connections tables."""
    conn = _get_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS database_connections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    dialect TEXT NOT NULL,
                    encrypted_connection_string TEXT NOT NULL,
                    is_read_only BOOLEAN NOT NULL DEFAULT 1,
                    allow_writes BOOLEAN NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_validated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
            """)
            try:
                conn.execute("ALTER TABLE database_connections ADD COLUMN allow_writes BOOLEAN NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
    finally:
        conn.close()


# Ensure tables exist on module import
init_auth_db()


def _sync_create_user(email: str, hashed_password: str) -> dict:
    user_id = str(uuid.uuid4())
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO users (id, email, hashed_password, created_at) VALUES (?, ?, ?, ?)",
                (user_id, email.lower().strip(), hashed_password, created_at),
            )
        return {
            "id": user_id,
            "email": email.lower().strip(),
            "created_at": created_at,
        }
    finally:
        conn.close()


async def create_user(email: str, hashed_password: str) -> dict:
    """Async-safe creation of user record."""
    return await asyncio.to_thread(_sync_create_user, email, hashed_password)


def create_user_sync(email: str, hashed_password: str) -> dict:
    return _sync_create_user(email, hashed_password)


def _sync_get_user_by_email(email: str) -> Optional[dict]:
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


async def get_user_by_email(email: str) -> Optional[dict]:
    """Async-safe retrieval of user by email."""
    return await asyncio.to_thread(_sync_get_user_by_email, email)


def get_user_by_email_sync(email: str) -> Optional[dict]:
    return _sync_get_user_by_email(email)


def _sync_get_user_by_id(user_id: str) -> Optional[dict]:
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


async def get_user_by_id(user_id: str) -> Optional[dict]:
    """Async-safe retrieval of user by ID."""
    return await asyncio.to_thread(_sync_get_user_by_id, user_id)


def get_user_by_id_sync(user_id: str) -> Optional[dict]:
    return _sync_get_user_by_id(user_id)


def _sync_create_database_connection(
    user_id: str,
    nickname: str,
    dialect: str,
    encrypted_connection_string: str,
    is_read_only: bool = True,
    allow_writes: bool = False,
) -> dict:
    conn_id = str(uuid.uuid4())
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO database_connections 
                (id, user_id, nickname, dialect, encrypted_connection_string, is_read_only, allow_writes, created_at, last_validated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conn_id,
                    user_id,
                    nickname.strip(),
                    dialect.strip().lower(),
                    encrypted_connection_string,
                    1 if is_read_only else 0,
                    1 if allow_writes else 0,
                    now_iso,
                    now_iso,
                ),
            )
        return {
            "id": conn_id,
            "user_id": user_id,
            "nickname": nickname.strip(),
            "dialect": dialect.strip().lower(),
            "is_read_only": is_read_only,
            "allow_writes": allow_writes,
            "created_at": now_iso,
            "last_validated_at": now_iso,
        }
    finally:
        conn.close()


async def create_database_connection(
    user_id: str,
    nickname: str,
    dialect: str,
    encrypted_connection_string: str,
    is_read_only: bool = True,
    allow_writes: bool = False,
) -> dict:
    """Async-safe creation of user database connection record."""
    return await asyncio.to_thread(
        _sync_create_database_connection,
        user_id,
        nickname,
        dialect,
        encrypted_connection_string,
        is_read_only,
        allow_writes,
    )


def create_database_connection_sync(
    user_id: str,
    nickname: str,
    dialect: str,
    encrypted_connection_string: str,
    is_read_only: bool = True,
    allow_writes: bool = False,
) -> dict:
    return _sync_create_database_connection(
        user_id,
        nickname,
        dialect,
        encrypted_connection_string,
        is_read_only,
        allow_writes,
    )


def _sync_get_connections_for_user(user_id: str) -> List[dict]:
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, nickname, dialect, is_read_only, allow_writes, created_at, last_validated_at
            FROM database_connections 
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "nickname": r["nickname"],
                "dialect": r["dialect"],
                "is_read_only": bool(r["is_read_only"]),
                "allow_writes": bool(r["allow_writes"]) if "allow_writes" in r.keys() else False,
                "created_at": r["created_at"],
                "last_validated_at": r["last_validated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


async def get_connections_for_user(user_id: str) -> List[dict]:
    """Async-safe retrieval of connections belonging to user."""
    return await asyncio.to_thread(_sync_get_connections_for_user, user_id)


def get_connections_for_user_sync(user_id: str) -> List[dict]:
    return _sync_get_connections_for_user(user_id)


def _sync_get_connection_by_id(connection_id: str) -> Optional[dict]:
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM database_connections WHERE id = ?", (connection_id,))
        row = cursor.fetchone()
        if row:
            d = dict(row)
            d["is_read_only"] = bool(d["is_read_only"])
            d["allow_writes"] = bool(d.get("allow_writes", False))
            return d
        return None
    finally:
        conn.close()


async def get_connection_by_id(connection_id: str) -> Optional[dict]:
    """Async-safe retrieval of connection by ID."""
    return await asyncio.to_thread(_sync_get_connection_by_id, connection_id)


def get_connection_by_id_sync(connection_id: str) -> Optional[dict]:
    return _sync_get_connection_by_id(connection_id)


def _sync_delete_database_connection(connection_id: str, user_id: str) -> bool:
    conn = _get_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM database_connections WHERE id = ? AND user_id = ?",
                (connection_id, user_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


async def delete_database_connection(connection_id: str, user_id: str) -> bool:
    """Async-safe deletion of user database connection."""
    return await asyncio.to_thread(_sync_delete_database_connection, connection_id, user_id)


def delete_database_connection_sync(connection_id: str, user_id: str) -> bool:
    return _sync_delete_database_connection(connection_id, user_id)

