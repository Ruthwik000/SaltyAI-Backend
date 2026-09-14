"""
Tests for Voice Activity Detection and audio processing utilities.
"""

import struct
from app.voice.vad import VoiceActivityDetector, VADStatus
from app.speech.audio_utils import (
    pcm_to_wav,
    wav_to_pcm,
    chunk_pcm_audio,
    calculate_rms_energy,
    b64_to_pcm,
    pcm_to_b64,
)


def test_calculate_rms_energy_silence(sample_silence_pcm):
    """Verify RMS energy of silence is 0."""
    rms = calculate_rms_energy(sample_silence_pcm)
    assert rms == 0.0


def test_calculate_rms_energy_sine_wave(sample_pcm_audio):
    """Verify RMS energy of synthetic sine wave is positive and non-zero."""
    rms = calculate_rms_energy(sample_pcm_audio)
    assert rms > 5000.0


def test_pcm_to_wav_and_back(sample_pcm_audio):
    """Verify conversion of raw PCM to WAV and round-trip extraction."""
    wav_bytes = pcm_to_wav(sample_pcm_audio, sample_rate=8000, channels=1, sample_width=2)
    assert len(wav_bytes) > len(sample_pcm_audio)  # WAV header overhead

    pcm_recovered, rate = wav_to_pcm(wav_bytes, target_sample_rate=8000)
    assert rate == 8000
    assert len(pcm_recovered) == len(sample_pcm_audio)
    assert pcm_recovered == sample_pcm_audio


def test_chunk_pcm_audio():
    """Verify audio chunking enforces Exotel's 3.2KB (3200 bytes) minimum and 320-byte alignment."""
    # 6400 bytes splits into two 3200-byte chunks
    data = b"\x01\x02" * 3200  # 6400 bytes
    chunks = chunk_pcm_audio(data, chunk_size_bytes=3200, min_chunk_bytes=3200)
    assert len(chunks) == 2
    for chunk in chunks:
        assert len(chunk) == 3200
        assert len(chunk) % 320 == 0

    # Short audio (1000 bytes) is zero-padded up to minimum 3200 bytes
    short_data = b"\x05\x06" * 500  # 1000 bytes
    padded_chunks = chunk_pcm_audio(short_data, chunk_size_bytes=3200, min_chunk_bytes=3200)
    assert len(padded_chunks) == 1
    assert len(padded_chunks[0]) == 3200
    assert padded_chunks[0][:1000] == short_data
    assert padded_chunks[0][1000:] == b"\x00" * 2200



def test_b64_pcm_roundtrip(sample_pcm_audio):
    """Verify Base64 encoding and decoding round-trip."""
    b64 = pcm_to_b64(sample_pcm_audio)
    decoded = b64_to_pcm(b64)
    assert decoded == sample_pcm_audio


def test_vad_speech_start_and_endpointing():
    """Verify VAD state transitions from Silence -> Speech -> Speech Ended."""
    vad = VoiceActivityDetector(
        rms_threshold=500,
        silence_ms=200,      # 200ms silence to endpoint
        min_speech_ms=60,    # 60ms speech to start
        sample_rate=8000,
        sample_width=2,
    )

    # 320 bytes = 20ms at 8kHz 16-bit mono
    speech_frame = struct.pack("<160h", *([3000] * 160))  # High energy frame
    silence_frame = b"\x00" * 320                          # Zero energy frame

    # 1. Feed silence -> SILENCE
    assert vad.process_chunk(silence_frame) == VADStatus.SILENCE
    assert not vad.is_speech_active

    # 2. Feed speech frames (20ms each)
    vad.process_chunk(speech_frame)  # 20ms
    vad.process_chunk(speech_frame)  # 40ms
    status = vad.process_chunk(speech_frame)  # 60ms -> min_speech_ms reached!
    assert status == VADStatus.SPEECH_STARTED
    assert vad.is_speech_active

    # 3. Feed ongoing speech
    assert vad.process_chunk(speech_frame) == VADStatus.SPEECH_ONGOING

    # 4. Feed silence frames until silence_ms (200ms = 10 frames of 20ms)
    for _ in range(9):
        status = vad.process_chunk(silence_frame)
        assert status == VADStatus.SPEECH_ONGOING

    # 10th silence frame (200ms total silence reached) -> SPEECH_ENDED
    status = vad.process_chunk(silence_frame)
    assert status == VADStatus.SPEECH_ENDED
