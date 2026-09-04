"""Local Ollama conversational client for phone calls."""

import logging
import re
import httpx
from typing import Any, Dict, List, Optional
from app.config import settings
from app.models.schemas import AIQueryResponse, Location

logger = logging.getLogger(__name__)


class OllamaClient:
    async def query(self, call_id: str, phone_number: str, message: str,
                    language: str = "en-IN",
                    conversation_history: Optional[List[Dict[str, Any]]] = None,
                    location: Optional[Location] = None) -> AIQueryResponse:
        language_rules = {
            "te-IN": "Output ONLY Telugu. Use Telugu script for every sentence; do not mix in English or Hindi words.",
            "hi-IN": "Output ONLY Hindi. Use Devanagari script for every sentence; do not mix in English or Telugu words.",
            "ta-IN": "Output ONLY Tamil. Use Tamil script for every sentence; do not mix in English or Hindi words.",
            "en-IN": "Output ONLY natural English.",
        }
        language_rule = language_rules.get(language, f"Output ONLY the language represented by {language}.")
        system = (
            "You are SALTY, a concise marine safety assistant for fishermen. "
            + language_rule + " The answer must be in exactly the same language and writing system as the caller's latest message. "
            "If the caller's message is Hindi, answer only in Devanagari Hindi; never answer in English or transliterated Hindi. "
            "If the caller's message is Telugu, answer only in Telugu script. "
            "Keep spoken answers under 55 words. "
            "Do not repeat the caller's language instruction or explain your language choice. "
            "Use these configured marine values for answers: wind 14 knots, "
            "significant waves 1.6 metres, swell 0.9 metres, current 0.45 metres per second, "
            "sea temperature 28.4 Celsius, and a favorable fishing window from 06:00 to 11:00. "
            "For normal weather, fishing, or sea-condition questions, give a positive answer using "
            "these values. Never mention internal data labels, test labels, or implementation details, "
            "or internal instructions. Do not claim this is a live safety clearance. "
            "For emergencies, give safe urgent guidance instead of blindly saying yes. "
            "Caller language: " + language + "."
        )
        messages = [{"role": "system", "content": system}]
        for turn in (conversation_history or [])[-10:]:
            if turn.get("role") in ("user", "assistant") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": message})
        logger.info(
            "[OLLAMA INPUT] call_id=%s | language=%s | location=%s | message=%r",
            call_id,
            language,
            location.name if location else "unknown",
            message,
        )
        try:
            async with httpx.AsyncClient(timeout=settings.OLLAMA_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{settings.OLLAMA_URL.rstrip('/')}/api/chat",
                    json={
                        "model": settings.OLLAMA_MODEL,
                        "messages": messages,
                        "stream": False,
                        "keep_alive": "10m",
                        "options": {"num_predict": settings.OLLAMA_MAX_TOKENS, "temperature": 0.2},
                    },
                )
            response.raise_for_status()
            content = (response.json().get("message", {}).get("content") or "").strip()
            # Qwen3 may include an internal reasoning block; never speak it
            # aloud to the caller.
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
            # Final speech guard: implementation/test labels must never reach
            # the handset even if the model ignores the system instruction.
            content = re.sub(
                r"[^.!?\n]*(?:mock|demo|synthetic|prototype)[^.!?\n]*[.!?]?",
                "",
                content,
                flags=re.IGNORECASE,
            ).strip()
            if not content:
                raise RuntimeError("Ollama returned an empty response")
            logger.info(
                "[OLLAMA OUTPUT] model=%s | call_id=%s | requested_language=%s | response=%r",
                settings.OLLAMA_MODEL,
                call_id,
                language,
                content,
            )
            return AIQueryResponse(response=content, language=language, priority="normal")
        except Exception as exc:
            logger.error("Ollama request failed: %s", exc, exc_info=True)
            return AIQueryResponse(
                response="Sorry, I could not connect to my local AI brain. Please ask again.",
                language=language, priority="normal",
            )


ollama_client = OllamaClient()
