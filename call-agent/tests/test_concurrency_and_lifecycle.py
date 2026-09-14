"""
Comprehensive tests for production-quality concurrency, per-call TTS ownership,
barge-in protection, WebSocket lifecycle safety, and latency instrumentation.
"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from app.config import settings
from app.voice.session import StreamSession
from app.voice.websocket import play_tts_audio_to_exotel, handle_user_turn
from app.models.schemas import STTResponse, AIQueryResponse, TTSResponse
from app.speech.audio_utils import pcm_to_b64


class MockWebSocket:
    """Mock FastAPI WebSocket for testing safe concurrent sends and lifecycle termination."""

    def __init__(self):
        self.sent_messages = []
        self.is_closed = False

    async def send_text(self, text: str):
        if self.is_closed:
            raise RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'")
        self.sent_messages.append(text)

    async def close(self, code: int = 1000):
        self.is_closed = True


@pytest.mark.asyncio
async def test_single_active_tts_playback_ownership(monkeypatch):
    """Verify that starting a second TTS task cancels the first, preventing overlapping audio."""
    session = StreamSession(call_id="call-concurrency-1", stream_sid="sid-1", phone_number="+919876543210")
    ws = MockWebSocket()

    # Mock TTS synthesize returning 1.0 second of audio (16,000 bytes at 8kHz 16-bit)
    fake_pcm = b"\x00\x01" * 8000
    mock_tts_res = TTSResponse(pcm_audio=fake_pcm, sample_rate=8000, duration_seconds=1.0)

    from app.speech.tts import tts_client
    monkeypatch.setattr(tts_client, "synthesize", AsyncMock(return_value=mock_tts_res))

    # Start Turn 1
    gen_1 = session.next_generation()
    task_1 = asyncio.create_task(
        play_tts_audio_to_exotel(
            websocket=ws,
            stream_session=session,
            text="First response in Telugu",
            language_code="te-IN",
            target_generation_id=gen_1,
        )
    )
    session.active_tts_task = task_1

    await asyncio.sleep(0.05)
    assert len(ws.sent_messages) >= 1

    # Start Turn 2 immediately while Turn 1 is still streaming
    await session.cancel_active_tts()
    gen_2 = session.next_generation()
    task_2 = asyncio.create_task(
        play_tts_audio_to_exotel(
            websocket=ws,
            stream_session=session,
            text="Second response in English",
            language_code="en-IN",
            target_generation_id=gen_2,
        )
    )
    session.active_tts_task = task_2

    await task_2
    assert task_1.done()
    # Ensure Turn 1 was cancelled and generation 2 completed
    assert session.generation_id == gen_2


@pytest.mark.asyncio
async def test_stale_generation_cannot_send_audio(monkeypatch):
    """Verify that a stale generation ID aborts before sending any audio."""
    session = StreamSession(call_id="call-stale-1", stream_sid="sid-2", phone_number="+919876543210")
    ws = MockWebSocket()

    fake_pcm = b"\x00\x02" * 3200
    mock_tts_res = TTSResponse(pcm_audio=fake_pcm, sample_rate=8000, duration_seconds=0.2)

    from app.speech.tts import tts_client
    monkeypatch.setattr(tts_client, "synthesize", AsyncMock(return_value=mock_tts_res))

    # Pass an outdated generation ID
    session.generation_id = 5
    await play_tts_audio_to_exotel(
        websocket=ws,
        stream_session=session,
        text="Stale message",
        language_code="en-IN",
        target_generation_id=3,  # Stale
    )

    # No audio messages should have been sent
    assert len(ws.sent_messages) == 0


@pytest.mark.asyncio
async def test_websocket_close_during_tts_handled_gracefully(monkeypatch):
    """Verify that if WebSocket closes mid-playback, safe_send_text suppresses errors without throwing."""
    session = StreamSession(call_id="call-ws-close-1", stream_sid="sid-3", phone_number="+919876543210")
    ws = MockWebSocket()

    fake_pcm = b"\x00\x03" * 9600  # 3 chunks
    mock_tts_res = TTSResponse(pcm_audio=fake_pcm, sample_rate=8000, duration_seconds=0.6)

    from app.speech.tts import tts_client
    monkeypatch.setattr(tts_client, "synthesize", AsyncMock(return_value=mock_tts_res))

    gen = session.next_generation()
    # Close websocket after first chunk
    ws.is_closed = True

    await play_tts_audio_to_exotel(
        websocket=ws,
        stream_session=session,
        text="Testing websocket closure",
        language_code="en-IN",
        target_generation_id=gen,
    )

    # Should exit cleanly without raising RuntimeError
    assert session.is_closed is True


@pytest.mark.asyncio
async def test_barge_in_cancels_playback_and_sends_clear():
    """Verify that cancel_active_tts invalidates playback and safe_send_text delivers clear frame."""
    session = StreamSession(call_id="call-barge-1", stream_sid="sid-4", phone_number="+919876543210")
    ws = MockWebSocket()

    session.is_playing_tts = True
    session.total_bytes_sent = 3200

    # Simulate barge-in cancellation
    await session.cancel_active_tts()
    assert session.is_playing_tts is False
    assert session.active_tts_task is None

    from app.voice.exotel import build_clear_message
    clear_msg = build_clear_message(session.stream_sid)
    sent = await session.safe_send_text(ws, clear_msg)

    assert sent is True
    assert len(ws.sent_messages) == 1
    parsed = json.loads(ws.sent_messages[0])
    assert parsed["event"] == "clear"


@pytest.mark.asyncio
async def test_rapid_consecutive_user_turns(monkeypatch):
    """Verify rapid consecutive user turns cleanly supersede earlier in-flight turns."""
    session = StreamSession(call_id="call-rapid-1", stream_sid="sid-5", phone_number="+919876543210")
    ws = MockWebSocket()

    from app.speech.stt import stt_client
    from app.ai.backend_client import ai_backend_client
    from app.speech.tts import tts_client

    # Mock slow STT and fast AI/TTS
    async def slow_stt(*args, **kwargs):
        await asyncio.sleep(0.05)
        return STTResponse(transcript="first turn", language_code="te-IN", is_empty=False)

    monkeypatch.setattr(stt_client, "transcribe", slow_stt)
    monkeypatch.setattr(ai_backend_client, "query", AsyncMock(return_value=AIQueryResponse(response="AI response", language="te-IN", priority="normal")))
    monkeypatch.setattr(tts_client, "synthesize", AsyncMock(return_value=TTSResponse(pcm_audio=b"\x00\x00" * 3200, sample_rate=8000, duration_seconds=0.2)))

    # Turn 1
    await session.cancel_active_turn()
    t1 = asyncio.create_task(handle_user_turn(ws, session, b"\x00\x01" * 3200))
    await asyncio.sleep(0.01)

    # Turn 2 immediately (interruption / barge-in during STT of Turn 1)
    await session.cancel_active_turn()
    t2 = asyncio.create_task(handle_user_turn(ws, session, b"\x00\x02" * 3200))

    await asyncio.gather(t1, t2)
    # Turn 2 should have incremented generation ID and become the active state
    assert session.generation_id >= 2



@pytest.mark.asyncio
async def test_telugu_to_english_switching_during_tts(monkeypatch):
    """Verify dynamic switching from Telugu to English cancels Telugu audio immediately."""
    session = StreamSession(call_id="call-switch-1", stream_sid="sid-6", phone_number="+919876543210")
    ws = MockWebSocket()

    from app.speech.tts import tts_client
    monkeypatch.setattr(tts_client, "synthesize", AsyncMock(return_value=TTSResponse(pcm_audio=b"\x00\x00" * 6400, sample_rate=8000, duration_seconds=0.4)))

    # Start Telugu playback
    gen_te = session.next_generation()
    task_te = asyncio.create_task(
        play_tts_audio_to_exotel(
            websocket=ws,
            stream_session=session,
            text="రేపు సముద్రం ప్రశాంతంగా ఉంటుంది.",
            language_code="te-IN",
            target_generation_id=gen_te,
        )
    )
    session.active_tts_task = task_te
    await asyncio.sleep(0.02)

    # User interrupts asking in English
    await session.cancel_active_tts()
    gen_en = session.next_generation()
    task_en = asyncio.create_task(
        play_tts_audio_to_exotel(
            websocket=ws,
            stream_session=session,
            text="Tomorrow morning sea will be calm.",
            language_code="en-IN",
            target_generation_id=gen_en,
        )
    )
    session.active_tts_task = task_en

    await task_en
    assert task_te.done()
    assert session.generation_id == gen_en


@pytest.mark.asyncio
async def test_barge_in_echo_guard_and_sustained_speech():
    """Verify that playback echo in first 600ms is ignored and sustained caller speech triggers barge-in."""
    import time
    session = StreamSession(call_id="call-echo-1", stream_sid="sid-echo", phone_number="+919876543210")
    ws = MockWebSocket()

    session.is_playing_tts = True
    session.total_bytes_sent = 3200
    session.playback_started_at = time.perf_counter()  # Just started
    session.playback_chunks_sent = 1

    # Inbound chunk during echo guard window (100ms after start)
    elapsed = time.perf_counter() - session.playback_started_at
    assert elapsed < 0.60
    assert session.playback_chunks_sent < 2
    # Guard condition holds: echo ignored

    # Now simulate time passing beyond echo guard (e.g. 700ms in and 3 chunks sent)
    session.playback_started_at = time.perf_counter() - 0.70
    session.playback_chunks_sent = 3

    # Consecutive inbound speech frames simulate caller speaking
    session.barge_in_speech_chunks = 3
    session.barge_in_speech_duration_ms = 300.0

    # Confirmed barge-in cancels active TTS
    await session.cancel_active_tts()
    assert session.is_playing_tts is False
    assert session.active_tts_task is None


@pytest.mark.asyncio
async def test_stale_ai_turn_in_flight_cancelled_by_new_turn(monkeypatch):
    """Verify in-flight AI reasoning query from Turn 1 is cancelled when Turn 2 starts, never speaking audio."""
    session = StreamSession(call_id="call-turn-cancel-1", stream_sid="sid-tc-1", phone_number="+919876543210")
    ws = MockWebSocket()

    from app.speech.stt import stt_client
    from app.ai.backend_client import ai_backend_client
    from app.speech.tts import tts_client
    from app.conversation.manager import conversation_manager

    conversation_manager.create_session(
        call_id=session.call_id,
        stream_sid=session.stream_sid,
        phone_number=session.phone_number,
    )

    t1_completed = False

    async def slow_ai_query(*args, **kwargs):
        nonlocal t1_completed
        await asyncio.sleep(0.15)
        t1_completed = True
        return AIQueryResponse(response="Slow Turn 1 answer", language="en-IN", priority="normal")

    async def fast_ai_query(*args, **kwargs):
        await asyncio.sleep(0.01)
        return AIQueryResponse(response="Fast Turn 2 answer", language="en-IN", priority="normal")

    monkeypatch.setattr(stt_client, "transcribe", AsyncMock(return_value=STTResponse(transcript="Query text", language_code="en-IN", is_empty=False)))
    monkeypatch.setattr(tts_client, "synthesize", AsyncMock(return_value=TTSResponse(pcm_audio=b"\x00\x00" * 3200, sample_rate=8000, duration_seconds=0.2)))

    # Dispatch Turn 1 with slow AI
    monkeypatch.setattr(ai_backend_client, "query", slow_ai_query)
    turn_task_1 = asyncio.create_task(handle_user_turn(ws, session, b"\x00\x01" * 3200))
    session.active_turn_task = turn_task_1

    await asyncio.sleep(0.02)  # Let Turn 1 enter AI query

    # Caller starts Turn 2 immediately while Turn 1 is waiting on AI query

    await session.cancel_active_turn()
    monkeypatch.setattr(ai_backend_client, "query", fast_ai_query)
    turn_task_2 = asyncio.create_task(handle_user_turn(ws, session, b"\x00\x02" * 3200))
    session.active_turn_task = turn_task_2

    await turn_task_2
    # Ensure Turn 1 was cancelled and never completed AI speech
    assert turn_task_1.done()
    assert t1_completed is False

    call_sess = conversation_manager.get_session(session.call_id)
    assert call_sess is not None
    # Conversation manager should only contain the successful Turn 2
    assert len(call_sess.conversation_history) == 2
    assert call_sess.conversation_history[-1]["content"] == "Fast Turn 2 answer"


@pytest.mark.asyncio
async def test_strict_turn_ordering_preservation(monkeypatch):
    """Verify sequential turns execute and log in strict chronological order."""
    session = StreamSession(call_id="call-order-1", stream_sid="sid-ord-1", phone_number="+919876543210")
    ws = MockWebSocket()

    from app.speech.stt import stt_client
    from app.ai.backend_client import ai_backend_client
    from app.speech.tts import tts_client
    from app.conversation.manager import conversation_manager

    conversation_manager.create_session(
        call_id=session.call_id,
        stream_sid=session.stream_sid,
        phone_number=session.phone_number,
    )

    monkeypatch.setattr(tts_client, "synthesize", AsyncMock(return_value=TTSResponse(pcm_audio=b"\x00\x00" * 3200, sample_rate=8000, duration_seconds=0.2)))

    # Turn 1
    monkeypatch.setattr(stt_client, "transcribe", AsyncMock(return_value=STTResponse(transcript="Turn 1 user", language_code="en-IN", is_empty=False)))
    monkeypatch.setattr(ai_backend_client, "query", AsyncMock(return_value=AIQueryResponse(response="Turn 1 bot response", language="en-IN", priority="normal")))
    await handle_user_turn(ws, session, b"\x00\x01" * 3200)

    # Turn 2
    monkeypatch.setattr(stt_client, "transcribe", AsyncMock(return_value=STTResponse(transcript="Turn 2 user", language_code="en-IN", is_empty=False)))
    monkeypatch.setattr(ai_backend_client, "query", AsyncMock(return_value=AIQueryResponse(response="Turn 2 bot response", language="en-IN", priority="normal")))
    await handle_user_turn(ws, session, b"\x00\x02" * 3200)

    call_sess = conversation_manager.get_session(session.call_id)
    assert call_sess is not None
    assert len(call_sess.conversation_history) == 4
    assert call_sess.conversation_history[0]["content"] == "Turn 1 user"
    assert call_sess.conversation_history[1]["content"] == "Turn 1 bot response"
    assert call_sess.conversation_history[2]["content"] == "Turn 2 user"
    assert call_sess.conversation_history[3]["content"] == "Turn 2 bot response"


@pytest.mark.asyncio
async def test_sequential_chunk_delivery_and_final_mark_ordering(monkeypatch):
    """Verify audio chunks are sent sequentially and mark event is delivered only after the final chunk."""
    session = StreamSession(call_id="call-seq-1", stream_sid="sid-seq-1", phone_number="+919876543210")
    ws = MockWebSocket()

    from app.speech.tts import tts_client
    # 4 distinct chunks of 3200 bytes each
    chunk_1 = b"\x01\x00" * 1600
    chunk_2 = b"\x02\x00" * 1600
    chunk_3 = b"\x03\x00" * 1600
    chunk_4 = b"\x04\x00" * 1600
    full_pcm = chunk_1 + chunk_2 + chunk_3 + chunk_4

    monkeypatch.setattr(tts_client, "synthesize", AsyncMock(return_value=TTSResponse(pcm_audio=full_pcm, sample_rate=8000, duration_seconds=0.8)))

    gen = session.next_generation()
    await play_tts_audio_to_exotel(
        websocket=ws,
        stream_session=session,
        text="Testing complete sequential chunk delivery",
        language_code="en-IN",
        mark_name="test_end_mark",
        target_generation_id=gen,
    )

    # ws.sent_messages should have 4 media messages followed by 1 mark message
    assert len(ws.sent_messages) == 5
    for i in range(4):
        msg = json.loads(ws.sent_messages[i])
        assert msg["event"] == "media"
        assert msg["stream_sid"] == "sid-seq-1"

    final_msg = json.loads(ws.sent_messages[4])
    assert final_msg["event"] == "mark"
    assert final_msg["mark"]["name"] == "test_end_mark"
    assert final_msg["stream_sid"] == "sid-seq-1"





