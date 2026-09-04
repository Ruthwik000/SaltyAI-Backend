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
