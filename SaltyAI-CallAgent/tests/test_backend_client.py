"""
Tests for AI Backend Client connector.
"""

import pytest
import respx
import httpx
from app.config import settings
from app.ai.backend_client import AIBackendClient
from app.models.schemas import Location


@pytest.mark.asyncio
async def test_backend_client_query_success(monkeypatch):
    """Verify successful query to Main SALTY AI Backend."""
    monkeypatch.setattr(settings, "CALL_AGENT_TEST_MODE", False)
    client = AIBackendClient(base_url="http://mock-ai-backend:8080", max_retries=1)

    with respx.mock(base_url="http://mock-ai-backend:8080") as respx_mock:
        respx_mock.post("/api/ai/query").mock(
            return_value=httpx.Response(
                200,
                json={
                    "response": "రేపు ఉదయం సముద్రం ప్రశాంతంగా ఉంటుంది.",
                    "language": "te-IN",
                    "priority": "normal",
                }
            )
        )

        result = await client.query(
            call_id="call-123",
            phone_number="+919876543210",
            message="Tomorrow sea condition epdi?",
            language="te-IN",
            conversation_history=[],
            location=Location(latitude=17.68, longitude=83.21, name="Visakhapatnam"),
        )

        assert "రేపు ఉదయం" in result.response
        assert result.language == "te-IN"
        assert result.priority == "normal"


@pytest.mark.asyncio
async def test_backend_client_retry_and_fallback(monkeypatch):
    """Verify client retries on 503 and returns spoken fallback on ultimate failure."""
    monkeypatch.setattr(settings, "CALL_AGENT_TEST_MODE", False)
    client = AIBackendClient(
        base_url="http://mock-ai-backend:8080",
        max_retries=2,
        retry_delay=0.01,
    )

    with respx.mock(base_url="http://mock-ai-backend:8080") as respx_mock:
        # Mock repeated 503 Service Unavailable
        respx_mock.post("/api/ai/query").mock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )

        result = await client.query(
            call_id="call-failed-1",
            phone_number="+919876543210",
            message="Is it safe?",
            language="en-IN",
        )

        # Should return safe spoken fallback message
        assert "Sorry, I'm having trouble connecting" in result.response
        assert result.language == "en-IN"

