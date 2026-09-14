"""
Speech package for SALTY AI Call Agent.
"""

from app.speech.audio_utils import (
    pcm_to_wav,
    wav_to_pcm,
    chunk_pcm_audio,
    calculate_rms_energy,
    b64_to_pcm,
    pcm_to_b64,
)
from app.speech.stt import SarvamSTTClient, stt_client
from app.speech.tts import SarvamTTSClient, tts_client

__all__ = [
    "pcm_to_wav",
    "wav_to_pcm",
    "chunk_pcm_audio",
    "calculate_rms_energy",
    "b64_to_pcm",
    "pcm_to_b64",
    "SarvamSTTClient",
    "stt_client",
    "SarvamTTSClient",
    "tts_client",
]
