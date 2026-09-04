"""Tests for the Exotel outbound call bridge."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_exotel_status_is_safe_and_reports_configuration(mocker):
    mocker.patch.object(settings, "EXOTEL_API_KEY", "key")
    mocker.patch.object(settings, "EXOTEL_API_TOKEN", "token")
    mocker.patch.object(settings, "EXOTEL_ACCOUNT_SID", "sid")
    mocker.patch.object(settings, "EXOTEL_CALLER_ID", "08000000000")
    mocker.patch.object(settings, "EXOTEL_STREAM_URL", "wss://voice.example/ws/exotel/stream")

    response = TestClient(app).get("/exotel/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "provider": "Exotel",
        "stream_configured": True,
    }
    assert "key" not in response.text and "token" not in response.text


@pytest.mark.anyio
async def test_outbound_call_uses_exotel_connect_api(respx, mocker):
    mocker.patch.object(settings, "EXOTEL_API_KEY", "key")
    mocker.patch.object(settings, "EXOTEL_API_TOKEN", "token")
    mocker.patch.object(settings, "EXOTEL_ACCOUNT_SID", "sid")
    mocker.patch.object(settings, "EXOTEL_SUB_DOMAIN", "api.in.exotel.com")
    mocker.patch.object(settings, "EXOTEL_CALLER_ID", "08000000000")
    mocker.patch.object(settings, "EXOTEL_STREAM_URL", "wss://voice.example/ws/exotel/stream")

    route = respx.post("https://api.in.exotel.com/v1/Accounts/sid/Calls/connect").mock(
        return_value=httpx.Response(200, text="<twilioresponse><call><sid>call-123</sid></call></twilioresponse>")
    )

    response = await app.router.routes[-1].endpoint.__wrapped__ if False else None
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        response = await client.post("/exotel/call", json={"phone_number": "+91 98765 43210"})
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.json()["call_sid"] == "call-123"
    assert route.calls[0].request.content.decode() == (
        "From=%2B919876543210&CallerId=08000000000&StreamUrl="
        "wss%3A%2F%2Fvoice.example%2Fws%2Fexotel%2Fstream&StreamType="
        "bidirectional&CallType=trans&TimeLimit=3600&TimeOut=30"
    )
