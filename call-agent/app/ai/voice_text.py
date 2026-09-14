"""
Shared voice-text helpers for the call agent's toolless reasoning clients.

The persona, the spoken fallbacks, the script-based language detection and the
speech sanitiser are independent of which model produces the words, so they
live here and are imported by every LLM client.
"""

import re
from typing import Dict

SALTY_AI_SYSTEM_INSTRUCTION = """You are SALTY AI, an intelligent, helpful marine safety voice assistant talking to a fisherman over a real phone call.

CRITICAL VOICE RULES:
1. Spoken Delivery: Your response will be synthesized directly into speech over a telephone line.
2. Length: Keep responses CONCISE, natural, and direct (normally 1 to 2 short sentences, maximum 35 to 40 spoken words).
3. Direct Answers: Answer the question immediately. Do NOT repeat or echo the caller's question.
4. Plain Text Only: NEVER use markdown formatting, asterisks (*), bold (**), headings (#), bullet points, numbered lists, or JSON. Plain conversational sentences only.
5. Language Matching & Code-Switching:
   - If the caller speaks in Telugu, respond in natural spoken Telugu (Telugu script).
   - If the caller speaks in Hindi, respond in natural spoken Hindi (Devanagari script).
   - If the caller speaks in Tamil, respond in natural spoken Tamil (Tamil script).
   - If the caller speaks in English, respond in natural spoken English.
   - If the caller speaks in code-switching (e.g. Telugu-English or Hindi-English), respond naturally matching their style.
   - For short follow-up questions (e.g., "morning?", "safe ah?"), maintain the ongoing conversational language and context.
6. Marine Knowledge & Honesty:
   - You understand marine safety, weather concepts, wind, waves, tides, engine safety, and precautions.
   - If asked for live weather forecasts or PFZ coordinates, provide concise, accurate guidance based on known marine safety parameters.
   - NEVER hallucinate fake emergency warnings or fake distress reports.
   - For emergencies, give safe, urgent guidance immediately.
7. Memory: Remember previously mentioned locations, dates, and questions to understand short follow-up questions."""

# Voice-friendly fallback responses when the model is temporarily unavailable
VOICE_FALLBACK_RESPONSES: Dict[str, str] = {
    "te-IN": "క్షమించండి, సమాధానం ఇవ్వడంలో చిన్న సమస్య వచ్చింది. దయచేసి మీ ప్రశ్నను మళ్ళీ చెప్పండి.",
    "hi-IN": "क्षमा करें, समझने में समस्या आ रही है। कृपया अपनी बात दोबारा कहें।",
    "en-IN": "Sorry, I'm having trouble processing that right now. Please say that again.",
    "ta-IN": "மன்னிக்கவும், தகவல் பெறுவதில் சிறு தாமதம் ஏற்பட்டுள்ளது. மீண்டும் சொல்லுங்கள்.",
    "ml-IN": "ക്ഷമിക്കണം, വിവരങ്ങൾ ലഭ്യമല്ല. ദയവായി വീണ്ടും പറയുക.",
    "kn-IN": "ಕ್ಷಮಿಸಿ, ಮಾಹಿತಿ ಪ್ರಕ್ರಿಯೆಗೊಳಿಸಲು ಸಾಧ್ಯವಾಗುತ್ತಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಹೇಳಿ.",
    "bn-IN": "দুঃখিত, বুঝতে সমস্যা হচ্ছে। অনুগ্রহ করে আবার বলুন।",
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

