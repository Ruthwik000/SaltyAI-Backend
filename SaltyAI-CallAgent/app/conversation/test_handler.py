"""
Development Test Conversation Handler for SALTY AI Call Agent.
Provides deterministic, multi-turn contextual dialog for local voice pipeline verification
without hallucinating fake marine, weather, or rescue intelligence.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Known coastal locations across Andhra Pradesh & East Coast for entity resolution
KNOWN_LOCATIONS = {
    "visakhapatnam": "Visakhapatnam",
    "vizag": "Visakhapatnam",
    "విశాఖపట్నం": "విశాఖపట్నం (Visakhapatnam)",
    "విశాఖ": "విశాఖపట్నం (Visakhapatnam)",
    "kakinada": "Kakinada",
    "కాకినాడ": "కాకినాడ (Kakinada)",
    "machilipatnam": "Machilipatnam",
    "మచిలీపట్నం": "మచిలీపట్నం (Machilipatnam)",
    "bhavanapadu": "Bhavanapadu",
    "భవనపాడు": "భవనపాడు (Bhavanapadu)",
    "nizampatnam": "Nizampatnam",
    "నిజాంపట్నం": "నిజాంపట్నం (Nizampatnam)",
    "krishnapatnam": "Krishnapatnam",
    "కృష్ణపట్నం": "కృష్ణపట్నం (Krishnapatnam)",
    "chennai": "Chennai",
    "చెన్నై": "చెన్నై (Chennai)",
    "rameswaram": "Rameswaram",
    "రామేశ్వరం": "రామేశ్వరం (Rameswaram)",
}

# Time entities
TIME_ENTITIES = {
    "tomorrow": "tomorrow",
    "రేపు": "రేపు (tomorrow)",
    "morning": "morning",
    "ఉదయం": "ఉదయం (morning)",
    "evening": "evening",
    "సాయంత్రం": "సాయంత్రం (evening)",
    "afternoon": "afternoon",
    "మధ్యాహ్నం": "మధ్యాహ్నం (afternoon)",
    "night": "night",
    "రాత్రి": "రాత్రి (night)",
    "today": "today",
    "ఈరోజు": "ఈరోజు (today)",
}


def is_telugu_text(text: str) -> bool:
    """Check if the text contains Telugu Unicode characters."""
    return bool(re.search(r"[\u0C00-\u0C7F]", text))


def extract_entities_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract location and time references from a single message."""
    lower = text.lower()
    loc = None
    time_ref = None

    for key, val in KNOWN_LOCATIONS.items():
        if key in lower or key in text:
            loc = val
            break

    for key, val in TIME_ENTITIES.items():
        if key in lower or key in text:
            time_ref = val
            break

    return loc, time_ref


def extract_context_from_history(
    current_message: str,
    history: List[Dict[str, Any]]
) -> Dict[str, Optional[str]]:
    """
    Extract accumulated entities (location, time, topic) across all turns of the conversation.
    """
    context = {"location": None, "time": None, "topic": None}

    # First parse through history chronologically
    for turn in history:
        content = turn.get("content", "")
        loc, t_ref = extract_entities_from_text(content)
        if loc:
            context["location"] = loc
        if t_ref:
            context["time"] = t_ref

    # Then update with current message
    cur_loc, cur_time = extract_entities_from_text(current_message)
    if cur_loc:
        context["location"] = cur_loc
    if cur_time:
        context["time"] = cur_time

    return context


def generate_development_response(
    message: str,
    conversation_history: List[Dict[str, Any]],
    current_language: str = "te-IN",
) -> Tuple[str, str]:
    """
    Generate deterministic, contextual development responses for voice testing.
    Never hallucinates fake marine/weather data.

    Returns:
        (response_text: str, response_language: str)
    """
    clean_msg = message.strip()
    msg_lower = clean_msg.lower()

    # Determine caller's spoken language for this turn
    if is_telugu_text(clean_msg):
        lang = "te-IN"
    elif any(word in msg_lower for word in ["hello", "hi", "what", "where", "how", "is", "can", "fishing", "boat", "sea"]):
        lang = "en-IN"
    else:
        lang = current_language or "te-IN"

    # Extract accumulated multi-turn context
    context = extract_context_from_history(clean_msg, conversation_history)
    loc = context["location"]
    time_ref = context["time"]

    # --------------------------------------------------------------------------
    # 1. Emergency Intent
    # --------------------------------------------------------------------------
    if any(k in clean_msg for k in ["ఆగిపోయింది", "మునిగిపోతుంది", "కాపాడండి", "సహాయం", "ప్రమాదం"]) or \
       any(k in msg_lower for k in ["sinking", "engine failed", "stuck in the sea", "help", "emergency", "mayday"]):
        if lang == "te-IN":
            return (
                "అత్యవసర పరిస్థితి గుర్తించబడింది. ఇది డెవలప్‌మెంట్ టెస్ట్ మోడ్. "
                "లైవ్ సిస్టమ్‌లో కోస్టల్ రెస్క్యూ టీమ్‌కు అలర్ట్ పంపబడుతుంది.",
                "te-IN"
            )
        else:
            return (
                "Emergency intent detected. This is development test mode. "
                "In live deployment, an alert is forwarded to Coastal Rescue Operations.",
                "en-IN"
            )

    # --------------------------------------------------------------------------
    # 2. Greetings
    # --------------------------------------------------------------------------
    greeting_patterns_en = ["hello", "hi", "hey", "good morning", "good evening"]
    greeting_patterns_te = ["నమస్కారం", "నమస్తే", "హలో", "బాగున్నారా"]
    if any(msg_lower == g or msg_lower.startswith(g + " ") for g in greeting_patterns_en) or \
       any(g in clean_msg for g in greeting_patterns_te):
        if lang == "te-IN":
            return ("నమస్కారం! ఇది సాల్టీ ఏఐ వాయిస్ అసిస్టెంట్. నేను మీకు ఎలా సహాయపడగలను?", "te-IN")
        else:
            return ("Hello! This is SALTY AI. How can I help you today?", "en-IN")

    # --------------------------------------------------------------------------
    # 3. Intent: Want to go fishing / Planning
    # --------------------------------------------------------------------------
    fishing_patterns = ["go fishing", "want to fish", "planning to fish", "చేపల వేట", "వేటకు వెళ్ళ", "వేటకు వెళ్ల"]
    if any(p in msg_lower or p in clean_msg for p in fishing_patterns) and not loc:
        if lang == "te-IN":
            return ("ఖచ్చితంగా. మీరు ఏ ప్రాంతం లేదా తీరం నుండి చేపల వేటకు వెళ్లాలనుకుంటున్నారు?", "te-IN")
        else:
            return ("Sure. Which area or port are you planning to fish from?", "en-IN")

    # --------------------------------------------------------------------------
    # 4. Location Provided (e.g. "Visakhapatnam", "Vizag.", "కాకినాడ")
    # --------------------------------------------------------------------------
    clean_nopunct = re.sub(r"[^\w\s\u0C00-\u0C7F]", "", msg_lower).strip()
    is_location_response = any(k in clean_nopunct or k == msg_lower for k in KNOWN_LOCATIONS)
    if is_location_response and not any(w in msg_lower for w in ["sea", "weather", "condition", "pfz", "wave", "wind"]):
        loc_name = loc or KNOWN_LOCATIONS.get(clean_nopunct) or clean_msg
        if lang == "te-IN":
            return (f"సరే, {loc_name}. మీరు సముద్ర పరిస్థితి లేదా చేపల వేట జోన్ గురించి తెలుసుకోవాలనుకుంటున్నారా?", "te-IN")
        else:
            return (f"Okay, {loc_name}. What would you like to know regarding sea conditions or fishing zones?", "en-IN")


    # --------------------------------------------------------------------------
    # 5. Intent: Sea Condition / Weather / Waves / Wind
    # --------------------------------------------------------------------------
    sea_patterns = ["sea condition", "weather", "wave", "wind", "rough", "సముద్ర పరిస్థితి", "వాతావరణం", "అలలు", "గాలి"]
    if any(p in msg_lower or p in clean_msg for p in sea_patterns):
        loc_str = f" in {loc}" if loc else ""
        time_str = f" for {time_ref}" if time_ref else ""
        
        loc_str_te = f" {loc} వద్ద" if loc else ""
        time_str_te = f" {time_ref}" if time_ref else ""

        if lang == "te-IN":
            return (
                f"సరే{loc_str_te}{time_str_te} సముద్ర సమాచారం. "
                "లైవ్ సముద్ర మరియు వాతావరణ సమాచారం మెయిన్ సాల్టీ ఏఐ ఇంటెలిజెన్స్ సిస్టమ్ కనెక్ట్ అయిన తర్వాత అందుతుంది. "
                "ప్రస్తుతం నేను మీతో వాయిస్ సంభాషణను పరీక్షిస్తున్నాను.",
                "te-IN"
            )
        else:
            return (
                f"Understood{loc_str}{time_str}. "
                "Real-time sea conditions and weather intelligence will come from the main SALTY AI backend once connected. "
                "Currently, I am testing voice conversation with you.",
                "en-IN"
            )

    # --------------------------------------------------------------------------
    # 6. Intent: PFZ (Potential Fishing Zone)
    # --------------------------------------------------------------------------
    pfz_patterns = ["pfz", "fishing zone", "fish zone", "చేపలు ఎక్కడ", "చేపల జోన్"]
    if any(p in msg_lower or p in clean_msg for p in pfz_patterns):
        if lang == "te-IN":
            return (
                "పొటెన్షియల్ ఫిషింగ్ జోన్ సమాచారం మెయిన్ సాల్టీ ఏఐ ఓషన్ అనలిటిక్స్ ద్వారా అందించబడుతుంది. "
                "ప్రస్తుతం వాయిస్ కమ్యూనికేషన్ సరిగ్గా పనిచేస్తోంది.",
                "te-IN"
            )
        else:
            return (
                "Potential Fishing Zone data will be provided by the main SALTY AI ocean analytics agent. "
                "Voice communication is functioning properly.",
                "en-IN"
            )

    # --------------------------------------------------------------------------
    # 7. Follow-up: Timing ("what about evening?", "సాయంత్రం సంగతేంటి?")
    # --------------------------------------------------------------------------
    timing_followups = ["what about", "how about", "evening", "morning", "సాయంత్రం", "ఉదయం", "మధ్యాహ్నం"]
    if any(t in msg_lower or t in clean_msg for t in timing_followups):
        t_name = time_ref or "that time"
        l_name = f" in {loc}" if loc else ""
        t_name_te = time_ref or "ఆ సమయానికి"
        l_name_te = f" {loc} లో" if loc else ""

        if lang == "te-IN":
            return (
                f"{l_name_te} {t_name_te} సముద్ర వివరాలు... "
                "మెయిన్ బ్యాకెండ్ కనెక్ట్ అయినప్పుడు ఖచ్చితమైన సమాచారం అందుతుంది. మీరు ఇంకేమైనా తెలుసుకోవాలనుకుంటున్నారా?",
                "te-IN"
            )
        else:
            return (
                f"Regarding {t_name}{l_name}... "
                "Exact marine forecasts will be provided when the main backend is live. Is there anything else you'd like to test?",
                "en-IN"
            )

    # --------------------------------------------------------------------------
    # 8. General / Fallback Conversational Response
    # --------------------------------------------------------------------------
    if lang == "te-IN":
        return (
            "మీరు చెప్పినది అర్థమైంది. లైవ్ సాల్టీ ఏఐ బ్యాకెండ్ కనెక్ట్ అయినప్పుడు ఖచ్చితమైన సముద్ర సమాచారం అందుతుంది. "
            "నేను మీకు ఇంకేమైనా సహాయం చేయగలనా?",
            "te-IN"
        )
    else:
        return (
            "I understood your query. When the live SALTY AI backend is connected, detailed marine answers will be provided. "
            "How else can I assist your test call?",
            "en-IN"
        )
