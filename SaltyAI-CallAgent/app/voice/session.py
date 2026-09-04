"""
Stream Session & Audio Buffer state for live Exotel WebSocket connections.
Manages audio buffering, VAD instance, playback cancellation, and barge-in tokens.
"""

import time
import asyncio
import logging
from typing import Optional, Dict

from app.config import settings
from app.voice.vad import VoiceActivityDetector

logger = logging.getLogger(__name__)


class StreamSession:
    """
    Manages active audio buffering, per-call TTS playback task ownership,
    concurrency control, VAD state, and safe WebSocket transmission for an Exotel stream.
    """

    def __init__(
        self,
        call_id: str,
        stream_sid: str,
        phone_number: str,
        language: str = "te-IN",
    ):
        self.call_id = call_id
        self.stream_sid = stream_sid
        self.phone_number = phone_number
        self.language = language

        # Audio stream buffers
        self.audio_buffer: bytearray = bytearray()
        self.vad = VoiceActivityDetector()

        # Concurrency & Generation ID Tracking (Eliminates TTS & AI response overlapping)
        self.generation_id: int = 0
        self.active_turn_task: Optional[asyncio.Task] = None
        self.active_tts_task: Optional[asyncio.Task] = None
        self.is_playing_tts: bool = False
        self.is_processing_turn: bool = False

        # Playback Echo Guard & Genuine Barge-In Tracking
        self.playback_started_at: float = 0.0
        self.playback_chunks_sent: int = 0
        self.barge_in_speech_duration_ms: float = 0.0
        self.barge_in_speech_chunks: int = 0

        # WebSocket Lifecycle & Safe Transmission Guards
        self.is_closed: bool = False
        self.is_connected: bool = True
        self.send_lock: asyncio.Lock = asyncio.Lock()

        # Metrics & Latency Tracking
        self.total_bytes_received: int = 0
        self.total_bytes_sent: int = 0
        self.created_at: float = time.time()
        self.last_audio_received: float = time.time()

    def next_generation(self) -> int:
        """Increment and return the new active generation ID, invalidating previous turns."""
        self.generation_id += 1
        return self.generation_id

    def append_audio(self, pcm_chunk: bytes) -> None:
        """Append incoming PCM audio chunk to current turn buffer."""
        self.audio_buffer.extend(pcm_chunk)
        self.total_bytes_received += len(pcm_chunk)
        self.last_audio_received = time.time()

    def get_audio_and_reset(self) -> bytes:
        """Retrieve collected PCM audio for STT and reset buffer and VAD."""
        audio = bytes(self.audio_buffer)
        self.audio_buffer.clear()
        self.vad.reset()
        return audio

    def clear_buffer(self) -> None:
        """Clear the audio buffer without returning."""
        self.audio_buffer.clear()
        self.vad.reset()

    async def cancel_active_turn(self) -> None:
        """
        Safely cancel any in-flight AI reasoning (Grok / LangGraph), STT, and TTS playback tasks.
        Invalidates the active generation ID so stale AI responses are never spoken.
        """
        self.generation_id += 1

        self.is_playing_tts = False
        self.is_processing_turn = False
        self.playback_started_at = 0.0
        self.playback_chunks_sent = 0
        self.barge_in_speech_duration_ms = 0.0
        self.barge_in_speech_chunks = 0

        # 1. Cancel in-flight AI turn task
        if self.active_turn_task and not self.active_turn_task.done():
            turn_task = self.active_turn_task
            self.active_turn_task = None
            turn_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(turn_task), timeout=0.15)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        self.active_turn_task = None

        # 2. Cancel in-flight TTS playback task
        if self.active_tts_task and not self.active_tts_task.done():
            tts_task = self.active_tts_task
            self.active_tts_task = None
            tts_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(tts_task), timeout=0.15)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        self.active_tts_task = None

    async def cancel_active_tts(self) -> None:
        """
        Safely cancel the currently active TTS playback task and invalidate the generation.
        Guarantees that no stale task continues sending audio chunks over the WebSocket.
        """
        # Invalidate current generation ID so any active send loop exits immediately
        self.generation_id += 1
        self.is_playing_tts = False
        self.playback_started_at = 0.0
        self.playback_chunks_sent = 0
        self.barge_in_speech_duration_ms = 0.0
        self.barge_in_speech_chunks = 0

        if self.active_tts_task and not self.active_tts_task.done():
            task = self.active_tts_task
            self.active_tts_task = None
            task.cancel()
            try:
                # Wait briefly for cancellation to complete
                await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        self.active_tts_task = None

    def cancel_playback(self) -> None:
        """Synchronous cancellation trigger for quick interruption."""
        self.generation_id += 1
        self.is_playing_tts = False
        self.is_processing_turn = False
        self.playback_started_at = 0.0
        self.playback_chunks_sent = 0
        self.barge_in_speech_duration_ms = 0.0
        self.barge_in_speech_chunks = 0
        if self.active_turn_task and not self.active_turn_task.done():
            self.active_turn_task.cancel()
        self.active_turn_task = None
        if self.active_tts_task and not self.active_tts_task.done():
            self.active_tts_task.cancel()
        self.active_tts_task = None



    async def safe_send_text(self, websocket, message: str) -> bool:
        """
        Thread-safe and lifecycle-safe WebSocket send.
        Guards against sending frames after WebSocket close or disconnect.
        """
        if self.is_closed or not self.is_connected:
            return False

        try:
            async with self.send_lock:
                if self.is_closed or not self.is_connected:
                    return False
                await websocket.send_text(message)
                return True
        except (RuntimeError, Exception) as exc:
            logger.debug(f"Safe send aborted (WebSocket inactive): {exc} | stream_sid={self.stream_sid}")
            self.is_connected = False
            self.is_closed = True
            return False

