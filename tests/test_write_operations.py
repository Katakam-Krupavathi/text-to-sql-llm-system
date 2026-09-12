import os
import sqlite3
import time
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from jose import jwt
from unittest.mock import AsyncMock, patch

from app.audit import audit_logger
from app.config import settings
from app.main import app
from app.models import init_auth_db
from app.validator import validate_write_sql, validate_is_write_query


@pytest.fixture(autouse=True)
def setup_clean_env(tmp_path, monkeypatch):
    """Ensures each test gets an isolated auth database and audit database."""
    test_auth_db = str(tmp_path / "test_auth_write.db")
    test_audit_db = str(tmp_path / "test_audit_write.db")
    monkeypatch.setattr(settings, "AUTH_DB_PATH", test_auth_db)
    monkeypatch.setattr(settings, "AUDIT_LOG_DB_PATH", test_audit_db)
    init_auth_db()
    audit_logger.db_path = test_audit_db
    audit_logger._init_db()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def temp_write_db(tmp_path):
    """Creates a temporary SQLite database with a sample products table."""
    db_file = str(tmp_path / "inventory.db")
    conn = sqlite3.connect(db_file)
    with conn:
        conn.execute("CREATE TABLE products (product_id INT PRIMARY KEY, name TEXT, price REAL, stock INT);")
        conn.execute("INSERT INTO products VALUES (1, 'Laptop', 999.99, 10);")
        conn.execute("INSERT INTO products VALUES (2, 'Mouse', 29.99, 50);")
        conn.execute("INSERT INTO products VALUES (3, 'Keyboard', 79.99, 30);")
    conn.close()
    return f"sqlite+aiosqlite:///{db_file}", db_file


def test_validate_write_sql_rules():
    """Confirms validate_write_sql strictly allows INSERT/UPDATE/DELETE with WHERE and blocks prohibited statements."""
    # Valid write statements
    valid_upd = "UPDATE products SET price = 899.99 WHERE product_id = 1;"
    is_v, norm, err = validate_write_sql(valid_upd, target_dialect="sqlite")
    assert is_v is True
    assert "UPDATE" in norm

    valid_del = "DELETE FROM products WHERE stock <= 0;"
    is_v, norm, err = validate_write_sql(valid_del, target_dialect="sqlite")
    assert is_v is True
    assert "DELETE" in norm

    valid_ins = "INSERT INTO products (product_id, name, price, stock) VALUES (4, 'Monitor', 199.99, 15);"
    is_v, norm, err = validate_write_sql(valid_ins, target_dialect="sqlite")
    assert is_v is True
    assert "INSERT" in norm

    # (d) UPDATE / DELETE without WHERE must be rejected outright
    no_where_upd = "UPDATE products SET price = 0.0;"
    is_v, _, err = validate_write_sql(no_where_upd, target_dialect="sqlite")
    assert is_v is False
    assert "WHERE clause" in err

    no_where_del = "DELETE FROM products;"
    is_v, _, err = validate_write_sql(no_where_del, target_dialect="sqlite")
    assert is_v is False
    assert "WHERE clause" in err

    # (f) Prohibited DDL/DCL operations
    for dangerous in [
        "DROP TABLE products;",
        "ALTER TABLE products ADD COLUMN discount REAL;",
        "TRUNCATE TABLE products;",
        "CREATE TABLE hackers (id INT);",
        "GRANT ALL ON products TO public;",
        "SELECT * FROM products;",  # SELECT not permitted in write path
    ]:
        is_v, _, err = validate_write_sql(dangerous, target_dialect="sqlite")
        assert is_v is False, f"Expected {dangerous} to be rejected"

    # Multi-statement injection
    multi = "UPDATE products SET price = 10 WHERE product_id = 1; DROP TABLE products;"
    is_v, _, err = validate_write_sql(multi, target_dialect="sqlite")
    assert is_v is False
    assert "Multiple SQL statements" in err


def test_write_preview_does_not_mutate_data(client, temp_write_db):
    """Test (a): POST /ask/write generates a dry-run preview showing affected rows without modifying data."""
    db_uri, db_file = temp_write_db

    # 1. Register & Login
    reg_resp = client.post("/auth/register", json={"email": "writer@example.com", "password": "Password123!"})
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Add DB Connection with allow_writes=True
    conn_resp = client.post(
        "/connections",
        json={"nickname": "Inventory DB", "dialect": "sqlite", "connection_string": db_uri, "allow_writes": True},
        headers=headers,
    )
    assert conn_resp.status_code == 200
    conn_id = conn_resp.json()["id"]
    assert conn_resp.json()["allow_writes"] is True

    # 3. Mock plan_write to return an UPDATE query
    mock_plan = {
        "reasoning_plan": "Update price for Laptop",
        "sql_dialect": "sqlite",
        "sql_query": "UPDATE products SET price = 850.0 WHERE product_id = 1",
    }
    with patch("app.main.plan_write", new=AsyncMock(return_value=mock_plan)):
        preview_resp = client.post(
            "/ask/write",
            json={"question": "Change Laptop price to 850", "connection_id": conn_id},
            headers=headers,
        )
        assert preview_resp.status_code == 200
        preview_data = preview_resp.json()
        assert "preview_token" in preview_data
        assert preview_data["operation"] == "UPDATE"
        assert len(preview_data["preview_rows"]) == 1
        assert preview_data["preview_rows"][0]["name"] == "Laptop"

    # 4. CRITICAL: Verify underlying DB was NOT mutated during preview
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT price FROM products WHERE product_id = 1")
    current_price = cursor.fetchone()[0]
    conn.close()
    assert current_price == 999.99, "Data must remain unchanged after dry-run preview!"


def test_confirm_write_commits_transaction(client, temp_write_db):
    """Test (b): POST /ask/write/confirm executes the transaction and verifiably modifies data."""
    db_uri, db_file = temp_write_db

    reg_resp = client.post("/auth/register", json={"email": "confirmer@example.com", "password": "Password123!"})
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    conn_resp = client.post(
        "/connections",
        json={"nickname": "Inventory DB", "dialect": "sqlite", "connection_string": db_uri, "allow_writes": True},
        headers=headers,
    )
    conn_id = conn_resp.json()["id"]

    mock_plan = {
        "reasoning_plan": "Update Laptop price to 850",
        "sql_dialect": "sqlite",
        "sql_query": "UPDATE products SET price = 850.0 WHERE product_id = 1",
    }
    with patch("app.main.plan_write", new=AsyncMock(return_value=mock_plan)):
        preview_resp = client.post(
            "/ask/write",
            json={"question": "Discount laptop to 850", "connection_id": conn_id},
            headers=headers,
        )
        preview_token = preview_resp.json()["preview_token"]

    # Confirm write
    confirm_resp = client.post(
        "/ask/write/confirm",
        json={"preview_token": preview_token},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    confirm_data = confirm_resp.json()
    assert confirm_data["status"] == "committed"
    assert confirm_data["affected_rows"] == 1

    # Verify data is now modified in the DB
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT price FROM products WHERE product_id = 1")
    new_price = cursor.fetchone()[0]
    conn.close()
    assert new_price == 850.0

    # Verify audit log recorded the write execution
    recent_logs = audit_logger.get_recent_logs(limit=5)
    write_logs = [log for log in recent_logs if log.get("is_write") == 1]
    assert len(write_logs) >= 1
    assert "UPDATE products SET price = 850" in write_logs[0]["sql_query"]


def test_expired_and_tampered_preview_token_rejected(client, temp_write_db):
    """Test (c): Expired or tampered preview tokens are rejected with appropriate error."""
    db_uri, _ = temp_write_db

    reg_resp = client.post("/auth/register", json={"email": "tester@example.com", "password": "Password123!"})
    token = reg_resp.json()["access_token"]
    user_id = reg_resp.json()["user_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Manually craft an expired token
    expired_payload = {
        "sub": user_id,
        "connection_id": None,
        "sql": "UPDATE products SET price = 50 WHERE product_id = 1",
        "operation": "UPDATE",
        "type": "write_preview",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=10),
    }
    expired_token = jwt.encode(expired_payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    expired_resp = client.post("/ask/write/confirm", json={"preview_token": expired_token}, headers=headers)
    assert expired_resp.status_code == 400
    assert "expired" in expired_resp.json()["detail"].lower()

    # 2. Token from another user (mismatched subject)
    other_user_payload = {
        "sub": "different-user-uuid-999",
        "connection_id": None,
        "sql": "UPDATE products SET price = 50 WHERE product_id = 1",
        "operation": "UPDATE",
        "type": "write_preview",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    other_token = jwt.encode(other_user_payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    other_resp = client.post("/ask/write/confirm", json={"preview_token": other_token}, headers=headers)
    assert other_resp.status_code == 403


def test_write_rejected_when_allow_writes_is_false(client, temp_write_db):
    """Test (e): Connections with allow_writes=False reject write requests."""
    db_uri, _ = temp_write_db

    reg_resp = client.post("/auth/register", json={"email": "readonly_user@example.com", "password": "Password123!"})
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Add connection with allow_writes=False (default)
    conn_resp = client.post(
        "/connections",
        json={"nickname": "Read Only DB", "dialect": "sqlite", "connection_string": db_uri, "allow_writes": False},
        headers=headers,
    )
    conn_id = conn_resp.json()["id"]

    write_resp = client.post(
        "/ask/write",
        json={"question": "Delete product 1", "connection_id": conn_id},
        headers=headers,
    )
    assert write_resp.status_code == 403
    assert "Write operations are disabled" in write_resp.json()["detail"]
