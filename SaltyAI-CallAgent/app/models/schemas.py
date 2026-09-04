"""
Pydantic schemas and data models for SALTY AI Call Agent.
Covers AI Backend contract, Emergency forwarding, Exotel AgentStream, STT, and TTS.
"""

from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime, timezone


# ==============================================================================
# Shared & Conversational Models
# ==============================================================================

class Location(BaseModel):
    """Geographic location representation if known."""
    latitude: Optional[float] = Field(default=None, description="Latitude coordinate")
    longitude: Optional[float] = Field(default=None, description="Longitude coordinate")
    name: Optional[str] = Field(default=None, description="Location name or landmark, e.g. Chennai Coast")
    accuracy: Optional[float] = Field(default=None, description="Accuracy radius in meters")


class ConversationTurn(BaseModel):
    """Single turn in a multi-turn conversation."""
    role: Literal["user", "assistant", "system"] = Field(description="Role of the speaker")
    content: str = Field(description="Utterance text")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of the turn")



# ==============================================================================
# AI Backend Contract Models (POST /api/ai/query)
# ==============================================================================

class AIQueryRequest(BaseModel):
    """
    Request sent from Call Agent to Main SALTY AI Backend.
    Endpoint: POST {AI_BACKEND_URL}/api/ai/query
    """
    call_id: str = Field(description="Unique call identifier")
    phone_number: str = Field(description="Caller phone number (E.164 or local format)")
    language: str = Field(default="te-IN", description="Language code (BCP-47), e.g. te-IN, en-IN, hi-IN")
    message: str = Field(description="Latest spoken query transcribed from fisherman")
    conversation_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Bounded list of prior turns with role and content"
    )
    location: Optional[Location] = Field(default=None, description="Location metadata if known, else null")


class AIQueryResponse(BaseModel):
    """
    Expected response from Main SALTY AI Backend.
    """
    response: str = Field(description="Spoken response message synthesized for the fisherman")
    language: str = Field(default="te-IN", description="Language code of the response text")
    priority: str = Field(default="normal", description="Priority level: normal, urgent, emergency")


# ==============================================================================
# Emergency Forwarding Contract (POST /api/emergency)
# ==============================================================================

class EmergencyEventRequest(BaseModel):
    """
    Payload sent to Main Backend when emergency intent or SOS is detected.
    Endpoint: POST {AI_BACKEND_URL}/api/emergency
    """
    call_id: str = Field(description="Unique call identifier")
    phone_number: str = Field(description="Caller phone number")
    language: str = Field(default="te-IN", description="Language code")
    transcript: str = Field(description="Spoken transcript containing emergency indicators")
    location: Optional[Location] = Field(default=None, description="Location metadata if available, else null")



class EmergencyEventResponse(BaseModel):
    """Acknowledgment response from Main Backend emergency endpoint."""
    status: str = Field(default="acknowledged", description="Emergency processing status")
    rescue_id: Optional[str] = Field(default=None, description="Rescue operation ticket ID if generated")
    message: Optional[str] = Field(default=None, description="Status detail or action dispatched")


# ==============================================================================
# Exotel AgentStream WebSocket Models
# ==============================================================================

class ExotelStartCustomParameters(BaseModel):
    """Custom parameters passed in the Exotel start event."""
    phone_number: Optional[str] = Field(default=None, description="Caller phone number if forwarded in custom params")
    language: Optional[str] = Field(default=None, description="Pre-selected language if set in Exotel flow")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Additional custom flow parameters")


class ExotelStartMetadata(BaseModel):
    """Start event metadata sent by Exotel."""
    account_sid: Optional[str] = Field(default=None, description="Exotel Account SID")
    call_sid: str = Field(description="Exotel unique call SID")
    from_: Optional[str] = Field(default=None, alias="from", description="Caller phone number")
    to: Optional[str] = Field(default=None, description="Exotel virtual number called")
    custom_parameters: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Custom parameters")


class ExotelStartEvent(BaseModel):
    """Exotel AgentStream 'start' event frame."""
    event: Literal["start"] = "start"
    sequence_number: Optional[str] = Field(default=None, description="Event sequence number")
    stream_sid: str = Field(description="Bidirectional stream session SID")
    start: ExotelStartMetadata


class ExotelMediaPayload(BaseModel):
    """Media chunk details."""
    chunk: Optional[int] = Field(default=None, description="Sequential audio chunk number")
    timestamp: Optional[str] = Field(default=None, description="Timestamp or packet offset")
    payload: str = Field(description="Base64-encoded Linear PCM audio samples")


class ExotelMediaEvent(BaseModel):
    """Exotel AgentStream 'media' event frame (incoming or outgoing)."""
    event: Literal["media"] = "media"
    sequence_number: Optional[str] = Field(default=None)
    stream_sid: str = Field(description="Stream session SID")
    media: ExotelMediaPayload


class ExotelMarkPayload(BaseModel):
    """Mark label payload."""
    name: str = Field(description="Name or ID of mark to track playback synchronization")


class ExotelMarkEvent(BaseModel):
    """Exotel AgentStream 'mark' event frame."""
    event: Literal["mark"] = "mark"
    sequence_number: Optional[str] = Field(default=None)
    stream_sid: str = Field(description="Stream session SID")
    mark: ExotelMarkPayload


class ExotelClearEvent(BaseModel):
    """Exotel AgentStream 'clear' event frame to flush audio buffer (barge-in / interrupt)."""
    event: Literal["clear"] = "clear"
    stream_sid: str = Field(description="Stream session SID")


class ExotelDTMFPayload(BaseModel):
    """DTMF tone payload."""
    digit: str = Field(description="Key pressed on phone keypad (0-9, *, #)")


class ExotelDTMFEvent(BaseModel):
    """Exotel AgentStream 'dtmf' event frame."""
    event: Literal["dtmf"] = "dtmf"
    sequence_number: Optional[str] = Field(default=None)
    stream_sid: str = Field(description="Stream session SID")
    dtmf: ExotelDTMFPayload


class ExotelStopMetadata(BaseModel):
    """Stop event metadata."""
    call_sid: Optional[str] = Field(default=None)
    account_sid: Optional[str] = Field(default=None)
    reason: Optional[str] = Field(default="callended", description="Reason for stream termination")


class ExotelStopEvent(BaseModel):
    """Exotel AgentStream 'stop' event frame."""
    event: Literal["stop"] = "stop"
    sequence_number: Optional[str] = Field(default=None)
    stream_sid: str = Field(description="Stream session SID")
    stop: Optional[ExotelStopMetadata] = None


# ==============================================================================
# Speech Services Models (Sarvam STT / TTS)
# ==============================================================================

class STTResponse(BaseModel):
    """Processed Speech-to-Text response."""
    request_id: Optional[str] = Field(default=None, description="Sarvam request ID")
    transcript: str = Field(description="Transcribed text")
    language_code: str = Field(default="ta-IN", description="Detected or specified BCP-47 language code")
    is_empty: bool = Field(default=False, description="True if no speech was detected in the audio")
    confidence: Optional[float] = Field(default=None, description="Transcription confidence if available")


class TTSRequest(BaseModel):
    """Text-to-Speech synthesis request parameters."""
    text: str = Field(description="Text to synthesize")
    language_code: str = Field(default="ta-IN", description="Target language BCP-47 code")
    speaker: Optional[str] = Field(default="shubh", description="Speaker voice ID")
    sample_rate: int = Field(default=8000, description="Target sample rate in Hz")


class TTSResponse(BaseModel):
    """Text-to-Speech synthesis result containing raw PCM audio."""
    request_id: Optional[str] = Field(default=None)
    pcm_audio: bytes = Field(description="Raw 16-bit Linear PCM audio bytes")
    sample_rate: int = Field(default=8000, description="Audio sample rate in Hz")
    duration_seconds: float = Field(default=0.0, description="Audio playback duration in seconds")


# ==============================================================================
# Health Check Models
# ==============================================================================

class ComponentHealth(BaseModel):
    """Health status of an external dependency or internal component."""
    status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
    message: Optional[str] = None
    latency_ms: Optional[float] = None


class HealthResponse(BaseModel):
    """System health check response."""
    status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    version: str = "1.0.0"
    components: Dict[str, ComponentHealth] = Field(default_factory=dict)
