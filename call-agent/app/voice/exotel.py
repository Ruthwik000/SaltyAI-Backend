"""
Exotel AgentStream protocol helpers, frame builders, and event constants.
Follows the official Exotel Voicebot Applet Bidirectional WebSocket specification.
"""

import json
from typing import Optional, Dict, Any

from app.models.schemas import (
    ExotelMediaEvent,
    ExotelMediaPayload,
    ExotelMarkEvent,
    ExotelMarkPayload,
    ExotelClearEvent,
)


class ExotelEventTypes:
    """Event names defined by Exotel AgentStream protocol."""
    CONNECTED = "connected"
    START = "start"
    MEDIA = "media"
    DTMF = "dtmf"
    MARK = "mark"
    CLEAR = "clear"
    STOP = "stop"


def build_media_message(stream_sid: str, base64_payload: str) -> str:
    """
    Build an outgoing media WebSocket JSON frame to stream audio back to Exotel.
    Audio format: Base64-encoded 16-bit Linear PCM (s16le), 8000Hz mono.
    Includes both stream_sid and streamSid for universal parser compatibility.
    """
    msg = {
        "event": ExotelEventTypes.MEDIA,
        "stream_sid": stream_sid,
        "streamSid": stream_sid,
        "media": {
            "payload": base64_payload
        }
    }
    return json.dumps(msg)


def build_mark_message(stream_sid: str, mark_name: str) -> str:
    """
    Build an outgoing mark WebSocket JSON frame to track audio playback completion.
    """
    msg = {
        "event": ExotelEventTypes.MARK,
        "stream_sid": stream_sid,
        "streamSid": stream_sid,
        "mark": {
            "name": mark_name
        }
    }
    return json.dumps(msg)


def build_clear_message(stream_sid: str) -> str:
    """
    Build an outgoing clear WebSocket JSON frame to flush Exotel's playback buffer.
    Used for instant Barge-In / Interruption when caller begins speaking.
    """
    msg = {
        "event": ExotelEventTypes.CLEAR,
        "stream_sid": stream_sid,
        "streamSid": stream_sid,
    }
    return json.dumps(msg)



def parse_exotel_message(raw_json: str) -> Dict[str, Any]:
    """Parse raw incoming WebSocket frame from Exotel safely."""
    try:
        return json.loads(raw_json)
    except Exception:
        return {}
