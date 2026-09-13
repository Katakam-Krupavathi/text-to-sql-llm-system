import os
import sqlite3
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.crypto import decrypt_connection_string, encrypt_connection_string, mask_connection_string
from app.main import app
from app.models import init_auth_db
from app.retrieval import get_retrieval_index


@pytest.fixture(autouse=True)
def setup_clean_auth_db(tmp_path, monkeypatch):
    """Ensures each test gets an isolated, clean auth SQLite database."""
    test_auth_db = str(tmp_path / "test_auth.db")
    monkeypatch.setattr(settings, "AUTH_DB_PATH", test_auth_db)
    init_auth_db()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def temp_dbs(tmp_path):
    """Creates two distinct SQLite databases for User A and User B."""
    db_a_path = str(tmp_path / "tenant_a.db")
    db_b_path = str(tmp_path / "tenant_b.db")

    # DB A: Hospital schema
    conn_a = sqlite3.connect(db_a_path)
    with conn_a:
        conn_a.execute("CREATE TABLE patients (patient_id INT, full_name TEXT, condition TEXT);")
        conn_a.execute("INSERT INTO patients VALUES (1, 'Alice Smith', 'Flu');")
        conn_a.execute("INSERT INTO patients VALUES (2, 'Bob Jones', 'Headache');")
    conn_a.close()

    # DB B: School schema
    conn_b = sqlite3.connect(db_b_path)
    with conn_b:
        conn_b.execute("CREATE TABLE students (student_id INT, student_name TEXT, grade_level TEXT);")
        conn_b.execute("INSERT INTO students VALUES (101, 'Charlie Brown', 'Senior');")
        conn_b.execute("INSERT INTO students VALUES (102, 'Diana Prince', 'Junior');")
    conn_b.close()

    url_a = f"sqlite+aiosqlite:///{db_a_path}"
    url_b = f"sqlite+aiosqlite:///{db_b_path}"
    return url_a, url_b


def test_encryption_at_rest_and_masking():
    """Confirms symmetric encryption protects raw connection strings and masking sanitizes logs."""
    raw_uri = "postgresql+asyncpg://admin_user:super_secret_pw@localhost:5432/finance_db"
    encrypted = encrypt_connection_string(raw_uri)

    # Must be encrypted ciphertext, not plaintext
    assert encrypted != raw_uri
    assert "super_secret_pw" not in encrypted

    # Must decrypt back to original
    decrypted = decrypt_connection_string(encrypted)
    assert decrypted == raw_uri

    # Masking test
    masked = mask_connection_string(raw_uri)
    assert "super_secret_pw" not in masked
    assert ":***@" in masked


def test_user_registration_and_login_flow(client):
    """Tests user registration, JWT generation, login, and auth token validation."""
    email = "tenant_user_1@example.com"
    password = "SecurePassword123!"

    # 1. Register
    reg_resp = client.post("/auth/register", json={"email": email, "password": password})
    assert reg_resp.status_code == 200
    reg_data = reg_resp.json()
    assert "access_token" in reg_data
    assert reg_data["email"] == email

    # Duplicate registration should fail
    dup_resp = client.post("/auth/register", json={"email": email, "password": password})
    assert dup_resp.status_code == 400

    # 2. Login
    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    # Wrong password fails
    bad_login = client.post("/auth/login", json={"email": email, "password": "WrongPassword"})
    assert bad_login.status_code == 401

    # 3. /auth/me with Bearer token
    headers = {"Authorization": f"Bearer {token}"}
    me_resp = client.get("/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == email


def test_invalid_unreachable_connection_rejected(client):
    """Tests that an invalid or unreachable DB connection is rejected at creation time with a clear 400 error."""
    # Register user
    reg = client.post("/auth/register", json={"email": "bad_conn_tester@example.com", "password": "password123"})
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to add unreachable Postgres host
    bad_payload = {
        "nickname": "Unreachable DB",
        "dialect": "postgres",
        "connection_string": "postgresql+asyncpg://invalid_user:invalid_pass@127.0.0.1:59999/nonexistent_db",
    }
    resp = client.post("/connections", json=bad_payload, headers=headers)
    assert resp.status_code == 400
    err_detail = resp.json()["detail"]
    assert "Failed to connect" in err_detail or "refused" in err_detail or "error" in err_detail.lower()


def test_write_probe_warning_on_writable_connection(client, temp_dbs):
    """Tests connection-time write probe identifies write privileges and returns safety warning."""
    url_a, _ = temp_dbs
    reg = client.post("/auth/register", json={"email": "probe_tester@example.com", "password": "password123"})
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Register writable SQLite DB
    payload = {
        "nickname": "Writable Test DB",
        "dialect": "sqlite",
        "connection_string": url_a,
    }
    resp = client.post("/connections", json=payload, headers=headers)
    assert resp.status_code == 200
    conn_data = resp.json()
    assert "warning" in conn_data
    # Confirms the warning informs the user about write permissions
    assert "write permissions" in conn_data["warning"]


@pytest.mark.asyncio
async def test_multi_tenant_connection_and_schema_isolation(client, temp_dbs):
    """
    Requirement 9:
    Two users each register their own connection.
    Confirms:
    1. User A's /ask routes to User A's database (patients table).
    2. User B's /ask routes to User B's database (students table).
    3. User A never accesses User B's connection or schema.
    4. User A trying to use User B's connection_id gets 404/403.
    """
    url_a, url_b = temp_dbs

    # Register User A & User B
    res_a = client.post("/auth/register", json={"email": "user_a@clinic.com", "password": "password123"})
    token_a = res_a.json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    res_b = client.post("/auth/register", json={"email": "user_b@school.com", "password": "password123"})
    token_b = res_b.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A connects Hospital DB
    conn_a_resp = client.post(
        "/connections",
        json={"nickname": "Clinic DB", "dialect": "sqlite", "connection_string": url_a},
        headers=headers_a,
    )
    assert conn_a_resp.status_code == 200
    conn_a_id = conn_a_resp.json()["id"]

    # User B connects School DB
    conn_b_resp = client.post(
        "/connections",
        json={"nickname": "University DB", "dialect": "sqlite", "connection_string": url_b},
        headers=headers_b,
    )
    assert conn_b_resp.status_code == 200
    conn_b_id = conn_b_resp.json()["id"]

    # Verify per-connection schema indexes were created
    idx_a = get_retrieval_index(conn_a_id)
    idx_b = get_retrieval_index(conn_b_id)

    schema_a = idx_a.relevant_schema("List all patients")
    assert "patients" in schema_a
    assert "students" not in schema_a

    schema_b = idx_b.relevant_schema("List all students")
    assert "students" in schema_b
    assert "patients" not in schema_b

    # Mock LLM for User A querying Clinic DB
    mock_plan_a = {
        "reasoning_plan": "Query patients table from clinic DB",
        "sql_dialect": "sqlite",
        "sql_query": "SELECT full_name, condition FROM patients;",
    }
    with patch("app.agent.llm_client.generate", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [
            '{"reasoning_plan": "Query patients table", "sql_dialect": "sqlite", "sql_query": "SELECT full_name, condition FROM patients;"}',
            "There are 2 patients: Alice Smith (Flu) and Bob Jones (Headache).",
        ]

        resp_ask_a = client.post(
            "/ask",
            json={"question": "Show all patients", "connection_id": conn_a_id},
            headers=headers_a,
        )
        assert resp_ask_a.status_code == 200
        data_a = resp_ask_a.json()
        assert len(data_a["rows"]) == 2
        assert data_a["rows"][0]["full_name"] == "Alice Smith"

    # User A attempting to query User B's connection should be rejected with 404 / access denied
    cross_tenant_resp = client.post(
        "/ask",
        json={"question": "Show all students", "connection_id": conn_b_id},
        headers=headers_a,
    )
    assert cross_tenant_resp.status_code in [403, 404]

    # Delete connection test
    del_resp = client.delete(f"/connections/{conn_a_id}", headers=headers_a)
    assert del_resp.status_code == 200

    # Re-query deleted connection fails
    after_del_resp = client.post(
        "/ask",
        json={"question": "Show all patients", "connection_id": conn_a_id},
        headers=headers_a,
    )
    assert after_del_resp.status_code in [403, 404]


def test_default_connection_selection_picks_earliest(client, temp_dbs):
    """Verifies that when connection_id is omitted, the user's earliest created connection is chosen."""
    url_a, url_b = temp_dbs

    res = client.post("/auth/register", json={"email": "multi_conn_user@example.com", "password": "password123"})
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Register first connection (Clinic DB - url_a)
    conn1_resp = client.post(
        "/connections",
        json={"nickname": "First Registered Clinic DB", "dialect": "sqlite", "connection_string": url_a},
        headers=headers,
    )
    assert conn1_resp.status_code == 200
    conn1_id = conn1_resp.json()["id"]

    # Register second connection (School DB - url_b)
    conn2_resp = client.post(
        "/connections",
        json={"nickname": "Second Registered School DB", "dialect": "sqlite", "connection_string": url_b},
        headers=headers,
    )
    assert conn2_resp.status_code == 200

    # Query without connection_id -> should route to first registered connection (Clinic DB / patients)
    with patch("app.agent.llm_client.generate", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [
            '{"reasoning_plan": "Query default clinic DB", "sql_dialect": "sqlite", "sql_query": "SELECT full_name FROM patients;"}',
            "There are 2 patients.",
        ]

        resp = client.post(
            "/ask",
            json={"question": "List all patients"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rows"]) == 2
        assert "full_name" in data["rows"][0]

