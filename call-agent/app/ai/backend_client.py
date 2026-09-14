"""
Connector to the SALTY marine reasoning agent.

A phone call reaches the SAME agent the web console uses: POST /api/ai/query on
the data API, which runs the full tool-calling agent with all seventeen marine
tools - INCOIS sea state, PFZ advisories, tides, thunderstorms, the EEZ
boundary, satellite productivity. The caller therefore hears the same verified
numbers a person on the website would read, phrased for speech.

Two things were quietly stopping that from working:

  * The default timeout was ten seconds. A real answer calls INCOIS THREDDS,
    which steps seaward past land cells, and routinely needs fifteen to thirty.
    Every call therefore timed out, retried, timed out again, and the caller
    heard "sorry, I'm having trouble connecting" while the backend was up and
    answering perfectly.
  * The payload spelled fields the API did not read - conversation_history and
    location.latitude - so even a call that got through arrived with no memory
    and no position, and the tools answered about the wrong coast.

There is NO reasoning fallback when the agent cannot be reached. A toolless
model answering a marine question from memory is exactly the failure this
project exists to avoid: a remembered wave height sounds identical to a
measured one, and the caller is at sea. When the agent is unreachable the
caller is told so, in their own language, and pointed at the local bulletin.
"""

import time
import asyncio
import logging
import httpx
from typing import Optional, List, Dict, Any

from app.config import settings
from app.models.schemas import AIQueryRequest, AIQueryResponse, Location

logger = logging.getLogger(__name__)

# Fallback spoken messages by language if AI backend is temporarily unreachable
FALLBACK_SPOKEN_MESSAGES: Dict[str, str] = {
    "ta-IN": "மன்னிக்கவும், தகவல் பெறுவதில் சிறு தாமதம் ஏற்பட்டுள்ளது. சற்று நேரத்தில் மீண்டும் கேளுங்கள்.",
    "hi-IN": "क्षमा करें, जानकारी प्राप्त करने में समस्या आ रही है। कृपया थोड़ी देर बाद पुनः प्रयास करें.",
    "te-IN": "క్షమించండి, సమాచారం పొందడంలో సమస్య ఉంది. దయచేసి కాసేపటి తర్వాత మళ్ళీ ప్రయత్నించండి.",
    "ml-IN": "ക്ഷമിക്കണം, വിവരങ്ങൾ ലഭ്യമാക്കാൻ സാധിക്കുന്നില്ല. ദയവായി അല്പം കഴിഞ്ഞ് വീണ്ടും ശ്രമിക്കുക.",
    "kn-IN": "ಕ್ಷಮಿಸಿ, ಮಾಹಿತಿ ಪಡೆಯಲು ಸಾಧ್ಯವಾಗುತ್ತಿಲ್ಲ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
    "bn-IN": "দুঃখিত, তথ্য পেতে সমস্যা হচ্ছে। অনুগ্রহ করে কিছুক্ষণ পর আবার চেষ্টা করুন.",
    "mr-IN": "क्षमस्व, माहिती मिळवण्यात अडचण येत आहे. कृपया थोड्या वेळाने पुन्हा प्रयत्न करा.",
    "en-IN": "Sorry, I'm having trouble connecting right now. Please ask your question again.",
}


class AIBackendClient:
    """Client for communicating with the main SALTY AI LangGraph backend."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
    ):
        self.base_url = (base_url or settings.AI_BACKEND_URL).rstrip("/")
        self.timeout = timeout or settings.AI_BACKEND_TIMEOUT_SECONDS
        self.max_retries = max_retries or settings.AI_BACKEND_MAX_RETRIES
        self.retry_delay = retry_delay or settings.AI_BACKEND_RETRY_DELAY_SECONDS
        self.query_endpoint = f"{self.base_url}/api/ai/query"

    async def query(
        self,
        call_id: str,
        phone_number: str,
        message: str,
        language: str = "ta-IN",
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        location: Optional[Location] = None,
        state: Optional[str] = None,
    ) -> AIQueryResponse:
        """
        Send caller query and multi-turn context to Main SALTY AI Backend.

        Args:
            call_id: Unique call identifier.
            phone_number: Caller phone number.
            message: Spoken message from caller.
            language: Caller's current detected language code.
            conversation_history: List of recent conversation turns.
            location: Known location if available.

        Returns:
            AIQueryResponse with synthesized answer text and priority.
        """
        # CALL_AGENT_TEST_MODE is a development flag for exercising the audio
        # pipeline without the data API running. It is the ONLY path to the
        # toolless model, and it must never be on in a real deployment: that
        # model has no marine data and will answer from memory.
        if settings.CALL_AGENT_TEST_MODE:
            logger.warning(
                "CALL_AGENT_TEST_MODE is on: answering from NIM without marine tools, "
                "with no live marine data. Do not use this on a real call."
            )
            from app.ai.nim_client import nim_client
            return await nim_client.query(
                call_id=call_id,
                phone_number=phone_number,
                message=message,
                language=language or settings.DEFAULT_FALLBACK_LANGUAGE,
                conversation_history=conversation_history or [],
                location=location,
            )

        # The caller's coastal state, resolved from the harbour they named, so
        # the advisory feed can match. Copied rather than mutated: the session
        # owns that Location object.
        if location is not None and state and not location.state:
            location = location.model_copy(update={"state": state})

        request_payload = AIQueryRequest(
            call_id=call_id,
            phone_number=phone_number,
            language=language,
            message=message,
            conversation_history=conversation_history or [],
            location=location,
        )




        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Call-ID": call_id,
        }

        payload_dict = request_payload.model_dump(mode="json")
        # The agent reads "history" and "query"; this client's schema calls the
        # same things "conversation_history" and "message". Send both spellings
        # rather than depend on which side is updated first - the cost is a few
        # bytes, and the cost of getting it wrong is a caller whose follow-up
        # question means nothing.
        payload_dict["history"] = payload_dict.get("conversation_history") or []
        payload_dict["query"] = message

        start_time = time.perf_counter()


        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        self.query_endpoint,
                        headers=headers,
                        json=payload_dict,
                    )

                latency_ms = (time.perf_counter() - start_time) * 1000

                if response.status_code == 200:
                    data = response.json()
                    validated_response = AIQueryResponse.model_validate(data)
                    logger.info(
                        f"AI Backend query succeeded in {latency_ms:.1f}ms | call_id: {call_id} | "
                        f"priority: {validated_response.priority}"
                    )
                    return validated_response

                elif response.status_code in (500, 502, 503, 504) and attempt < self.max_retries:
                    logger.warning(
                        f"AI Backend returned HTTP {response.status_code} (attempt {attempt + 1}/{self.max_retries + 1}). "
                        f"Retrying in {self.retry_delay}s..."
                    )
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue

                else:
                    logger.error(
                        f"AI Backend query failed with HTTP {response.status_code}: {response.text} | "
                        f"call_id: {call_id}"
                    )
                    break

            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < self.max_retries:
                    logger.warning(
                        f"AI Backend connection error: {exc} (attempt {attempt + 1}/{self.max_retries + 1}). "
                        f"Retrying in {self.retry_delay}s..."
                    )
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue
                else:
                    logger.error(
                        f"AI Backend connection failed after {self.max_retries + 1} attempts: {exc} | "
                        f"call_id: {call_id}"
                    )
                    break
            except Exception as exc:
                logger.error(f"Unexpected error calling AI Backend: {exc} | call_id: {call_id}", exc_info=True)
                break

        # Every attempt failed. Say so plainly. The one thing this must not do
        # is hand the question to a model with no data and read out whatever it
        # invents.
        logger.error(
            f"Marine agent unreachable at {self.query_endpoint} after "
            f"{self.max_retries + 1} attempt(s) | call_id: {call_id}"
        )
        fallback_text = FALLBACK_SPOKEN_MESSAGES.get(
            language,
            FALLBACK_SPOKEN_MESSAGES.get(settings.DEFAULT_FALLBACK_LANGUAGE,
                                         FALLBACK_SPOKEN_MESSAGES["en-IN"]),
        )
        return AIQueryResponse(
            response=fallback_text,
            language=language,
            priority="normal",
        )


# Singleton AI backend client instance
ai_backend_client = AIBackendClient()
