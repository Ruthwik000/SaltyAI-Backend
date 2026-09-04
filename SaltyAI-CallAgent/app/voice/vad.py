"""
Voice Activity Detection (VAD) & Silence Endpointing module.
Configurable for noisy coastal / marine boat environments.
"""

import time
import logging
from enum import Enum
from typing import Optional

from app.config import settings
from app.speech.audio_utils import calculate_rms_energy

logger = logging.getLogger(__name__)


class VADStatus(Enum):
    SILENCE = "silence"
    SPEECH_STARTED = "speech_started"
    SPEECH_ONGOING = "speech_ongoing"
    SPEECH_ENDED = "speech_ended"


class VoiceActivityDetector:
    """
    Energy-based Voice Activity Detector and endpointing state machine.
    Uses configurable RMS threshold, speech start window, and silence timeout.
    """

    def __init__(
        self,
        rms_threshold: Optional[int] = None,
        silence_ms: Optional[int] = None,
        min_speech_ms: Optional[int] = None,
        sample_rate: Optional[int] = None,
        sample_width: Optional[int] = None,
    ):
        self.rms_threshold = rms_threshold if rms_threshold is not None else settings.VAD_RMS_THRESHOLD
        self.silence_ms = silence_ms if silence_ms is not None else settings.VAD_SILENCE_MS
        self.min_speech_ms = min_speech_ms if min_speech_ms is not None else settings.VAD_MIN_SPEECH_MS
        self.sample_rate = sample_rate if sample_rate is not None else settings.AUDIO_SAMPLE_RATE
        self.sample_width = sample_width if sample_width is not None else settings.AUDIO_SAMPLE_WIDTH

        # Internal state tracking
        self._speech_started: bool = False
        self._speech_duration_ms: float = 0.0
        self._silence_duration_ms: float = 0.0
        self._total_duration_ms: float = 0.0

    def reset(self) -> None:
        """Reset the detector state for a new turn."""
        self._speech_started = False
        self._speech_duration_ms = 0.0
        self._silence_duration_ms = 0.0
        self._total_duration_ms = 0.0

    def process_chunk(self, pcm_chunk: bytes) -> VADStatus:
        """
        Evaluate an incoming audio PCM chunk.

        Args:
            pcm_chunk: Raw PCM bytes (e.g., 320 bytes = 20ms at 8kHz).

        Returns:
            VADStatus indicating the conversational speech state.
        """
        if not pcm_chunk:
            return VADStatus.SILENCE

        # Calculate chunk duration in milliseconds
        num_samples = len(pcm_chunk) // self.sample_width
        chunk_duration_ms = (num_samples / self.sample_rate) * 1000.0
        self._total_duration_ms += chunk_duration_ms

        rms = calculate_rms_energy(pcm_chunk, self.sample_width)
        is_speech_frame = rms >= self.rms_threshold

        if is_speech_frame:
            self._speech_duration_ms += chunk_duration_ms
            self._silence_duration_ms = 0.0

            if not self._speech_started:
                if self._speech_duration_ms >= self.min_speech_ms:
                    self._speech_started = True
                    logger.debug(f"Speech start detected! RMS={rms:.1f} >= {self.rms_threshold}")
                    return VADStatus.SPEECH_STARTED
                return VADStatus.SILENCE
            else:
                return VADStatus.SPEECH_ONGOING

        else:
            # Silence or background ambient noise frame
            if self._speech_started:
                self._silence_duration_ms += chunk_duration_ms
                if self._silence_duration_ms >= self.silence_ms:
                    logger.debug(
                        f"Speech end detected after {self._silence_duration_ms:.0f}ms silence | "
                        f"Total speech: {self._speech_duration_ms:.0f}ms"
                    )
                    return VADStatus.SPEECH_ENDED
                return VADStatus.SPEECH_ONGOING
            else:
                # Reset brief noise spikes that didn't reach min_speech_ms
                self._speech_duration_ms = max(0.0, self._speech_duration_ms - chunk_duration_ms)
                return VADStatus.SILENCE

    @property
    def is_speech_active(self) -> bool:
        """Return True if speech has started and endpoint has not triggered."""
        return self._speech_started
