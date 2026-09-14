"""
Voice package for SALTY AI Call Agent.
"""

from app.voice.exotel import (
    ExotelEventTypes,
    build_media_message,
    build_mark_message,
    build_clear_message,
    parse_exotel_message,
)
from app.voice.vad import VADStatus, VoiceActivityDetector
from app.voice.session import StreamSession
from app.voice.websocket import router as voice_router

__all__ = [
    "ExotelEventTypes",
    "build_media_message",
    "build_mark_message",
    "build_clear_message",
    "parse_exotel_message",
    "VADStatus",
    "VoiceActivityDetector",
    "StreamSession",
    "voice_router",
]
