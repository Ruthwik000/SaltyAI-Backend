# -*- coding: utf-8 -*-
"""Spoken prompts the agent needs to ask the caller, in the caller's language.

Anything the agent needs FROM the caller is asked for on the call itself, in
the language the caller is already speaking. Asking a Telugu fisherman "which
coastal city are you calling from?" in English - which is what this used to do,
with the language code hard-coded to en-IN - is the same as not asking at all.

These are short on purpose. They are synthesised into speech and heard once,
over an engine, on a handset.
"""

from typing import Dict

# Prompt key -> language code -> spoken text.
PROMPTS: Dict[str, Dict[str, str]] = {
    # First time: we need a position before any marine tool can answer.
    "ask_location": {
        "en-IN": "Which coastal town or fishing harbour are you calling from?",
        "te-IN": "మీరు ఏ తీర ప్రాంతం లేదా చేపల రేవు నుండి మాట్లాడుతున్నారు?",
        "hi-IN": "आप किस तटीय शहर या मछली बंदरगाह से बोल रहे हैं?",
        "ta-IN": "நீங்கள் எந்த கடலோர ஊரிலிருந்து அல்லது மீன்பிடி துறைமுகத்திலிருந்து பேசுகிறீர்கள்?",
        "ml-IN": "നിങ്ങൾ ഏത് തീരദേശ പട്ടണത്തിൽ നിന്നാണ്, അല്ലെങ്കിൽ ഏത് മത്സ്യബന്ധന തുറമുഖത്തിൽ നിന്നാണ് വിളിക്കുന്നത്?",
        "kn-IN": "ನೀವು ಯಾವ ಕರಾವಳಿ ಊರಿನಿಂದ ಅಥವಾ ಮೀನುಗಾರಿಕೆ ಬಂದರಿನಿಂದ ಮಾತನಾಡುತ್ತಿದ್ದೀರಿ?",
        "bn-IN": "আপনি কোন উপকূলীয় শহর বা মাছ ধরার বন্দর থেকে বলছেন?",
        "mr-IN": "तुम्ही कोणत्या किनारी गावातून किंवा मासेमारी बंदरातून बोलत आहात?",
        "gu-IN": "તમે કયા દરિયાકાંઠાના ગામ કે માછીમારી બંદરથી બોલો છો?",
        "or-IN": "ଆପଣ କେଉଁ ଉପକୂଳ ସହର କିମ୍ବା ମତ୍ସ୍ୟ ବନ୍ଦରରୁ କଥା ହେଉଛନ୍ତି?",
    },
    # They answered, but nothing in it looked like a place.
    "retry_location": {
        "en-IN": "I did not catch the place. Please say just the name of your nearest fishing harbour.",
        "te-IN": "ఆ ప్రాంతం నాకు వినిపించలేదు. మీ దగ్గరి చేపల రేవు పేరు మాత్రమే చెప్పండి.",
        "hi-IN": "जगह समझ नहीं आई। कृपया अपने सबसे नज़दीकी मछली बंदरगाह का नाम बताइए।",
        "ta-IN": "இடம் புரியவில்லை. உங்கள் அருகிலுள்ள மீன்பிடி துறைமுகத்தின் பெயரை மட்டும் சொல்லுங்கள்.",
        "ml-IN": "സ്ഥലം മനസ്സിലായില്ല. അടുത്തുള്ള മത്സ്യബന്ധന തുറമുഖത്തിന്റെ പേര് മാത്രം പറയുക.",
        "kn-IN": "ಸ್ಥಳ ಅರ್ಥವಾಗಲಿಲ್ಲ. ನಿಮ್ಮ ಹತ್ತಿರದ ಮೀನುಗಾರಿಕೆ ಬಂದರಿನ ಹೆಸರು ಮಾತ್ರ ಹೇಳಿ.",
        "bn-IN": "জায়গাটা বুঝতে পারিনি। আপনার নিকটতম মাছ ধরার বন্দরের নাম বলুন।",
        "mr-IN": "ठिकाण समजले नाही. तुमच्या जवळच्या मासेमारी बंदराचे नाव सांगा.",
        "gu-IN": "સ્થળ સમજાયું નહીં. તમારા નજીકના માછીમારી બંદરનું નામ કહો.",
        "or-IN": "ସ୍ଥାନଟି ବୁଝିପାରିଲି ନାହିଁ। ଆପଣଙ୍କ ନିକଟସ୍ଥ ମତ୍ସ୍ୟ ବନ୍ଦରର ନାମ କୁହନ୍ତୁ।",
    },
    # They named somewhere, but it is not a coast this can place.
    "unknown_place": {
        "en-IN": "I do not know that place. Please say a bigger fishing harbour near you.",
        "te-IN": "ఆ ప్రాంతం నాకు తెలియదు. మీ దగ్గరలోని పెద్ద చేపల రేవు పేరు చెప్పండి.",
        "hi-IN": "यह जगह मुझे नहीं पता। कृपया अपने पास का कोई बड़ा मछली बंदरगाह बताइए।",
        "ta-IN": "அந்த இடம் எனக்குத் தெரியாது. உங்கள் அருகில் உள்ள பெரிய மீன்பிடி துறைமுகத்தைச் சொல்லுங்கள்.",
        "ml-IN": "ആ സ്ഥലം എനിക്ക് അറിയില്ല. അടുത്തുള്ള വലിയ മത്സ്യബന്ധന തുറമുഖം പറയുക.",
        "kn-IN": "ಆ ಸ್ಥಳ ನನಗೆ ಗೊತ್ತಿಲ್ಲ. ನಿಮ್ಮ ಹತ್ತಿರದ ದೊಡ್ಡ ಮೀನುಗಾರಿಕೆ ಬಂದರು ಹೇಳಿ.",
        "bn-IN": "এই জায়গাটা আমি জানি না। আপনার কাছের বড় মাছ ধরার বন্দরের নাম বলুন।",
        "mr-IN": "ते ठिकाण मला माहीत नाही. तुमच्या जवळचे मोठे मासेमारी बंदर सांगा.",
        "gu-IN": "એ સ્થળ મને ખબર નથી. તમારી નજીકનું મોટું માછીમારી બંદર કહો.",
        "or-IN": "ସେହି ସ୍ଥାନ ମୋତେ ଜଣା ନାହିଁ। ଆପଣଙ୍କ ନିକଟରେ ଥିବା ବଡ଼ ମତ୍ସ୍ୟ ବନ୍ଦର କୁହନ୍ତୁ।",
    },
}

# Spoken the moment the call connects, before anyone has said anything - so
# there is no detected language yet and it goes out in the deployment's
# DEFAULT_FALLBACK_LANGUAGE. An Andhra deployment sets that to te-IN and the
# line answers in Telugu, which is the point.
PROMPTS["greeting"] = {
    "en-IN": "Hello, this is SALTY, the sea information line. Ask me about the sea, the weather, or where to fish.",
    "te-IN": "నమస్కారం, ఇది సాల్టీ సముద్ర సమాచార లైన్. సముద్రం, వాతావరణం, లేదా చేపలు ఎక్కడ దొరుకుతాయో అడగండి.",
    "hi-IN": "नमस्ते, यह साल्टी समुद्र जानकारी लाइन है। समुद्र, मौसम, या मछली कहाँ मिलेगी, पूछिए।",
    "ta-IN": "வணக்கம், இது சால்டி கடல் தகவல் இணைப்பு. கடல், வானிலை, அல்லது மீன் எங்கே கிடைக்கும் என்று கேளுங்கள்.",
    "ml-IN": "നമസ്കാരം, ഇത് സാൾട്ടി കടൽ വിവര ലൈൻ. കടൽ, കാലാവസ്ഥ, അല്ലെങ്കിൽ മീൻ എവിടെ കിട്ടും എന്ന് ചോദിക്കുക.",
    "kn-IN": "ನಮಸ್ಕಾರ, ಇದು ಸಾಲ್ಟಿ ಸಮುದ್ರ ಮಾಹಿತಿ ಸಂಪರ್ಕ. ಸಮುದ್ರ, ಹವಾಮಾನ, ಅಥವಾ ಮೀನು ಎಲ್ಲಿ ಸಿಗುತ್ತದೆ ಎಂದು ಕೇಳಿ.",
    "bn-IN": "নমস্কার, এটি সাল্টি সমুদ্র তথ্য লাইন। সমুদ্র, আবহাওয়া, বা মাছ কোথায় পাওয়া যাবে জিজ্ঞাসা করুন।",
    "mr-IN": "नमस्कार, ही साल्टी समुद्र माहिती लाइन आहे. समुद्र, हवामान, किंवा मासे कुठे मिळतील ते विचारा.",
    "gu-IN": "નમસ્તે, આ સાલ્ટી દરિયા માહિતી લાઇન છે. દરિયો, હવામાન, કે માછલી ક્યાં મળશે તે પૂછો.",
    "or-IN": "ନମସ୍କାର, ଏହା ସାଲ୍ଟି ସମୁଦ୍ର ସୂଚନା ଲାଇନ। ସମୁଦ୍ର, ପାଗ, କିମ୍ବା ମାଛ କେଉଁଠି ମିଳିବ ପଚାରନ୍ତୁ।",
}

DEFAULT_LANGUAGE = "en-IN"


def spoken(key: str, language: str) -> str:
    """The prompt for this language, falling back by base language then English.

    A caller on "te" or "te-IN" gets Telugu either way; an unknown code gets
    English rather than nothing, because a prompt the caller cannot understand
    is still better than silence on a live call.
    """
    table = PROMPTS.get(key)
    if not table:
        return ""
    code = str(language or "").strip()
    if code in table:
        return table[code]
    base = code.replace("_", "-").split("-")[0].lower()
    for candidate, text in table.items():
        if candidate.split("-")[0].lower() == base:
            return text
    return table[DEFAULT_LANGUAGE]
