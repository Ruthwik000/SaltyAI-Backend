"""
Groq Intelligence Client alias for backward compatibility.
Points directly to the production GroqClient implementation in app.ai.groq_client.
"""

from app.ai.groq_client import (
    GroqClient,
    GroqClient as GroqTestClient,
    groq_client,
    groq_client as groq_test_client,
    detect_text_language,
    sanitize_speech_output,
    SALTY_AI_SYSTEM_INSTRUCTION,
    VOICE_FALLBACK_RESPONSES,
)

__all__ = [
    "GroqClient",
    "GroqTestClient",
    "groq_client",
    "groq_test_client",
    "detect_text_language",
    "sanitize_speech_output",
    "SALTY_AI_SYSTEM_INSTRUCTION",
    "VOICE_FALLBACK_RESPONSES",
]
