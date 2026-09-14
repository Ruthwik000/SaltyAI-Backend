"""
Layered Emergency Detection & Forwarding System for SALTY AI Call Agent.
Implements fast multilingual keyword matching, semantic distress intent analysis,
and asynchronous dispatch to the main backend rescue coordinator.
"""

import re
import time
import asyncio
import logging
import httpx
from typing import Optional, Dict, List, Tuple

from app.config import settings
from app.models.schemas import (
    EmergencyEventRequest,
    EmergencyEventResponse,
    Location,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# Layer 1: Multilingual Distress Lexicon
# ==============================================================================

DISTRESS_PATTERNS = {
    "universal": [
        r"\b(?:sos|mayday|help|emergency|sinking|danger|engine\s+fail(?:ed)?|engine\s+stopped|stuck\s+in\s+the\s+sea|lost\s+at\s+sea|man\s+overboard|capsiz(?:ed|ing)|drifting)\b",
    ],
    "ta": [  # Tamil
        r"காப்பாத்துங்க",
        r"ஆபத்து",
        r"படகு\s*மூழ்கு(?:து|கிறது)",
        r"என்ஜின்\s*பழுது",
        r"கடலில்\s*மாட்டி",
        r"புயல்\s*ஆபத்து",
        r"அவசரம்",
        r"உயிருக்கு\s*ஆபத்து",
        r"தண்ணி\s*உள்ள\s*வருது",
        r"காப்பாத்து",
    ],
    "hi": [  # Hindi
        r"बचाओ",
        r"मदद",
        r"डूब\s*रही\s*है",
        r"नाव\s*पलट\s*गई",
        r"इंजन\s*खराब",
        r"तूफान\s*में\s*फंसे",
        r"खतरा\s*है",
        r"आपातकाल",
        r"पानी\s*भर\s*रहा\s*है",
    ],
    "te": [  # Telugu
        r"రక్షించండి",
        r"సహాయం",
        r"సహాయం\s*కావాలి",
        r"మునిగిపోతుంది",
        r"పడవ\s*ప్రమాదం",
        r"బోట్\s*మునిగిపోతుంది",
        r"ఇంజిన్\s*ఆగిపోయింది",
        r"ఇంజిన్\s*చెడిపోయింది",
        r"బోట్\s*ఇంజిన్\s*ఆగిపోయింది",
        r"ప్రమాదంలో\s*ఉన్నాము",
        r"తుఫాను",
        r"నీళ్లు\s*వస్తున్నాయి",
        r"కాపాడండి",
    ],

    "ml": [  # Malayalam
        r"രക്ഷിക്കണേ",
        r"സഹായം",
        r"ബോട്ട്\s*മുങ്ങുന്നു",
        r"എഞ്ചിൻ\s*തകരാറിലായി",
        r"അപകടത്തിലാണ്",
        r"ചുഴലിക്കാറ്റ്",
        r"വെള്ളം\s*കയറുന്നു",
    ],
    "bn": [  # Bengali
        r"বাঁচাও",
        r"সাহায্য\s*করুন",
        r"নৌকা\s*ডুবছে",
        r"ইঞ্জিন\s*নষ্ট",
        r"বিপদে\s*পড়েছি",
        r"ঝড়ের\s*মুখে",
    ],
    "mr": [  # Marathi
        r"वाचवा",
        r"मदत\s*करा",
        r"बोट\s*बुडत\s*आहे",
        r"इंजिन\s*बंद\s*पडले",
        r"धोक्यात\s*आहोत",
    ],
}

# Compile all regex patterns for fast Layer 1 evaluation
COMPILED_PATTERNS = [
    re.compile(pat, re.IGNORECASE | re.UNICODE)
    for patterns in DISTRESS_PATTERNS.values()
    for pat in patterns
]

# Layer 2 Semantic indicators: Co-occurrence of vessel/marine term + critical failure/danger term
VESSEL_TERMS = {"boat", "ship", "vessel", "engine", "motor", "motoru", "padagu", "nau", "naav", "donga", "vallam"}
FAILURE_TERMS = {"stopped", "broken", "leak", "leaking", "fire", "smoke", "broke", "dead", "pazhudhu", "kettupochu", "band", "kharaab"}


class EmergencyDetector:
    """Multi-tiered emergency detector and dispatcher."""

    def __init__(self, backend_url: Optional[str] = None):
        self.backend_url = (backend_url or settings.AI_BACKEND_URL).rstrip("/")
        self.emergency_endpoint = f"{self.backend_url}/api/emergency"

    def detect(self, transcript: str) -> Tuple[bool, str]:
        """
        Evaluate transcript using Layer 1 and Layer 2 detectors.

        Returns:
            (is_emergency: bool, reason: str)
        """
        if not transcript or not transcript.strip():
            return False, ""

        clean_text = transcript.strip()

        # --- Layer 1: Fast Multilingual Regex/Keyword Matching ---
        for pat in COMPILED_PATTERNS:
            if pat.search(clean_text):
                logger.warning(f"Layer 1 Emergency Detected in transcript: '{clean_text}' (Pattern match)")
                return True, "layer_1_keyword_match"

        # --- Layer 2: Semantic Intent & Cross-Language Indicator Co-occurrence ---
        words = set(re.findall(r"\w+", clean_text.lower()))
        if (words & VESSEL_TERMS) and (words & FAILURE_TERMS):
            logger.warning(f"Layer 2 Emergency Detected: Vessel failure intent in transcript '{clean_text}'")
            return True, "layer_2_semantic_vessel_failure"

        return False, ""

    async def dispatch_emergency(
        self,
        call_id: str,
        phone_number: str,
        transcript: str,
        language: str = "ta-IN",
        location: Optional[Location] = None,
    ) -> EmergencyEventResponse:
        """
        Layer 3: Asynchronously forward emergency event to Main SALTY AI Backend.
        """
        request_payload = EmergencyEventRequest(
            call_id=call_id,
            phone_number=phone_number,
            language=language,
            transcript=transcript,
            location=location,
        )

        headers = {
            "Content-Type": "application/json",
            "X-Call-ID": call_id,
            "X-Priority": "EMERGENCY",
        }

        logger.warning(
            f"DISPATCHING EMERGENCY to Main Backend: call_id={call_id}, "
            f"phone={phone_number}, transcript='{transcript}'"
        )

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    self.emergency_endpoint,
                    headers=headers,
                    json=request_payload.model_dump(mode="json"),
                )

            if response.status_code in (200, 201, 202):
                data = response.json()
                logger.info(f"Emergency dispatched successfully to Main Backend: {data}")
                return EmergencyEventResponse.model_validate(data)
            else:
                logger.error(
                    f"Emergency dispatch returned HTTP {response.status_code}: {response.text}"
                )
                return EmergencyEventResponse(
                    status="forward_failed",
                    message=f"HTTP {response.status_code}"
                )
        except Exception as e:
            logger.error(f"Failed to forward emergency event to {self.emergency_endpoint}: {e}")
            return EmergencyEventResponse(
                status="network_error",
                message=str(e)
            )

    def trigger_async_dispatch(
        self,
        call_id: str,
        phone_number: str,
        transcript: str,
        language: str = "ta-IN",
        location: Optional[Location] = None,
    ) -> asyncio.Task:
        """
        Schedule non-blocking background task to forward emergency to main backend.
        Ensures caller's audio loop is never delayed while rescue alerts fire.
        """
        return asyncio.create_task(
            self.dispatch_emergency(
                call_id=call_id,
                phone_number=phone_number,
                transcript=transcript,
                language=language,
                location=location,
            )
        )


# Singleton emergency detector instance
emergency_detector = EmergencyDetector()
