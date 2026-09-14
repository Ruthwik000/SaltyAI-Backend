"""
NVIDIA NIM intelligence client for SALTY AI Call Agent.

Talks to NIM's OpenAI-compatible /chat/completions endpoint. The default model,
openai/gpt-oss-20b, supports tool calling; the marine tools themselves live in
the data API's agent (app/ai/backend_client.py), which also runs on NIM.

This client carries NO marine data. It is used only under CALL_AGENT_TEST_MODE
to exercise the voice pipeline without the data API; real answers come from
the marine agent.
"""

import time
import asyncio
import logging
import httpx
from typing import Optional, List, Dict, Any, Tuple

from app.config import settings
from app.models.schemas import AIQueryResponse, Location
from app.ai.voice_text import (
    SALTY_AI_SYSTEM_INSTRUCTION,
    VOICE_FALLBACK_RESPONSES,
    detect_text_language,
    sanitize_speech_output,
)

logger = logging.getLogger(__name__)


class NIMClient:
    """Client for NVIDIA NIM hosted chat completions used for voice reasoning."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        max_tokens: Optional[int] = None,
    ):
        self._explicit_api_key = api_key
        self._explicit_model = model
        self._explicit_base_url = base_url
        self.timeout = timeout or settings.LLM_TIMEOUT_SECONDS
        self.max_retries = max_retries if max_retries is not None else settings.LLM_MAX_RETRIES
        self.max_tokens = max_tokens or settings.NIM_MAX_TOKENS
        self._client: Optional[httpx.AsyncClient] = None

    def _resolve_configuration(self) -> Tuple[str, str, str]:
        """Resolve active API key, model name, and base URL for NIM."""
        api_key = self._explicit_api_key if self._explicit_api_key is not None else settings.NVIDIA_API_KEY
        model = self._explicit_model if self._explicit_model is not None else settings.NIM_MODEL
        base_url = (self._explicit_base_url or settings.NIM_BASE_URL).rstrip("/")
        return api_key, model, base_url

    def _get_client(self, timeout_seconds: float) -> httpx.AsyncClient:
        """Get or initialize a reusable AsyncClient with keep-alive pooling."""
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

    def _fallback(self, language: str) -> AIQueryResponse:
        text = VOICE_FALLBACK_RESPONSES.get(language, VOICE_FALLBACK_RESPONSES["te-IN"])
        return AIQueryResponse(response=text, language=language, priority="normal")

    async def query(
        self,
        call_id: str,
        phone_number: str,
        message: str,
        language: str = "te-IN",
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        location: Optional[Location] = None,
    ) -> AIQueryResponse:
        """Generate a conversational response from NIM, with multi-turn history."""
        api_key, model, base_url = self._resolve_configuration()
        timeout = self.timeout
        max_retries = self.max_retries

        if not api_key:
            logger.warning(f"[NIM CLIENT] NVIDIA_API_KEY is not configured. Returning voice fallback for call {call_id}.")
            return self._fallback(language)

        messages: List[Dict[str, str]] = [{"role": "system", "content": SALTY_AI_SYSTEM_INSTRUCTION}]
        for turn in (conversation_history or [])[-6:]:
            content_text = turn.get("content", "")
            if not content_text:
                continue
            messages.append({"role": "user" if turn.get("role") == "user" else "assistant", "content": content_text})
        messages.append({"role": "user", "content": message.strip()})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": self.max_tokens,
        }
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        start_time = time.perf_counter()

        for attempt in range(max_retries + 1):
            try:
                client = self._get_client(timeout)
                response = await client.post(url, headers=headers, json=payload)
                latency_ms = (time.perf_counter() - start_time) * 1000

                if response.status_code == 200:
                    choices = response.json().get("choices") or []
                    generated_text = ""
                    if choices:
                        generated_text = ((choices[0].get("message") or {}).get("content") or "").strip()

                    if generated_text:
                        clean_response = sanitize_speech_output(generated_text)
                        detected_lang = detect_text_language(clean_response, default_language=language)
                        logger.info(
                            f"[NIM CLIENT] Success in {latency_ms:.1f}ms | Call {call_id} | "
                            f"Model: {model} | Lang: {detected_lang} | "
                            f"Length: {len(clean_response)} chars | Text: '{clean_response[:80]}...'"
                        )
                        return AIQueryResponse(response=clean_response, language=detected_lang, priority="normal")

                    logger.warning(f"[NIM CLIENT] Empty completion from {model} for call {call_id}")
                    break

                elif response.status_code == 429:
                    # A live caller cannot wait out a rate-limit window.
                    logger.warning(f"[NIM CLIENT] HTTP 429 rate limit for call {call_id}. Returning voice fallback.")
                    break
                elif response.status_code in (400, 401, 403, 404, 422):
                    logger.error(f"[NIM CLIENT] Client error HTTP {response.status_code}: {response.text} | call_id: {call_id}")
                    break
                elif response.status_code in (500, 502, 503, 504) and attempt < max_retries:
                    logger.warning(
                        f"[NIM CLIENT] HTTP {response.status_code} (attempt {attempt + 1}/{max_retries + 1}). Retrying..."
                    )
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                else:
                    logger.error(f"[NIM CLIENT] API error HTTP {response.status_code}: {response.text} | call_id: {call_id}")
                    break

            except httpx.TimeoutException:
                if attempt < max_retries:
                    logger.warning(f"[NIM CLIENT] Request timeout (attempt {attempt + 1}). Retrying...")
                    await asyncio.sleep(0.3)
                    continue
                logger.error(f"[NIM CLIENT] Request timed out after {timeout}s for call {call_id}")
                break

            except httpx.HTTPError as exc:
                logger.error(f"[NIM CLIENT] Cannot reach NIM at {base_url}: {exc} | call_id: {call_id}")
                break

        return self._fallback(language)


# Global singleton NIM client instance
nim_client = NIMClient()
