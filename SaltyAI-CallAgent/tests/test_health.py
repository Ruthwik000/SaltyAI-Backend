"""
Tests for health and readiness endpoints using TestClient.
"""

from starlette.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_root_endpoint():
    """Verify root endpoint returns valid metadata and documentation links."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "SALTY AI Call Agent"
    assert "/ws/exotel/stream" in data["websocket_endpoints"]
    assert data["telephony_provider"] == "Exotel"


def test_health_live_endpoint():
    """Verify liveness probe returns HTTP 200."""
    response = client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "live"


def test_health_ready_endpoint():
    """Verify readiness probe returns HTTP 200."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"


def test_comprehensive_health_endpoint():
    """Verify comprehensive /health endpoint returns structured component statuses."""
    response = client.get("/health")
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert "components" in data
    assert "sarvam_api" in data["components"]
    assert "exotel" in data["components"]
    assert "ai_backend" in data["components"]
