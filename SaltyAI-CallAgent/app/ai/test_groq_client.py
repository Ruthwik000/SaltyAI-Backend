"""
Groq Intelligence Client for SALTY AI Call Agent (Development Test Mode).
Provides real-time, low-latency multi-turn conversational intelligence for live telephone calls.
"""

import re
import time
import asyncio
import logging
import httpx
from typing import Optional, List, Dict, Any, Tuple

from app.config import settings
from app.models.schemas import AIQueryResponse, Location

logger = logging.getLogger(__name__)

# System persona and strict voice conversational guidelines for telephone playback
SALTY_AI_SYSTEM_INSTRUCTION = """You are SALTY AI, an intelligent, helpful marine safety voice assistant talking to a fisherman over a real phone call.

CRITICAL VOICE RULES:
1. Spoken Delivery: Your response will be synthesized directly into speech over a telephone line.
2. Length: Keep responses CONCISE, natural, and direct (normally 1 to 2 short sentences, maximum 35 to 40 spoken words).
3. Direct Answers: Answer the question immediately. Do NOT repeat or echo the caller's question.
4. Plain Text Only: NEVER use markdown formatting, asterisks (*), bold (**), headings (#), bullet points, numbered lists, or JSON. Plain conversational sentences only.
5. Language Matching & Code-Switching:
   - If the caller speaks in Telugu, respond in natural spoken Telugu.
   - If the caller speaks in Hindi, respond in natural spoken Hindi.
   - If the caller speaks in English, respond in natural spoken English.
   - If the caller speaks in Telugu-English or Hindi-English code-switching, respond naturally matching their style.
   - For short follow-up questions (e.g., "morning?", "safe ah?"), maintain the ongoing conversational language and context.
6. Marine Knowledge & Honesty:
   - You understand marine safety, weather concepts, wind, waves, tides, engine safety, and precautions.
   - In this test environment, live sensor/satellite feeds are not yet connected. If asked for live weather forecasts, cyclone alerts, or PFZ coordinates, clearly state in 1 short sentence that live data is not connected in this test mode, while providing general marine safety advice.
   - NEVER hallucinate fake weather readings, coordinates, or fake emergency warnings.
7. Memory: Remember previously mentioned locations, dates, and questions to understand short follow-up questions."""

# Voice-friendly fallback responses when Groq is temporarily unavailable
VOICE_FALLBACK_RESPONSES: Dict[str, str] = {
    "te-IN": "క్షమించండి, సమాధానం ఇవ్వడంలో చిన్న సమస్య వచ్చింది. దయచేసి మీ ప్రశ్నను మళ్ళీ చెప్పండి.",
    "hi-IN": "क्षमा करें, समझने में समस्या आ रही है। कृपया अपनी बात दोबारा कहें।",
    "en-IN": "Sorry, I'm having trouble processing that right now. Please say that again.",
    "ta-IN": "மன்னிக்கவும், தகவல் பெறுவதில் சிறு தாமதம் ஏற்பட்டுள்ளது. மீண்டும் சொல்லுங்கள்.",
    "ml-IN": "ക്ഷമിക്കണം, വിവരങ്ങൾ ലഭ്യമല്ല. ദయവായി വീണ്ടും പറയുക.",
    "kn-IN": "ಕ್ಷಮಿಸಿ, ಮಾಹಿತಿ ಪ್ರಕ್ರಿಯೆಗೊಳಿಸಲು ಸಾಧ್ಯವಾಗುತ್ತಿಲ್ಲ. దయవిಟ್ಟು ಮತ್ತೆ ಹೇಳಿ.",
    "bn-IN": "দুঃখিত, বুঝতে समस्या হচ্ছে। অনুগ্রহ করে আবার বলুন।",
    "mr-IN": "क्षमस्व, प्रक्रिया करण्यात अडचण येत आहे. कृपया पुन्हा सांगा.",
}


def detect_text_language(text: str, default_language: str = "te-IN") -> str:
    """
    Detect the primary BCP-47 language of text based on Unicode character blocks.
    Ensures Sarvam Bulbul TTS receives the exact matching regional language code.
    """
    if not text:
        return default_language

    # Telugu script block: U+0C00 - U+0C7F
    if re.search(r"[\u0C00-\u0C7F]", text):
        return "te-IN"

    # Devanagari script block (Hindi/Marathi): U+0900 - U+097F
    if re.search(r"[\u0900-\u097F]", text):
        return "hi-IN"

    # Tamil script block: U+0B80 - U+0BFF
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "ta-IN"

    # Malayalam script block: U+0D00 - U+0D7F
    if re.search(r"[\u0D00-\u0D7F]", text):
        return "ml-IN"

    # Kannada script block: U+0C80 - U+0CFF
    if re.search(r"[\u0C80-\u0CFF]", text):
        return "kn-IN"

    # Bengali script block: U+0980 - U+09FF
    if re.search(r"[\u0980-\u09FF]", text):
        return "bn-IN"

    # If predominantly Latin/ASCII characters, classify as English
    latin_chars = len(re.findall(r"[a-zA-Z]", text))
    if latin_chars >= 3:
        return "en-IN"

    return default_language


def sanitize_speech_output(text: str) -> str:
    """Clean generated text to ensure natural, complete Text-to-Speech audio output without truncation."""
    if not text:
        return ""
    cleaned = text
    # 1. Strip reasoning and code blocks if any
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"```(?:json)?[\s\S]*?```", "", cleaned)
    # 2. Strip speaker role prefixes (e.g. "SALTY AI:", "Assistant:", "AI:", "Bot:", "Model:", "System:")
    cleaned = re.sub(r"^(?:SALTY\s+AI|Assistant|Model|AI|Bot|System)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    # 3. Strip leading conversational artifacts like ': "', ':"', ': ', ' - '
    cleaned = re.sub(r"^[:\s\-–—]+", "", cleaned)
    # 4. Remove markdown formatting symbols (*, #, _, `, ~, >, [, ])
    cleaned = re.sub(r"[*#_`~>\[\]]", "", cleaned)
    cleaned = re.sub(r"^\s*-\s+", "", cleaned, flags=re.MULTILINE)
    # 5. Strip outer matching quotation marks if the full response is quoted
    cleaned = cleaned.strip()
    if len(cleaned) >= 2 and ((cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'"))):
        cleaned = cleaned[1:-1].strip()
    # 6. Strip leading unclosed quote if leftover from prefix stripping
    if cleaned.startswith('"') or cleaned.startswith("'"):
        cleaned = cleaned[1:].strip()
    # 7. Collapse multiple whitespace / newlines into a single space
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class GroqTestClient:
    """Client for Groq API used during Development Test Mode."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        self._explicit_api_key = api_key
        self._explicit_model = model
        self._explicit_base_url = base_url
        self.timeout = timeout or settings.LLM_TIMEOUT_SECONDS
        self.max_retries = max_retries or settings.LLM_MAX_RETRIES
        self._client: Optional[httpx.AsyncClient] = None

    def _resolve_configuration(self) -> Tuple[str, str, str]:
        """Resolve active API key, model name, and base URL for Groq."""
        api_key = self._explicit_api_key if self._explicit_api_key is not None else settings.GROQ_API_KEY
        model = self._explicit_model if self._explicit_model is not None else settings.GROQ_MODEL
        base_url = (self._explicit_base_url or settings.GROQ_BASE_URL).rstrip("/")
        return api_key, model, base_url


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

    async def query(
        self,
        call_id: str,
        phone_number: str,
        message: str,
        language: str = "te-IN",
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        location: Optional[Location] = None,
    ) -> AIQueryResponse:
        """
        Generate conversational response using Groq API with multi-turn history.
        """
        api_key, model, base_url = self._resolve_configuration()
        timeout = self.timeout or settings.LLM_TIMEOUT_SECONDS
        max_retries = self.max_retries or settings.LLM_MAX_RETRIES

        if not api_key:
            logger.warning(
                f"[GROQ TEST MODE] GROQ_API_KEY is not configured. Returning voice fallback response for call {call_id}."
            )
            fallback_text = VOICE_FALLBACK_RESPONSES.get(language, VOICE_FALLBACK_RESPONSES["te-IN"])
            return AIQueryResponse(response=fallback_text, language=language, priority="normal")

        # Build messages array: System instruction + bounded conversation history + current message
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": SALTY_AI_SYSTEM_INSTRUCTION}
        ]
        history = conversation_history or []
        recent_history = history[-6:] if len(history) > 6 else history

        for turn in recent_history:
            role = turn.get("role", "user")
            content_text = turn.get("content", "")
            if not content_text:
                continue
            msg_role = "user" if role == "user" else "assistant"
            messages.append({"role": msg_role, "content": content_text})

        # Append current user utterance
        user_text = message.strip()
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 800,
        }

        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        start_time = time.perf_counter()

        for attempt in range(max_retries + 1):
            try:
                client = self._get_client(timeout)
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                )

                latency_ms = (time.perf_counter() - start_time) * 1000

                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices") or []
                    generated_text = ""
                    if choices:
                        msg = choices[0].get("message") or {}
                        generated_text = msg.get("content", "").strip()

                    if generated_text:
                        clean_response = sanitize_speech_output(generated_text)
                        detected_lang = detect_text_language(clean_response, default_language=language)

                        logger.info(
                            f"[GROQ TEST MODE] Success in {latency_ms:.1f}ms | Call {call_id} | "
                            f"Model: {model} | Lang: {detected_lang} | "
                            f"Length: {len(clean_response)} chars | Text: '{clean_response[:80]}...'"
                        )
                        return AIQueryResponse(
                            response=clean_response,
                            language=detected_lang,
                            priority="normal",
                        )
                    else:
                        logger.warning(f"[GROQ TEST MODE] Received empty choices from Groq API for call {call_id}")
                        break

                elif response.status_code == 429:
                    logger.warning(
                        f"[GROQ TEST MODE] HTTP 429 Rate Limit Exceeded for call {call_id}. "
                        f"Returning immediate voice fallback."
                    )
                    break
                elif response.status_code in (400, 401, 403, 404):
                    logger.error(
                        f"[GROQ TEST MODE] Client error HTTP {response.status_code}: {response.text} | call_id: {call_id}"
                    )
                    break
                elif response.status_code in (500, 502, 503, 504) and attempt < max_retries:
                    logger.warning(
                        f"[GROQ TEST MODE] HTTP {response.status_code} (attempt {attempt + 1}/{max_retries + 1}). "
                        f"Retrying..."
                    )
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                else:
                    logger.error(
                        f"[GROQ TEST MODE] API error HTTP {response.status_code}: {response.text} | call_id: {call_id}"
                    )
                    break

            except httpx.TimeoutException:
                if attempt < max_retries:
                    logger.warning(f"[GROQ TEST MODE] Request timeout (attempt {attempt + 1}). Retrying...")
                    await asyncio.sleep(0.3)
                    continue
                logger.error(f"[GROQ TEST MODE] Request timed out after {timeout}s for call {call_id}")
                break

            except Exception as e:
                logger.error(f"[GROQ TEST MODE] Unexpected error calling Groq API: {e} | call_id: {call_id}", exc_info=True)
                break

        # Fallback response if Groq fails or times out
        fallback_text = VOICE_FALLBACK_RESPONSES.get(language, VOICE_FALLBACK_RESPONSES["te-IN"])
        return AIQueryResponse(
            response=fallback_text,
            language=language,
            priority="normal",
        )


# Global singleton Groq client instance
groq_test_client = GroqTestClient()
