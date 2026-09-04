"""
Real Sarvam Bulbul Text-to-Speech (TTS) Client.
Synthesizes natural Indian regional language speech and converts to telephony-ready 8kHz Linear PCM.
"""

import time
import base64
import logging
import httpx
from typing import Optional, Dict

from app.config import settings
from app.models.schemas import TTSResponse
from app.speech.audio_utils import wav_to_pcm

logger = logging.getLogger(__name__)

# Preferred speaker mapping for regional languages in Bulbul
LANGUAGE_SPEAKER_MAP: Dict[str, str] = {
    "ta-IN": "shubh",
    "hi-IN": "shubh",
    "te-IN": "shubh",
    "ml-IN": "shubh",
    "kn-IN": "shubh",
    "bn-IN": "shubh",
    "mr-IN": "shubh",
    "en-IN": "shubh",
}


class SarvamTTSClient:
    """Client for Sarvam Bulbul Text-to-Speech API with in-memory caching."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.base_url = (base_url or settings.SARVAM_BASE_URL).rstrip("/")
        self.model = model or settings.SARVAM_TTS_MODEL
        self.endpoint = f"{self.base_url}/text-to-speech"
        self._cache: Dict[str, TTSResponse] = {}
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

    async def synthesize(
        self,
        text: str,
        language_code: str = "te-IN",
        speaker: Optional[str] = None,
        sample_rate: int = 8000,
        timeout_seconds: float = 10.0,
    ) -> TTSResponse:
        """
        Synthesize text into raw 16-bit Linear PCM audio suitable for Exotel AgentStream.
        Uses in-memory cache for static or repeated phrases.
        """
        clean_text = text.strip() if text else ""
        if not clean_text:
            return TTSResponse(pcm_audio=b"", sample_rate=sample_rate, duration_seconds=0.0)

        chosen_speaker = speaker or LANGUAGE_SPEAKER_MAP.get(language_code, settings.SARVAM_DEFAULT_SPEAKER)

        # Check cache
        cache_key = f"{clean_text}:{language_code}:{chosen_speaker}:{sample_rate}"
        if cache_key in self._cache:
            logger.debug(f"TTS cache hit for '{clean_text[:30]}...'")
            return self._cache[cache_key]

        # Truncate overly long text for phone voice responsiveness (max ~500 chars for spoken turns)
        if len(clean_text) > 1500:
            clean_text = clean_text[:1500]

        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "inputs": [clean_text],
            "target_language_code": language_code,
            "speaker": chosen_speaker,
            "speech_sample_rate": sample_rate,
            "model": self.model,
        }

        start_time = time.perf_counter()
        try:
            client = self._get_client(timeout_seconds)
            response = await client.post(
                self.endpoint,
                headers=headers,
                json=payload,
            )

            latency_ms = (time.perf_counter() - start_time) * 1000


            if response.status_code != 200:
                logger.error(
                    f"Sarvam TTS API returned status {response.status_code}: {response.text} "
                    f"(latency: {latency_ms:.1f}ms)"
                )
                return TTSResponse(pcm_audio=b"", sample_rate=sample_rate, duration_seconds=0.0)

            result_json = response.json()
            audios = result_json.get("audios") or []
            if not audios:
                logger.error("Sarvam TTS returned empty audios array")
                return TTSResponse(pcm_audio=b"", sample_rate=sample_rate, duration_seconds=0.0)

            # Base64 decode the synthesized WAV
            b64_audio = audios[0]
            wav_bytes = base64.b64decode(b64_audio)

            # Extract raw Linear PCM and ensure target sample rate (8000 Hz)
            pcm_bytes, effective_rate = wav_to_pcm(wav_bytes, target_sample_rate=sample_rate)
            duration_sec = len(pcm_bytes) / (effective_rate * 2) if effective_rate > 0 else 0.0

            logger.info(
                f"Sarvam TTS synthesized {len(clean_text)} chars into {len(pcm_bytes)} PCM bytes "
                f"({duration_sec:.2f}s) in {latency_ms:.1f}ms | lang: {language_code}"
            )

            res = TTSResponse(
                request_id=result_json.get("request_id"),
                pcm_audio=pcm_bytes,
                sample_rate=effective_rate,
                duration_seconds=duration_sec,
            )
            if len(self._cache) < 200:
                self._cache[cache_key] = res
            return res


        except httpx.TimeoutException:
            logger.error(f"Sarvam TTS request timed out after {timeout_seconds}s")
            return TTSResponse(pcm_audio=b"", sample_rate=sample_rate, duration_seconds=0.0)
        except Exception as e:
            logger.error(f"Sarvam TTS unexpected error: {e}", exc_info=True)
            return TTSResponse(pcm_audio=b"", sample_rate=sample_rate, duration_seconds=0.0)


# Local Piper speech is the default. Sarvam remains available by setting
# VOICE_PROVIDER=sarvam in the environment.
if settings.VOICE_PROVIDER.lower() == "sarvam":
    tts_client = SarvamTTSClient()
else:
    from app.speech.local_tts import LocalPiperTTSClient
    tts_client = LocalPiperTTSClient()
