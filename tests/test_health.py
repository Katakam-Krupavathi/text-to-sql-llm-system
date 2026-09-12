import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from app.main import app

client = TestClient(app)


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert data["sql_dialect"] == "postgres"


@pytest.mark.asyncio
async def test_health_endpoint():
    # Test health check endpoint with mocked DB connection check
    with patch("app.main.check_db_connection", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = {"connected": True, "ping": True}
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["dialect"] == "postgres"
        assert data["database"]["connected"] is True
