"""
Models package for SALTY AI Call Agent.
"""

from app.models.schemas import (
    Location,
    ConversationTurn,
    AIQueryRequest,
    AIQueryResponse,
    EmergencyEventRequest,
    EmergencyEventResponse,
    ExotelStartEvent,
    ExotelStartMetadata,
    ExotelMediaEvent,
    ExotelMediaPayload,
    ExotelMarkEvent,
    ExotelClearEvent,
    ExotelStopEvent,
    ExotelDTMFEvent,
    STTResponse,
    TTSRequest,
    TTSResponse,
    HealthResponse,
    ComponentHealth,
)

__all__ = [
    "Location",
    "ConversationTurn",
    "AIQueryRequest",
    "AIQueryResponse",
    "EmergencyEventRequest",
    "EmergencyEventResponse",
    "ExotelStartEvent",
    "ExotelStartMetadata",
    "ExotelMediaEvent",
    "ExotelMediaPayload",
    "ExotelMarkEvent",
    "ExotelClearEvent",
    "ExotelStopEvent",
    "ExotelDTMFEvent",
    "STTResponse",
    "TTSRequest",
    "TTSResponse",
    "HealthResponse",
    "ComponentHealth",
]
