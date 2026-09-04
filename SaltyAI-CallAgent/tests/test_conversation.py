"""
Tests for Conversation and Session Manager.
"""

from app.conversation.manager import ConversationManager, CallSession
from app.models.schemas import Location


def test_conversation_session_lifecycle():
    """Verify session creation, turn recording, and termination."""
    mgr = ConversationManager()

    session = mgr.create_session(
        call_id="call-lifecycle-1",
        stream_sid="stream-sid-1",
        phone_number="+919876543210",
        initial_language="ta-IN",
    )

    assert session.call_id == "call-lifecycle-1"
    assert session.language == "ta-IN"
    assert session.turn_count == 0

    # User speaks first turn
    session.add_user_message("Can I go fishing tomorrow?", detected_language="en-IN")
    assert session.turn_count == 1
    assert session.language == "en-IN"  # Language updated to English

    # Assistant responds
    session.add_assistant_message("Tomorrow morning is moderate.")

    # User speaks second turn (follow-up referring to previous context)
    session.add_user_message("What about evening?")
    assert session.turn_count == 2
    session.add_assistant_message("Evening has high waves. Avoid after 4 PM.")

    history = session.get_history_payload()
    assert len(history) == 4
    assert history[0]["content"] == "Can I go fishing tomorrow?"
    assert history[2]["content"] == "What about evening?"

    # Update location
    mgr.update_location("call-lifecycle-1", Location(name="Chennai Harbour", latitude=13.08, longitude=80.29))
    assert session.location.name == "Chennai Harbour"

    # End session
    ended = mgr.end_session("call-lifecycle-1")
    assert ended.call_id == "call-lifecycle-1"
    assert mgr.get_session("call-lifecycle-1") is None
    assert mgr.get_session_by_stream("stream-sid-1") is None


def test_conversation_history_windowing():
    """Verify conversation history is bounded to avoid memory leaks."""
    session = CallSession(
        call_id="call-overflow",
        stream_sid="stream-overflow",
        phone_number="+919876543210",
    )

    # Add 25 turns (50 messages)
    for i in range(25):
        session.add_user_message(f"User message {i}")
        session.add_assistant_message(f"Assistant response {i}")

    # Maximum history is 10 turns (20 messages)
    assert len(session.conversation_history) == 20
    assert session.conversation_history[-1]["content"] == "Assistant response 24"
    assert session.conversation_history[-2]["content"] == "User message 24"
