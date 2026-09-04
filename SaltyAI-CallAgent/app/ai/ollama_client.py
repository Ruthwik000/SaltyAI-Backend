"""Local Ollama/Qwen conversational client for phone calls."""

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
        system = (
            "You are SALTY, a concise marine safety assistant for fishermen. "
            "Answer in the caller's language when possible. Keep spoken answers under 55 words. "
            "This is a clearly labeled prototype using mock marine data: wind 14 knots, "
            "significant waves 1.6 metres, swell 0.9 metres, current 0.45 metres per second, "
            "sea temperature 28.4 Celsius, and a favorable demo fishing window from 06:00 to 11:00. "
            "For normal weather, fishing, or sea-condition questions, give a positive answer using "
            "these mock values and say 'demo data'. Do not claim this is a live safety clearance. "
            "For emergencies, give safe urgent guidance instead of blindly saying yes. "
            "Caller language: " + language + "."
        )
        messages = [{"role": "system", "content": system}]
        for turn in (conversation_history or [])[-10:]:
            if turn.get("role") in ("user", "assistant") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": message})
        try:
            async with httpx.AsyncClient(timeout=settings.OLLAMA_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{settings.OLLAMA_URL.rstrip('/')}/api/chat",
                    json={"model": settings.OLLAMA_MODEL, "messages": messages, "stream": False},
                )
            response.raise_for_status()
            content = (response.json().get("message", {}).get("content") or "").strip()
            # Qwen3 may include an internal reasoning block; never speak it
            # aloud to the caller.
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
            if not content:
                raise RuntimeError("Ollama returned an empty response")
            logger.info("Ollama response succeeded | model=%s | call_id=%s", settings.OLLAMA_MODEL, call_id)
            return AIQueryResponse(response=content, language=language, priority="normal")
        except Exception as exc:
            logger.error("Ollama request failed: %s", exc, exc_info=True)
            return AIQueryResponse(
                response="Sorry, I could not connect to my local AI brain. Please ask again.",
                language=language, priority="normal",
            )


ollama_client = OllamaClient()
