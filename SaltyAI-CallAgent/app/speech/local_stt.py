"""Offline speech-to-text using faster-whisper."""

import asyncio
import logging

from app.config import settings
from app.models.schemas import STTResponse

logger = logging.getLogger(__name__)


class LocalWhisperSTTClient:
    def __init__(self) -> None:
        self._model = None
        self._load_lock = asyncio.Lock()

    async def _get_model(self):
        if self._model is None:
            async with self._load_lock:
                if self._model is None:
                    from faster_whisper import WhisperModel
                    logger.info("Loading local faster-whisper model: %s", settings.LOCAL_STT_MODEL)
                    self._model = await asyncio.to_thread(
                        WhisperModel,
                        settings.LOCAL_STT_MODEL,
                        device=settings.LOCAL_STT_DEVICE,
                        compute_type=settings.LOCAL_STT_COMPUTE_TYPE,
                    )
        return self._model

    @staticmethod
    def _wav_to_float32(pcm_audio: bytes, sample_rate: int):
        import numpy as np
        samples = np.frombuffer(pcm_audio, dtype="<i2").astype("float32") / 32768.0
        if sample_rate == 16000:
            return samples
        # Linear interpolation is adequate for telephone speech and avoids a
        # second audio dependency in the realtime path.
        target_len = max(1, int(len(samples) * 16000 / sample_rate))
        old_x = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
        new_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        return np.interp(new_x, old_x, samples).astype("float32")

    async def transcribe(self, pcm_audio: bytes, sample_rate: int = 8000,
                         language_code: str = "unknown", timeout_seconds: float = 30.0) -> STTResponse:
        if not pcm_audio or len(pcm_audio) < 640:
            return STTResponse(transcript="", language_code=language_code, is_empty=True)
        try:
            model = await self._get_model()
            audio = self._wav_to_float32(pcm_audio, sample_rate)
            lang = None if not language_code or language_code == "unknown" else language_code[:2]

            def run():
                segments, info = model.transcribe(audio, language=lang, beam_size=1, vad_filter=True)
                return " ".join(segment.text.strip() for segment in segments).strip(), info

            transcript, info = await asyncio.wait_for(asyncio.to_thread(run), timeout=timeout_seconds)
            detected = language_code if language_code != "unknown" else settings.DEFAULT_FALLBACK_LANGUAGE
            detected = f"{info.language}-IN" if getattr(info, "language", None) else detected
            logger.info("Local STT succeeded | lang=%s | transcript=%r", detected, transcript)
            return STTResponse(transcript=transcript, language_code=detected, is_empty=not transcript)
        except Exception as exc:
            logger.error("Local STT failed: %s", exc, exc_info=True)
            return STTResponse(transcript="", language_code=language_code, is_empty=True)


stt_client = LocalWhisperSTTClient()
