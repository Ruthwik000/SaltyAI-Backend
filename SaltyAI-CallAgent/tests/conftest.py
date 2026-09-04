"""
Pytest configuration, shared fixtures, and test utilities for SALTY AI Call Agent.
"""

import io
import wave
import struct
import pytest


@pytest.fixture
def sample_pcm_audio():
    """Generate 1 second of synthetic 400Hz sine wave 16-bit 8000Hz mono PCM audio."""
    sample_rate = 8000
    duration_sec = 1.0
    freq = 400.0
    num_samples = int(sample_rate * duration_sec)

    import math
    pcm_bytes = bytearray()
    for i in range(num_samples):
        val = int(10000 * math.sin(2 * math.pi * freq * i / sample_rate))
        pcm_bytes.extend(struct.pack("<h", val))
    return bytes(pcm_bytes)


@pytest.fixture
def sample_silence_pcm():
    """Generate 1 second of pure silence (zero PCM samples)."""
    return b"\x00" * 16000  # 8000 samples * 2 bytes


@pytest.fixture
def sample_wav_audio(sample_pcm_audio):
    """Generate WAV container wrapping sample PCM audio."""
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(sample_pcm_audio)
    return wav_io.getvalue()
