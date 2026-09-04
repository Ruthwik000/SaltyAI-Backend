"""
Real Sarvam Saaras Speech-to-Text (STT) Client.
Handles audio upload, regional language speech recognition, code-mixing, and language detection.
"""

import time
import logging
import httpx
from typing import Optional

from app.config import settings
from app.models.schemas import STTResponse
from app.speech.audio_utils import pcm_to_wav

logger = logging.getLogger(__name__)


class SarvamSTTClient:
    """Client for Sarvam Saaras Speech-to-Text API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.base_url = (base_url or settings.SARVAM_BASE_URL).rstrip("/")
        self.model = model or settings.SARVAM_STT_MODEL
        self.endpoint = f"{self.base_url}/speech-to-text"
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self, timeout_seconds: float) -> httpx.AsyncClient:
        """Get or initialize reusable persistent AsyncClient with HTTP keep-alive connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=timeout_seconds,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30.0),
            )
        return self._client

    async def close(self) -> None:
        """Close persistent HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def transcribe(
        self,
        pcm_audio: bytes,
        sample_rate: int = 8000,
        language_code: str = "unknown",
        timeout_seconds: float = 10.0,
    ) -> STTResponse:
        """
        Transcribe raw PCM audio bytes using Sarvam Saaras STT API.

        Args:
            pcm_audio: Raw 16-bit Linear PCM audio bytes.
            sample_rate: Audio sample rate in Hz (default 8000 for telephony).
            language_code: BCP-47 language code (e.g., 'ta-IN', 'hi-IN') or 'unknown' for auto-detect.
            timeout_seconds: HTTP request timeout.

        Returns:
            STTResponse containing transcript and detected language.
        """
        if not pcm_audio or len(pcm_audio) < 320:
            logger.debug("Audio buffer too short for STT transcription")
            return STTResponse(
                transcript="",
                language_code=language_code if language_code != "unknown" else settings.DEFAULT_FALLBACK_LANGUAGE,
                is_empty=True
            )

        # Convert raw PCM to standard WAV format in memory
        wav_bytes = pcm_to_wav(pcm_audio, sample_rate=sample_rate)

        headers = {
            "api-subscription-key": self.api_key,
        }

        files = {
            "file": ("audio.wav", wav_bytes, "audio/wav"),
        }

        data = {
            "model": self.model,
            "language_code": language_code if language_code else "unknown",
            "mode": "transcribe",
        }

        start_time = time.perf_counter()
        try:
            client = self._get_client(timeout_seconds)
            response = await client.post(
                self.endpoint,
                headers=headers,
                files=files,
                data=data,
            )

            latency_ms = (time.perf_counter() - start_time) * 1000


            if response.status_code != 200:
                logger.error(
                    f"Sarvam STT API returned status {response.status_code}: {response.text} "
                    f"(latency: {latency_ms:.1f}ms)"
                )
                return STTResponse(
                    transcript="",
                    language_code=language_code if language_code != "unknown" else settings.DEFAULT_FALLBACK_LANGUAGE,
                    is_empty=True
                )

            result_json = response.json()
            transcript = (result_json.get("transcript") or "").strip()
            detected_lang = result_json.get("language_code") or language_code

            if detected_lang == "unknown" or not detected_lang:
                detected_lang = settings.DEFAULT_FALLBACK_LANGUAGE

            logger.info(
                f"Sarvam STT succeeded in {latency_ms:.1f}ms | lang: {detected_lang} | "
                f"transcript: '{transcript}'"
            )

            return STTResponse(
                request_id=result_json.get("request_id"),
                transcript=transcript,
                language_code=detected_lang,
                is_empty=len(transcript) == 0,
            )

        except httpx.TimeoutException:
            logger.error(f"Sarvam STT request timed out after {timeout_seconds}s")
            return STTResponse(
                transcript="",
                language_code=language_code if language_code != "unknown" else settings.DEFAULT_FALLBACK_LANGUAGE,
                is_empty=True
            )
        except Exception as e:
            logger.error(f"Sarvam STT unexpected error: {e}", exc_info=True)
            return STTResponse(
                transcript="",
                language_code=language_code if language_code != "unknown" else settings.DEFAULT_FALLBACK_LANGUAGE,
                is_empty=True
            )


# Local speech recognition is the default. Sarvam remains available by setting
# VOICE_PROVIDER=sarvam in the environment.
if settings.VOICE_PROVIDER.lower() == "sarvam":
    stt_client = SarvamSTTClient()
else:
    from app.speech.local_stt import LocalWhisperSTTClient
    stt_client = LocalWhisperSTTClient()
