"""
Tests for Development Test Mode, Telugu/English multi-turn conversation, and context retention.
"""

import pytest
import respx
import httpx
from app.config import settings
from app.ai.backend_client import AIBackendClient
from app.conversation.test_handler import generate_development_response
from app.api.emergency import EmergencyDetector


def test_development_response_greetings():
    """Verify greeting responses in both Telugu and English."""
    # Telugu greeting
    resp_te, lang_te = generate_development_response("నమస్కారం", [], "te-IN")
    assert "సాల్టీ ఏఐ" in resp_te
    assert lang_te == "te-IN"

    # English greeting
    resp_en, lang_en = generate_development_response("Hello", [], "en-IN")
    assert "SALTY AI" in resp_en
    assert lang_en == "en-IN"


def test_multi_turn_context_retention_flow():
    """
    Verify complete multi-turn conversation preserving context:
    Turn 1: Hello
    Turn 2: I want to go fishing tomorrow
    Turn 3: Visakhapatnam
    Turn 4: రేపు morning sea condition ఎలా ఉంటుంది?
    Turn 5: Okay, what about evening?
    """
    history = []

    # Turn 1: Caller says "Hello"
    t1_msg = "Hello"
    r1, l1 = generate_development_response(t1_msg, history, "en-IN")
    assert "SALTY AI" in r1
    history.append({"role": "user", "content": t1_msg})
    history.append({"role": "assistant", "content": r1})

    # Turn 2: Caller says "I want to go fishing tomorrow"
    t2_msg = "I want to go fishing tomorrow."
    r2, l2 = generate_development_response(t2_msg, history, "en-IN")
    assert "area" in r2.lower() or "port" in r2.lower()
    history.append({"role": "user", "content": t2_msg})
    history.append({"role": "assistant", "content": r2})

    # Turn 3: Caller says "Visakhapatnam"
    t3_msg = "Visakhapatnam."
    r3, l3 = generate_development_response(t3_msg, history, "en-IN")
    assert "Visakhapatnam" in r3
    history.append({"role": "user", "content": t3_msg})
    history.append({"role": "assistant", "content": r3})

    # Turn 4: Caller switches to Telugu: "రేపు morning sea condition ఎలా ఉంటుంది?"
    t4_msg = "రేపు morning sea condition ఎలా ఉంటుంది?"
    r4, l4 = generate_development_response(t4_msg, history, "te-IN")
    assert l4 == "te-IN"
    # Verify entity understanding and honest notice that live intelligence comes from backend
    assert "సాల్టీ ఏఐ" in r4 or "సముద్ర" in r4
    history.append({"role": "user", "content": t4_msg})
    history.append({"role": "assistant", "content": r4})

    # Turn 5: Caller asks follow-up: "Okay, what about evening?"
    t5_msg = "Okay, what about evening?"
    r5, l5 = generate_development_response(t5_msg, history, "en-IN")
    assert l5 == "en-IN"
    # Confirms it retained "evening" and context from previous turns
    assert "evening" in r5.lower() or "Visakhapatnam" in r5
    assert "backend" in r5.lower() or "live" in r5.lower()


def test_code_switching_telugu_english():
    """Verify seamless code-switching within same turn or across turns."""
    # Code-mixed query with Telugu script
    resp, lang = generate_development_response(
        "Tomorrow morning sea condition ఎలా ఉంటుంది?",
        [{"role": "user", "content": "Visakhapatnam"}],
        "te-IN"
    )
    assert lang == "te-IN"
    assert "సముద్ర" in resp

    # English query after Telugu
    resp_en, lang_en = generate_development_response(
        "Okay, thank you. What about evening?",
        [{"role": "user", "content": "రేపు విశాఖపట్నం సముద్రం"}],
        "en-IN"
    )
    assert lang_en == "en-IN"
    assert "evening" in resp_en.lower()


def test_no_hallucinated_marine_data():
    """Verify system does not fabricate weather or cyclone forecasts during test mode."""
    resp_te, _ = generate_development_response("రేపు తుఫాను ఉందా?", [], "te-IN")
    # Response must clarify live backend connection requirement
    assert "సాల్టీ ఏఐ" in resp_te or "బ్యాకెండ్" in resp_te or "సమాచారం" in resp_te

    resp_en, _ = generate_development_response("Is the sea rough tomorrow?", [], "en-IN")
    assert "backend" in resp_en.lower() or "SALTY AI" in resp_en


def test_emergency_detection_telugu_and_english():
    """Verify emergency phrases in Telugu and English."""
    detector = EmergencyDetector()

    # Telugu emergency phrases
    assert detector.detect("నా బోట్ ఇంజిన్ ఆగిపోయింది.")[0] is True
    assert detector.detect("నాకు సహాయం కావాలి.")[0] is True
    assert detector.detect("పడవ మునిగిపోతుంది కాపాడండి")[0] is True

    # English emergency phrases
    assert detector.detect("I am stuck in the sea.")[0] is True
    assert detector.detect("Help, my boat is sinking.")[0] is True
    assert detector.detect("Engine failed and drifting")[0] is True


@pytest.mark.asyncio
async def test_backend_client_in_test_mode(monkeypatch):
    """Verify AIBackendClient invokes Groq test client when CALL_AGENT_TEST_MODE is True."""
    monkeypatch.setattr(settings, "CALL_AGENT_TEST_MODE", True)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "mock-groq-key")

    with respx.mock:
        respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "Sure, which port are you heading out from?"}}
                    ]
                }
            )
        )

        client = AIBackendClient()
        result = await client.query(
            call_id="call-test-mode-1",
            phone_number="+919876543210",
            message="Hello, I want to go fishing tomorrow.",
            language="en-IN",
            conversation_history=[],
        )

        assert result.priority == "normal"
        assert "Sure" in result.response
        assert result.language == "en-IN"





@pytest.mark.asyncio
async def test_backend_client_in_production_mode(monkeypatch):
    """Verify AIBackendClient queries external HTTP endpoint when CALL_AGENT_TEST_MODE is False."""
    monkeypatch.setattr(settings, "CALL_AGENT_TEST_MODE", False)
    client = AIBackendClient(base_url="http://mock-prod-backend:8080")

    with respx.mock(base_url="http://mock-prod-backend:8080") as respx_mock:
        respx_mock.post("/api/ai/query").mock(
            return_value=httpx.Response(
                200,
                json={
                    "response": "రేపు విశాఖపట్నం తీరంలో వాతావరణం అనుకూలంగా ఉంది.",
                    "language": "te-IN",
                    "priority": "normal",
                }
            )
        )

        result = await client.query(
            call_id="call-prod-1",
            phone_number="+919876543210",
            message="Visakhapatnam weather",
            language="te-IN",
            conversation_history=[],
        )

        assert "విశాఖపట్నం" in result.response
        assert result.language == "te-IN"
