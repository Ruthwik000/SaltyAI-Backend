"""
Tests for Pydantic data schemas and contract models.
"""

from app.models.schemas import (
    AIQueryRequest,
    AIQueryResponse,
    EmergencyEventRequest,
    EmergencyEventResponse,
    ExotelStartEvent,
    ExotelMediaEvent,
    ExotelClearEvent,
    ExotelMarkEvent,
    ExotelStopEvent,
    Location,
)


def test_ai_query_request_serialization():
    """Verify AI backend request model serialization with multi-turn history."""
    req = AIQueryRequest(
        call_id="call-test-123",
        phone_number="+919876543210",
        language="ta-IN",
        message="Can I go fishing tomorrow?",
        conversation_history=[
            {"role": "user", "content": "How is the sea?"},
            {"role": "assistant", "content": "Sea is moderate."},
        ],
        location=Location(latitude=13.0827, longitude=80.2707, name="Chennai Coast"),
    )
    payload = req.model_dump(mode="json")
    assert payload["call_id"] == "call-test-123"
    assert payload["phone_number"] == "+919876543210"
    assert payload["language"] == "ta-IN"
    assert len(payload["conversation_history"]) == 2
    assert payload["location"]["name"] == "Chennai Coast"


def test_ai_query_response_validation():
    """Verify AI backend response parsing and validation."""
    data = {
        "response": "Tomorrow sea is safe in morning, rough after 2 PM.",
        "language": "en-IN",
        "priority": "normal",
    }
    resp = AIQueryResponse.model_validate(data)
    assert resp.response.startswith("Tomorrow sea is safe")
    assert resp.language == "en-IN"
    assert resp.priority == "normal"


def test_emergency_event_request():
    """Verify emergency event schema serialization."""
    req = EmergencyEventRequest(
        call_id="call-sos-999",
        phone_number="+919999988888",
        language="ta-IN",
        transcript="படகு மூழ்குது காப்பாத்துங்க",
        location=None,
    )
    payload = req.model_dump(mode="json")
    assert payload["call_id"] == "call-sos-999"
    assert payload["transcript"] == "படகு மூழ்குது காப்பாத்துங்க"
    assert payload["location"] is None


def test_exotel_events_schema():
    """Verify parsing of Exotel start, media, mark, clear, and stop events."""
    start_json = {
        "event": "start",
        "sequence_number": "1",
        "stream_sid": "stream_abc123",
        "start": {
            "account_sid": "acc_001",
            "call_sid": "call_001",
            "from": "+919876543210",
            "to": "08012345678",
            "custom_parameters": {"language": "ta-IN"}
        }
    }
    start_event = ExotelStartEvent.model_validate(start_json)
    assert start_event.stream_sid == "stream_abc123"
    assert start_event.start.call_sid == "call_001"

    media_json = {
        "event": "media",
        "sequence_number": "2",
        "stream_sid": "stream_abc123",
        "media": {
            "chunk": 1,
            "timestamp": "100",
            "payload": "AAAA"
        }
    }
    media_event = ExotelMediaEvent.model_validate(media_json)
    assert media_event.media.payload == "AAAA"

    clear_json = {"event": "clear", "stream_sid": "stream_abc123"}
    clear_event = ExotelClearEvent.model_validate(clear_json)
    assert clear_event.event == "clear"
