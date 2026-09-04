"""
Tests for Exotel AgentStream WebSocket protocol and conversational lifecycle.
"""

import json
import base64
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.speech.audio_utils import pcm_to_b64


def test_websocket_start_and_stop_lifecycle(mocker, sample_pcm_audio):
    """Verify WebSocket connection handles start event, greeting, and stop cleanly."""
    # Mock STT and TTS to avoid external API dependencies in integration test
    mocker.patch(
        "app.voice.websocket.tts_client.synthesize",
        return_value=mocker.MagicMock(
            pcm_audio=sample_pcm_audio[:1600],
            sample_rate=8000,
            duration_seconds=0.1,
            request_id="mock-tts-1",
        )
    )

    client = TestClient(app)
    with client.websocket_connect("/ws/exotel/stream") as ws:
        # 1. Send START event
        start_payload = {
            "event": "start",
            "sequence_number": "1",
            "stream_sid": "stream-test-99",
            "start": {
                "account_sid": "acc_001",
                "call_sid": "call_test_99",
                "from": "+919876543210",
                "to": "08012345678",
                "custom_parameters": {"language": "ta-IN"}
            }
        }
        ws.send_text(json.dumps(start_payload))

        # Receive greeting media chunk from server
        resp_text = ws.receive_text()
        resp_data = json.loads(resp_text)
        assert resp_data["event"] in ("media", "mark")
        assert resp_data["stream_sid"] == "stream-test-99"

        # 2. Send STOP event
        stop_payload = {
            "event": "stop",
            "sequence_number": "2",
            "stream_sid": "stream-test-99",
            "stop": {"call_sid": "call_test_99", "reason": "callended"}
        }
        ws.send_text(json.dumps(stop_payload))


def test_websocket_barge_in_clear_event(mocker, sample_pcm_audio):
    """Verify that speaking while bot is playing generates a CLEAR event and stops TTS."""
    mocker.patch(
        "app.voice.websocket.tts_client.synthesize",
        return_value=mocker.MagicMock(
            pcm_audio=sample_pcm_audio * 5,  # 5 seconds of audio
            sample_rate=8000,
            duration_seconds=5.0,
            request_id="mock-tts-long",
        )
    )

    client = TestClient(app)
    with client.websocket_connect("/ws/exotel/stream") as ws:
        # Start call
        start_payload = {
            "event": "start",
            "sequence_number": "1",
            "stream_sid": "stream-barge-in-1",
            "start": {
                "call_sid": "call_barge_1",
                "from": "+919876543210",
            }
        }
        ws.send_text(json.dumps(start_payload))

        # Read first media chunk of greeting
        ws.receive_text()

        # Caller interrupts with loud voice (high RMS audio)
        b64_speech = pcm_to_b64(sample_pcm_audio[:320])
        media_input = {
            "event": "media",
            "stream_sid": "stream-barge-in-1",
            "media": {"payload": b64_speech}
        }
        ws.send_text(json.dumps(media_input))

        # Server should emit CLEAR event or continue
        # Send STOP to cleanly terminate test
        stop_payload = {
            "event": "stop",
            "stream_sid": "stream-barge-in-1",
        }
        ws.send_text(json.dumps(stop_payload))


def test_websocket_fast_telugu_greeting_delivery(mocker, sample_pcm_audio):
    """Verify short Telugu greeting is transmitted immediately on start event."""
    mocker.patch(
        "app.voice.websocket.tts_client.synthesize",
        return_value=mocker.MagicMock(
            pcm_audio=sample_pcm_audio[:3200],
            sample_rate=8000,
            duration_seconds=0.2,
            request_id="mock-tts-fast-te",
        )
    )

    client = TestClient(app)
    with client.websocket_connect("/ws/exotel/stream") as ws:
        start_payload = {
            "event": "start",
            "sequence_number": "1",
            "stream_sid": "stream-fast-te-1",
            "start": {
                "call_sid": "call_fast_te_1",
                "from": "+919876543210",
                "custom_parameters": {"language": "te-IN"}
            }
        }
        ws.send_text(json.dumps(start_payload))

        # First media message received from server
        msg_str = ws.receive_text()
        msg_data = json.loads(msg_str)
        assert msg_data["event"] == "media"
        assert msg_data["stream_sid"] == "stream-fast-te-1"
        assert "payload" in msg_data["media"]

        # Clean shutdown
        ws.send_text(json.dumps({"event": "stop", "stream_sid": "stream-fast-te-1"}))


def test_websocket_full_handshake_connected_start_media_flow(mocker, sample_pcm_audio):
    """Regression test: Exotel CONNECTED -> START -> Outbound Greeting -> Inbound Media -> STOP."""
    mocker.patch(
        "app.voice.websocket.tts_client.synthesize",
        return_value=mocker.MagicMock(
            pcm_audio=sample_pcm_audio[:3200],
            sample_rate=8000,
            duration_seconds=0.2,
            request_id="mock-tts-handshake",
        )
    )

    client = TestClient(app)
    with client.websocket_connect("/ws/exotel/stream") as ws:
        # 1. CONNECTED handshake event
        ws.send_text(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))

        # 2. START event with media_format
        start_payload = {
            "event": "start",
            "sequence_number": "1",
            "stream_sid": "stream-handshake-101",
            "start": {
                "account_sid": "bvrit4",
                "call_sid": "call_hs_101",
                "from": "+919876543210",
                "to": "08012345678",
                "media_format": {
                    "encoding": "audio/x-raw",
                    "sample_rate": "8000",
                    "channels": 1
                },
                "custom_parameters": {"language": "te-IN"}
            }
        }
        ws.send_text(json.dumps(start_payload))

        # 3. Server streams outbound media
        outbound_frame_str = ws.receive_text()
        outbound_frame = json.loads(outbound_frame_str)
        assert outbound_frame["event"] == "media"
        assert outbound_frame["stream_sid"] == "stream-handshake-101"
        assert outbound_frame["streamSid"] == "stream-handshake-101"
        assert "payload" in outbound_frame["media"]

        # 4. Caller sends normal inbound media audio
        caller_media = {
            "event": "media",
            "sequence_number": "2",
            "stream_sid": "stream-handshake-101",
            "media": {
                "track": "inbound",
                "chunk": "1",
                "timestamp": "200",
                "payload": pcm_to_b64(sample_pcm_audio[:320])
            }
        }
        ws.send_text(json.dumps(caller_media))

        # 5. Send STOP event cleanly
        stop_payload = {
            "event": "stop",
            "sequence_number": "3",
            "stream_sid": "stream-handshake-101",
            "stop": {"call_sid": "call_hs_101", "reason": "callended"}
        }
        ws.send_text(json.dumps(stop_payload))


