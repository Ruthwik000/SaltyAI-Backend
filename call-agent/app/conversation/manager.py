"""
Natural Conversation and Session Manager for SALTY AI Call Agent.
Maintains multi-turn context, bounded memory, dynamic language tracking, and caller metadata.
"""

import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from app.config import settings
from app.models.schemas import Location

logger = logging.getLogger(__name__)

# The port gazetteer lives with the data API, in the repo's backend/ directory. Importing
# it here lets the call agent tell a place it can position from ("Kakinada")
# apart from one it cannot ("Delhi"), and ask again instead of sending a name
# that will resolve to nothing. If it is not reachable - a deployment that
# ships the call agent alone - the name is still forwarded and the API
# resolves it there, so this degrades rather than breaks.
try:  # pragma: no cover - import path depends on how the service is deployed
    import os as _os
    import sys as _sys
    _BACKEND_ROOT = _os.path.abspath(
        _os.path.join(_os.path.dirname(__file__), "..", "..", "..", "backend")
    )
    if _BACKEND_ROOT not in _sys.path:
        _sys.path.append(_BACKEND_ROOT)
    from ports import resolve as resolve_port  # pyright: ignore[reportMissingImports] - resolved via _BACKEND_ROOT at runtime; absence is a supported deployment
except Exception:  # noqa: BLE001 - absence is a supported deployment, not an error
    resolve_port = None
    logger.info("Port gazetteer not available here; place names go to the API unresolved.")


@dataclass
class CallSession:
    """Represents an active call session and its conversational state."""
    call_id: str
    stream_sid: str
    phone_number: str
    language: str = "ta-IN"
    location: Optional[Location] = None
    emergency_state: bool = False
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    state: str = "IDLE"  # IDLE, LISTENING, PROCESSING, SPEAKING
    awaiting_location: bool = False

    def touch(self) -> None:
        """Update the last activity timestamp."""
        self.last_activity = time.time()

    def add_user_message(self, message: str, detected_language: Optional[str] = None) -> None:
        """Record user's spoken turn, updating language and history window."""
        self.touch()
        self.turn_count += 1
        if detected_language and detected_language != "unknown":
            self.language = detected_language

        self.conversation_history.append({
            "role": "user",
            "content": message.strip()
        })
        self._trim_history()

    def add_assistant_message(self, message: str) -> None:
        """Record assistant's spoken turn."""
        self.touch()
        self.conversation_history.append({
            "role": "assistant",
            "content": message.strip()
        })
        self._trim_history()

    def _trim_history(self) -> None:
        """Keep only the most recent N turns to bound memory usage."""
        max_items = settings.MAX_CONVERSATION_HISTORY_TURNS * 2  # user + assistant pairs
        if len(self.conversation_history) > max_items:
            self.conversation_history = self.conversation_history[-max_items:]

    def get_history_payload(self) -> List[Dict[str, Any]]:
        """Return history suitable for AI backend query."""
        return list(self.conversation_history)

    def has_location(self) -> bool:
        """A usable position: coordinates, or at least a name to resolve from."""
        if not self.location:
            return False
        has_point = (
            self.location.latitude is not None and self.location.longitude is not None
        )
        return bool(has_point or self.location.name)


class ConversationManager:
    """Manages active call sessions across the voice gateway."""

    def __init__(self):
        self._sessions: Dict[str, CallSession] = {}
        self._stream_to_call_map: Dict[str, str] = {}

    def create_session(
        self,
        call_id: str,
        stream_sid: str,
        phone_number: str,
        initial_language: Optional[str] = None,
        location: Optional[Location] = None,
    ) -> CallSession:
        """Create and register a new call session."""
        lang = initial_language or settings.DEFAULT_FALLBACK_LANGUAGE
        session = CallSession(
            call_id=call_id,
            stream_sid=stream_sid,
            phone_number=phone_number,
            language=lang,
            location=location,
        )
        self._sessions[call_id] = session
        self._stream_to_call_map[stream_sid] = call_id
        logger.info(
            f"Created call session: call_id={call_id}, stream_sid={stream_sid}, "
            f"phone={phone_number}, lang={lang}"
        )
        return session

    def get_session(self, call_id: str) -> Optional[CallSession]:
        """Retrieve session by call_id."""
        return self._sessions.get(call_id)

    def get_session_by_stream(self, stream_sid: str) -> Optional[CallSession]:
        """Retrieve session by Exotel stream_sid."""
        call_id = self._stream_to_call_map.get(stream_sid)
        if call_id:
            return self._sessions.get(call_id)
        return None

    def update_location(self, call_id: str, location: Location) -> None:
        """Update caller location if received or resolved."""
        session = self.get_session(call_id)
        if session:
            session.location = location
            session.touch()
            logger.info(f"Updated location for call_id={call_id}: {location}")

    def set_emergency(self, call_id: str, is_emergency: bool = True) -> None:
        """Flag call session as emergency state."""
        session = self.get_session(call_id)
        if session:
            session.emergency_state = is_emergency
            session.touch()
            logger.warning(f"Session emergency_state set to {is_emergency} for call_id={call_id}")

    def end_session(self, call_id: str) -> Optional[CallSession]:
        """Clean up and remove session upon call termination."""
        session = self._sessions.pop(call_id, None)
        if session:
            self._stream_to_call_map.pop(session.stream_sid, None)
            duration = time.time() - session.created_at
            logger.info(
                f"Ended call session: call_id={call_id}, total_turns={session.turn_count}, "
                f"duration={duration:.1f}s, emergency={session.emergency_state}"
            )
        return session

    def cleanup_stale_sessions(self, max_idle_seconds: float = 1800.0) -> int:
        """Remove sessions that have been idle longer than max_idle_seconds."""
        now = time.time()
        stale_call_ids = [
            cid for cid, sess in self._sessions.items()
            if (now - sess.last_activity) > max_idle_seconds
        ]
        for cid in stale_call_ids:
            self.end_session(cid)
        if stale_call_ids:
            logger.info(f"Cleaned up {len(stale_call_ids)} stale call sessions")
        return len(stale_call_ids)


# Singleton conversation manager instance
conversation_manager = ConversationManager()


# Words that mark a question as one only answerable for a PLACE.
#
# These have to cover every language the agent answers in, because Sarvam runs
# in transcribe mode: a Telugu caller's words come back as Telugu script, not
# as English. While this list was English-only, "regeneration in Telugu" was
# not the problem - the gate simply never fired, the question went to the agent
# with no position at all, and every tool answered about the default coast.
#
# Indic entries are STEMS, without the case endings these languages attach, so
# that "సముద్ర" still matches "సముద్రంలోకి".
LOCATION_QUESTION_TERMS = (
    # English
    "weather", "forecast", "wind", "wave", "waves", "sea", "ocean", "fishing",
    "fish", "sail", "sailing", "boat", "tide", "current", "rain", "storm",
    "safe", "safety", "condition", "temperature", "tomorrow", "today", "coast",
    # Telugu
    "వాతావరణ", "సముద్ర", "అల", "గాలి", "చేప", "సురక్షిత", "ప్రమాద", "ఆటుపోటు",
    "తుఫాన", "వర్ష", "ఈరోజు", "రేపు", "పడవ", "తీర", "వెళ్ళ", "వెళ్ల",
    # Hindi and Marathi share Devanagari
    "मौसम", "समुद्र", "लहर", "हवा", "मछली", "सुरक्षित", "ज्वार", "तूफान",
    "बारिश", "आज", "कल", "नाव", "किनार", "लाट", "वारा", "मासे", "भरती",
    "वादळ", "पाऊस", "उद्या", "होडी", "हवामान",
    # Tamil
    "வானிலை", "கடல", "கடலு", "அலை", "காற்று", "மீன", "பாதுகாப", "ஓதம",
    "புயல", "மழை", "இன்று", "நாளை", "படகு", "கரை",
    # Malayalam
    "കാലാവസ്ഥ", "കടല", "തിരമാല", "കാറ്റ", "മീന", "സുരക്ഷിത", "വേലിയേറ്റ",
    "കൊടുങ്കാറ്റ", "മഴ", "ഇന്ന", "നാളെ", "വള്ള", "തീര",
    # Kannada
    "ಹವಾಮಾನ", "ಸಮುದ್ರ", "ಅಲೆ", "ಗಾಳಿ", "ಮೀನ", "ಸುರಕ್ಷಿತ", "ಉಬ್ಬರ",
    "ಬಿರುಗಾಳಿ", "ಮಳೆ", "ಇಂದು", "ನಾಳೆ", "ದೋಣಿ", "ಕರಾವಳಿ",
    # Bengali
    "আবহাওয়া", "সমুদ্র", "ঢেউ", "বাতাস", "মাছ", "নিরাপদ", "জোয়ার", "ঝড়",
    "বৃষ্টি", "আজ", "কাল", "নৌকা", "উপকূল",
    # Gujarati
    "હવામાન", "દરિય", "મોજ", "પવન", "માછલ", "સુરક્ષિત", "સલામત", "ભરતી",
    "વાવાઝોડ", "વરસાદ", "આજે", "કાલે", "હોડી", "કિનાર",
    # Odia
    "ପାଗ", "ସମୁଦ୍ର", "ଢେଉ", "ପବନ", "ମାଛ", "ନିରାପଦ", "ଜୁଆର", "ଝଡ଼",
    "ବର୍ଷା", "ଆଜି", "କାଲି", "ଡଙ୍ଗା", "କୂଳ",
)


def needs_location_context(text: str) -> bool:
    """Does answering this need to know where the caller is?"""
    lowered = str(text or "").lower()
    return any(term in lowered for term in LOCATION_QUESTION_TERMS)


def location_in_question(text: str) -> Optional[Location]:
    """A place the caller already named inside the question itself.

    "kakinada lo weather enti" and "కాకినాడ లో వాతావరణం" both say where they
    are. Asking such a caller "which harbour are you calling from?" wastes a
    turn on a phone call and makes the agent look like it was not listening.
    """
    if resolve_port is None:
        return None
    found = resolve_port(text)
    if not found:
        return None
    return Location(
        name=str(found["name"]),
        latitude=float(found["latitude"]),
        longitude=float(found["longitude"]),
        state=str(found["state"]),
    )


# Answers that are a reply but not a place. Treated as "say it again", not as
# a location called "yes".
# A reply that is a reply but not a place. Treated as "say it again", never as
# a harbour called "yes". Multilingual for the same reason the question terms
# are: the caller answers in the language they were asked in.
_NON_ANSWERS = {
    "yes", "no", "okay", "ok", "hello", "hi", "hmm", "what", "sorry",
    "i don't know", "dont know", "don't know", "no idea",
    "అవును", "కాదు", "లేదు", "తెలియదు", "సరే", "హలో",
    "हाँ", "हां", "नहीं", "पता नहीं", "ठीक", "नमस्ते", "हो", "नाही", "माहीत नाही",
    "ஆம்", "இல்லை", "தெரியாது", "சரி", "வணக்கம்",
    "അതെ", "ഇല്ല", "അറിയില്ല", "ശരി", "നമസ്കാരം",
    "ಹೌದು", "ಇಲ್ಲ", "ಗೊತ್ತಿಲ್ಲ", "ಸರಿ", "ನಮಸ್ಕಾರ",
    "হ্যাঁ", "না", "জানি না", "ঠিক আছে", "নমস্কার",
    "હા", "ના", "ખબર નથી", "બરાબર", "નમસ્તે",
    "ହଁ", "ନା", "ଜଣା ନାହିଁ", "ଠିକ୍",
}


def location_from_reply(text: str) -> Optional[Location]:
    """A spoken reply to "where are you calling from" -> a usable Location.

    Returns a Location WITH COORDINATES when the place is one the gazetteer
    knows, because every marine tool needs a latitude and longitude and a bare
    name gets none. An unrecognised place returns None so the caller can be
    asked again - guessing the nearest spelling would answer a Kochi fisherman
    about the Bay of Bengal.
    """
    cleaned = " ".join(text.strip().split())
    if not cleaned or len(cleaned) > 100:
        return None
    if cleaned.lower() in _NON_ANSWERS:
        return None
    if len(cleaned.split()) > 8:
        return None

    if resolve_port is not None:
        found = resolve_port(cleaned)
        if not found:
            return None
        return Location(
            name=str(found["name"]),
            latitude=float(found["latitude"]),
            longitude=float(found["longitude"]),
        )

    # No gazetteer here: forward the name and let the API resolve it.
    return Location(name=cleaned)


def state_for_location(location: Optional[Location]) -> Optional[str]:
    """The coastal state a resolved place sits in, for the advisory feed."""
    if not location or not location.name or resolve_port is None:
        return None
    found = resolve_port(location.name)
    return str(found["state"]) if found else None
