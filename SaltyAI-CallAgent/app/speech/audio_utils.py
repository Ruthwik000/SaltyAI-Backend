"""
Audio utility functions for PCM handling, WAV framing, resampling, and Exotel streaming chunks.
"""

import io
import wave
import struct
import math
import base64
import logging
from typing import List, Tuple

try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop
    except ImportError:
        audioop = None

logger = logging.getLogger(__name__)


def pcm_to_wav(
    pcm_data: bytes,
    sample_rate: int = 8000,
    channels: int = 1,
    sample_width: int = 2
) -> bytes:
    """
    Wrap raw 16-bit Linear PCM audio bytes in a standard WAV container.
    Used to prepare in-memory audio files for Sarvam Saaras STT API.
    """
    if not pcm_data:
        return b""
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    return wav_io.getvalue()


def wav_to_pcm(
    wav_data: bytes,
    target_sample_rate: int = 8000
) -> Tuple[bytes, int]:
    """
    Extract raw PCM audio from a WAV byte stream and resample to target_sample_rate if necessary.
    Returns (pcm_bytes, sample_rate).
    """
    if not wav_data:
        return b"", target_sample_rate

    wav_io = io.BytesIO(wav_data)
    try:
        with wave.open(wav_io, "rb") as wav_file:
            n_channels = wav_file.getnchannels()
            sampwidth = wav_file.getsampwidth()
            framerate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())

            # Convert stereo to mono if needed
            if n_channels > 1:
                if audioop is not None:
                    frames = audioop.tomono(frames, sampwidth, 1, 1)
                else:
                    # Pure python stereo to mono downmix
                    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
                    mono_samples = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)]
                    frames = struct.pack(f"<{len(mono_samples)}h", *mono_samples)

            # Resample to target sample rate if needed
            if framerate != target_sample_rate:
                if audioop is not None:
                    frames, _ = audioop.ratecv(frames, sampwidth, 1, framerate, target_sample_rate, None)
                else:
                    # Simple linear interpolation fallback
                    ratio = target_sample_rate / framerate
                    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
                    new_len = int(len(samples) * ratio)
                    resampled = []
                    for i in range(new_len):
                        orig_idx = i / ratio
                        idx0 = int(orig_idx)
                        idx1 = min(idx0 + 1, len(samples) - 1)
                        frac = orig_idx - idx0
                        val = int(samples[idx0] * (1.0 - frac) + samples[idx1] * frac)
                        resampled.append(val)
                    frames = struct.pack(f"<{len(resampled)}h", *resampled)

            return frames, target_sample_rate
    except Exception as e:
        logger.error(f"Failed to parse WAV audio: {e}")
        # If already raw PCM or fallback, return as is
        return wav_data, target_sample_rate


def calculate_rms_energy(pcm_data: bytes, sample_width: int = 2) -> float:
    """
    Calculate the Root Mean Square (RMS) energy of PCM audio samples.
    Used for Voice Activity Detection (VAD) and silence endpointing.
    """
    if not pcm_data or len(pcm_data) < sample_width:
        return 0.0

    if audioop is not None:
        try:
            return float(audioop.rms(pcm_data, sample_width))
        except Exception:
            pass

    # Pure Python RMS calculation fallback
    num_samples = len(pcm_data) // sample_width
    if num_samples == 0:
        return 0.0

    fmt = f"<{num_samples}h" if sample_width == 2 else f"<{num_samples}b"
    try:
        samples = struct.unpack(fmt, pcm_data[: num_samples * sample_width])
        sum_squares = sum(s * s for s in samples)
        return math.sqrt(sum_squares / num_samples)
    except Exception as e:
        logger.debug(f"RMS calculation error: {e}")
        return 0.0


def chunk_pcm_audio(
    pcm_data: bytes,
    chunk_size_bytes: int = 3200,
    min_chunk_bytes: int = 3200,
) -> List[bytes]:
    """
    Split continuous PCM audio into fixed-size chunks for Exotel AgentStream.
    Exotel specifies a minimum chunk size of 3.2KB (3200 bytes) and requires
    chunks to be multiples of 320 bytes.
    """
    if not pcm_data:
        return []

    # Ensure chunk size is at least min_chunk_bytes and a multiple of 320 bytes
    min_units = max(1, min_chunk_bytes // 320)
    req_units = max(min_units, chunk_size_bytes // 320)
    chunk_size = req_units * 320
    min_size = min_units * 320

    chunks = []
    total_len = len(pcm_data)

    for i in range(0, total_len, chunk_size):
        chunk = pcm_data[i : i + chunk_size]
        # If chunk is smaller than minimum required (3.2KB), pad with silence
        if len(chunk) < min_size:
            chunk = chunk + (b"\x00" * (min_size - len(chunk)))
        else:
            # Pad to the nearest 320-byte boundary if needed
            remainder = len(chunk) % 320
            if remainder != 0:
                chunk = chunk + (b"\x00" * (320 - remainder))
        chunks.append(chunk)

    return chunks




def b64_to_pcm(b64_payload: str) -> bytes:
    """Decode base64 encoded audio string into raw bytes."""
    try:
        return base64.b64decode(b64_payload)
    except Exception as e:
        logger.error(f"Error decoding base64 audio payload: {e}")
        return b""


def pcm_to_b64(pcm_data: bytes) -> str:
    """Encode raw audio bytes into base64 string."""
    return base64.b64encode(pcm_data).decode("ascii")
