"""
Tests for Groq intelligence client in Development Test Mode.
Verifies multilingual spoken responses, multi-turn context, error handling, and speech sanitization.
"""

import pytest
import respx
import httpx
from unittest.mock import AsyncMock

from app.ai.test_groq_client import (
    GroqTestClient,
    detect_text_language,
    sanitize_speech_output,
    SALTY_AI_SYSTEM_INSTRUCTION,
)
from app.ai.backend_client import AIBackendClient
from app.models.schemas import AIQueryResponse
from app.config import settings


def test_detect_text_language_unicode():
    """Verify accurate BCP-47 language detection based on Unicode script blocks."""
    # Telugu
    assert detect_text_language("నమస్కారం, రేపు సముద్రం ప్రశాంతంగా ఉంటుంది.") == "te-IN"
    # Hindi
    assert detect_text_language("कल समुद्र में लहरें तेज रहेंगी।") == "hi-IN"
    # Tamil
    assert detect_text_language("நாளை கடல் மிதமாக இருக்கும்.") == "ta-IN"
    # English
    assert detect_text_language("Tomorrow morning sea condition is moderate.") == "en-IN"
    # Telugu with English word
    assert detect_text_language("రేపు morning సముద్ర పరిస్థితి బాగుంటుంది.") == "te-IN"


def test_sanitize_speech_output():
    """Verify markdown symbols, bullet points, headers, prefixes, think tags, and colon quotes are cleaned cleanly."""
    # Test 1: Markdown and think tag removal
    dirty_text = "<think>Analyzing marine query</think>**Warning:** # High waves! * Wear life jackets.\n- Be safe."
    clean = sanitize_speech_output(dirty_text)
    assert "<think>" not in clean
    assert "**" not in clean
    assert "#" not in clean
    assert "*" not in clean
    assert "- " not in clean
    assert clean == "Warning: High waves! Wear life jackets. Be safe."

    # Test 2: Stripping role prefix and outer quotes
    prefix_text = 'SALTY AI: "సముద్రం రేపు ప్రశాంతంగా ఉంటుంది, వేటకు వెళ్లవచ్చు."'
    assert sanitize_speech_output(prefix_text) == "సముద్రం రేపు ప్రశాంతంగా ఉంటుంది, వేటకు వెళ్లవచ్చు."

    # Test 3: Stripping colon quote fragment ': "Okay I can help you...'
    colon_text = ': "Okay I can check the wind conditions for Visakhapatnam."'
    assert sanitize_speech_output(colon_text) == "Okay I can check the wind conditions for Visakhapatnam."

    # Test 4: Assistant and Groq prefix
    groq_text = 'Assistant: "Tomorrow morning is safe for fishing."'
    assert sanitize_speech_output(groq_text) == "Tomorrow morning is safe for fishing."


@pytest.mark.asyncio
async def test_groq_client_success_telugu():
    """Verify Groq API successfully returns Telugu spoken response."""
    client = GroqTestClient(api_key="mock-groq-key", model="openai/gpt-oss-20b", timeout=5.0)
    url = "https://api.groq.com/openai/v1/chat/completions"

    with respx.mock:
        respx.post(url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-123",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "openai/gpt-oss-20b",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "రేపు విశాఖపట్నం తీరంలో వాతావరణం అనుకూలంగా ఉంది.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        )

        res = await client.query(
            call_id="call-telugu-1",
            phone_number="+919876543210",
            message="రేపు వాతావరణం ఎలా ఉంది?",
            language="te-IN",
        )

        assert res.language == "te-IN"
        assert "రేపు విశాఖపట్నం తీరంలో వాతావరణం అనుకూలంగా ఉంది." in res.response
        assert res.priority == "normal"


@pytest.mark.asyncio
async def test_groq_client_hindi_response():
    """Verify Groq API returns natural Hindi spoken response."""
    client = GroqTestClient(api_key="mock-groq-key", model="openai/gpt-oss-20b", timeout=5.0)
    url = "https://api.groq.com/openai/v1/chat/completions"

    with respx.mock:
        respx.post(url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "कल समुद्र में हवा की गति सामान्य रहेगी।",
                            }
                        }
                    ]
                },
            )
        )

        res = await client.query(
            call_id="call-hindi-1",
            phone_number="+919876543210",
            message="कल मौसम कैसा रहेगा?",
            language="hi-IN",
        )

        assert res.language == "hi-IN"
        assert "कल समुद्र में हवा की गति सामान्य रहेगी।" in res.response


@pytest.mark.asyncio
async def test_groq_client_english_response():
    """Verify Groq API returns English spoken response."""
    client = GroqTestClient(api_key="mock-groq-key", model="openai/gpt-oss-20b", timeout=5.0)
    url = "https://api.groq.com/openai/v1/chat/completions"

    with respx.mock:
        respx.post(url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Tomorrow sea conditions are calm near Chennai.",
                            }
                        }
                    ]
                },
            )
        )

        res = await client.query(
            call_id="call-eng-1",
            phone_number="+919876543210",
            message="How is the sea tomorrow near Chennai?",
            language="en-IN",
        )

        assert res.language == "en-IN"
        assert "Tomorrow sea conditions are calm near Chennai." in res.response


@pytest.mark.asyncio
async def test_groq_client_timeout_and_fallback():
    """Verify graceful fallback response when Groq API request times out."""
    client = GroqTestClient(api_key="mock-groq-key", model="openai/gpt-oss-20b", timeout=0.1, max_retries=1)
    url = "https://api.groq.com/openai/v1/chat/completions"

    with respx.mock:
        respx.post(url).mock(side_effect=httpx.TimeoutException("Connection timed out"))

        res = await client.query(
            call_id="call-timeout-1",
            phone_number="+919876543210",
            message="Is it safe to sail?",
            language="en-IN",
        )

        assert "Sorry, I'm having trouble processing that right now." in res.response
        assert res.language == "en-IN"


@pytest.mark.asyncio
async def test_groq_client_missing_key_fallback(monkeypatch):
    """Verify fallback response when GROQ_API_KEY is empty."""
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    client = GroqTestClient(api_key="", model="openai/gpt-oss-20b")
    res = await client.query(
        call_id="call-nokey-1",
        phone_number="+919876543210",
        message="రేపు వేటకు వెళ్లవచ్చా?",
        language="te-IN",
    )

    assert "క్షమించండి" in res.response
    assert res.language == "te-IN"



@pytest.mark.asyncio
async def test_groq_client_http_429_quota_limit():
    """Verify HTTP 429 returns immediate localized voice fallback with zero retries."""
    client = GroqTestClient(api_key="mock-groq-key", model="openai/gpt-oss-20b", timeout=5.0)
    url = "https://api.groq.com/openai/v1/chat/completions"

    request_count = 0

    def count_and_reject(request):
        nonlocal request_count
        request_count += 1
        return httpx.Response(429, json={"error": {"code": 429, "message": "Rate limit exceeded"}})

    with respx.mock:
        respx.post(url).mock(side_effect=count_and_reject)

        result = await client.query(
            call_id="call-429-test",
            phone_number="+919876543210",
            message="సముద్రం ఎలా ఉంది?",
            language="te-IN",
        )

    assert request_count == 1
    assert "క్షమించండి" in result.response
    assert result.language == "te-IN"


@pytest.mark.asyncio
async def test_groq_client_client_error_400_and_401():
    """Verify HTTP 400 and 401 client errors return voice fallback with 0 retries."""
    client = GroqTestClient(api_key="invalid-key", model="openai/gpt-oss-20b", timeout=5.0)
    url = "https://api.groq.com/openai/v1/chat/completions"

    with respx.mock:
        respx.post(url).mock(return_value=httpx.Response(401, json={"error": "Unauthorized"}))

        result = await client.query(
            call_id="call-401-test",
            phone_number="+919876543210",
            message="Hello",
            language="en-IN",
        )

    assert "Sorry" in result.response
    assert result.language == "en-IN"


@pytest.mark.asyncio
async def test_groq_client_empty_or_malformed_response():
    """Verify malformed JSON or empty choices return voice fallback."""
    client = GroqTestClient(api_key="mock-groq-key", model="openai/gpt-oss-20b", timeout=5.0)
    url = "https://api.groq.com/openai/v1/chat/completions"

    with respx.mock:
        respx.post(url).mock(return_value=httpx.Response(200, json={"choices": []}))

        result = await client.query(
            call_id="call-empty-test",
            phone_number="+919876543210",
            message="Hello",
            language="en-IN",
        )

    assert "Sorry" in result.response


@pytest.mark.asyncio
async def test_multi_turn_context_retention():
    """Verify multi-turn history is sent formatted as system, user, and assistant messages."""
    client = GroqTestClient(api_key="mock-groq-key", model="openai/gpt-oss-20b", timeout=5.0)
    url = "https://api.groq.com/openai/v1/chat/completions"

    captured_payload = None

    def capture_request(request):
        nonlocal captured_payload
        import json
        captured_payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "రేపు ఉదయం సముద్రం ప్రశాంతంగా ఉంటుంది.",
                        }
                    }
                ]
            },
        )

    with respx.mock:
        respx.post(url).mock(side_effect=capture_request)

        history = [
            {"role": "user", "content": "రేపు విశాఖపట్నంలో వాతావరణం ఎలా ఉంటుంది?"},
            {"role": "assistant", "content": "రేపు విశాఖపట్నంలో గాలి వేగం తక్కువగా ఉంటుంది."},
        ]

        res = await client.query(
            call_id="call-multi-1",
            phone_number="+919876543210",
            message="మరి ఉదయం పూట?",
            language="te-IN",
            conversation_history=history,
        )

        assert captured_payload is not None
        messages = captured_payload["messages"]
        # System prompt + 2 history turns + current turn = 4 messages
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "రేపు విశాఖపట్నంలో వాతావరణం ఎలా ఉంటుంది?"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"] == "రేపు విశాఖపట్నంలో గాలి వేగం తక్కువగా ఉంటుంది."
        assert messages[3]["role"] == "user"
        assert messages[3]["content"] == "మరి ఉదయం పూట?"

        assert res.language == "te-IN"
        assert "రేపు ఉదయం సముద్రం ప్రశాంతంగా ఉంటుంది." in res.response


@pytest.mark.asyncio
async def test_backend_client_routes_to_groq_when_test_mode_enabled(monkeypatch):
    """Verify AIBackendClient delegates to GroqTestClient when CALL_AGENT_TEST_MODE is True."""
    monkeypatch.setattr("app.config.settings.CALL_AGENT_TEST_MODE", True)

    backend = AIBackendClient()
    from app.ai.test_groq_client import groq_test_client

    monkeypatch.setattr(
        groq_test_client,
        "query",
        AsyncMock(return_value=AIQueryResponse(response="Groq test mode response", language="en-IN", priority="normal")),
    )

    result = await backend.query(
        call_id="call-test-route",
        phone_number="+919876543210",
        message="Testing test mode routing",
        language="en-IN",
    )

    assert result.response == "Groq test mode response"
    assert result.language == "en-IN"


@pytest.mark.asyncio
async def test_backend_client_routes_to_main_backend_when_test_mode_disabled(monkeypatch):
    """Verify AIBackendClient calls production AI_BACKEND_URL when CALL_AGENT_TEST_MODE is False."""
    monkeypatch.setattr("app.config.settings.CALL_AGENT_TEST_MODE", False)

    backend = AIBackendClient(base_url="http://mock-backend:8080")

    with respx.mock(base_url="http://mock-backend:8080") as respx_mock:
        respx_mock.post("/api/ai/query").mock(
            return_value=httpx.Response(
                200,
                json={
                    "response": "Production LangGraph response",
                    "language": "en-IN",
                    "priority": "normal",
                },
            )
        )

        result = await backend.query(
            call_id="call-prod-route",
            phone_number="+919876543210",
            message="Production query",
            language="en-IN",
        )

        assert result.response == "Production LangGraph response"
        assert result.language == "en-IN"
