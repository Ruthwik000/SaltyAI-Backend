"""Indian coastal places, so a spoken place name becomes a position.

Why this exists
---------------
The phone agent asks the caller "which coastal city or fishing village are you
calling from?" and captures whatever they say as a NAME. Every marine tool
needs a latitude and longitude, so a name on its own resolved to nothing and
the whole toolset answered "no position available" - on a call where the
fisherman had just told it exactly where he was.

This is a lookup table of harbours and coastal towns, not a geocoder. It does
not guess: a name it does not recognise returns None, and the agent says it
could not place the caller rather than answering about the wrong coast. That
matters more here than coverage - Kochi and Kakinada are on opposite sides of
the subcontinent, and an answer for one is dangerous for the other.

Coordinates are harbour centroids to two decimals, which is a few hundred
metres. That is well inside the resolution of the INCOIS forecast grid (about
0.1 degrees, roughly eleven kilometres), and point_conditions steps seaward
from a land cell anyway, so a town-centre coordinate still reads real water.
"""

from __future__ import annotations

import re
import unicodedata

# name -> (latitude, longitude, coastal state)
PORTS: dict[str, tuple[float, float, str]] = {
    # --- East coast: Bay of Bengal ---
    "visakhapatnam": (17.69, 83.22, "Andhra Pradesh"),
    "bheemunipatnam": (17.89, 83.45, "Andhra Pradesh"),
    "srikakulam": (18.30, 83.90, "Andhra Pradesh"),
    "vizianagaram": (18.11, 83.40, "Andhra Pradesh"),
    "kakinada": (16.99, 82.25, "Andhra Pradesh"),
    "machilipatnam": (16.17, 81.14, "Andhra Pradesh"),
    "narsapur": (16.43, 81.70, "Andhra Pradesh"),
    "ongole": (15.51, 80.05, "Andhra Pradesh"),
    "nellore": (14.45, 79.99, "Andhra Pradesh"),
    "krishnapatnam": (14.28, 80.12, "Andhra Pradesh"),
    "chennai": (13.08, 80.27, "Tamil Nadu"),
    "kasimedu": (13.13, 80.30, "Tamil Nadu"),
    "mahabalipuram": (12.62, 80.19, "Tamil Nadu"),
    "puducherry": (11.93, 79.83, "Puducherry"),
    "cuddalore": (11.75, 79.77, "Tamil Nadu"),
    "karaikal": (10.92, 79.84, "Puducherry"),
    "nagapattinam": (10.77, 79.84, "Tamil Nadu"),
    "rameswaram": (9.29, 79.31, "Tamil Nadu"),
    "thoothukudi": (8.76, 78.13, "Tamil Nadu"),
    "tiruchendur": (8.50, 78.12, "Tamil Nadu"),
    "kanyakumari": (8.08, 77.55, "Tamil Nadu"),
    "paradip": (20.32, 86.61, "Odisha"),
    "puri": (19.81, 85.83, "Odisha"),
    "konark": (19.89, 86.09, "Odisha"),
    "gopalpur": (19.26, 84.90, "Odisha"),
    "balasore": (21.49, 86.93, "Odisha"),
    "dhamra": (20.79, 86.97, "Odisha"),
    "digha": (21.63, 87.51, "West Bengal"),
    "haldia": (22.03, 88.09, "West Bengal"),
    "kolkata": (22.57, 88.36, "West Bengal"),
    "diamond harbour": (22.19, 88.19, "West Bengal"),
    "port blair": (11.62, 92.73, "Andaman and Nicobar Islands"),

    # --- West coast: Arabian Sea ---
    "thiruvananthapuram": (8.52, 76.94, "Kerala"),
    "vizhinjam": (8.38, 76.99, "Kerala"),
    "kollam": (8.89, 76.61, "Kerala"),
    "alappuzha": (9.50, 76.34, "Kerala"),
    "kochi": (9.93, 76.27, "Kerala"),
    "munambam": (10.18, 76.17, "Kerala"),
    "thrissur": (10.52, 76.21, "Kerala"),
    "ponnani": (10.77, 75.92, "Kerala"),
    "beypore": (11.17, 75.81, "Kerala"),
    "kozhikode": (11.25, 75.78, "Kerala"),
    "kannur": (11.87, 75.37, "Kerala"),
    "kasaragod": (12.50, 74.99, "Kerala"),
    "mangaluru": (12.87, 74.84, "Karnataka"),
    "udupi": (13.34, 74.75, "Karnataka"),
    "malpe": (13.35, 74.70, "Karnataka"),
    "bhatkal": (13.99, 74.56, "Karnataka"),
    "karwar": (14.81, 74.13, "Karnataka"),
    "panaji": (15.50, 73.83, "Goa"),
    "vasco da gama": (15.40, 73.81, "Goa"),
    "malvan": (16.06, 73.46, "Maharashtra"),
    "ratnagiri": (16.99, 73.31, "Maharashtra"),
    "alibag": (18.64, 72.87, "Maharashtra"),
    "mumbai": (18.94, 72.83, "Maharashtra"),
    "thane": (19.22, 72.98, "Maharashtra"),
    "daman": (20.40, 72.83, "Daman and Diu"),
    "valsad": (20.61, 72.93, "Gujarat"),
    "surat": (21.17, 72.83, "Gujarat"),
    "bhavnagar": (21.76, 72.15, "Gujarat"),
    "diu": (20.71, 70.98, "Daman and Diu"),
    "veraval": (20.90, 70.37, "Gujarat"),
    "porbandar": (21.64, 69.61, "Gujarat"),
    "dwarka": (22.24, 68.97, "Gujarat"),
    "okha": (22.47, 69.07, "Gujarat"),
    "mandvi": (22.83, 69.35, "Gujarat"),
    "jakhau": (23.22, 68.72, "Gujarat"),
    "kandla": (23.03, 70.22, "Gujarat"),
    "kavaratti": (10.57, 72.64, "Lakshadweep"),
}

# What people actually say. Older names, spellings a speech engine produces,
# and the district a harbour sits in.
ALIASES: dict[str, str] = {
    "vizag": "visakhapatnam", "vishakhapatnam": "visakhapatnam",
    "vishakapatnam": "visakhapatnam", "waltair": "visakhapatnam",
    "madras": "chennai", "royapuram": "chennai",
    "bombay": "mumbai", "bombay port": "mumbai", "sassoon dock": "mumbai",
    "cochin": "kochi", "ernakulam": "kochi", "fort kochi": "kochi",
    "calicut": "kozhikode",
    "trivandrum": "thiruvananthapuram",
    "quilon": "kollam",
    "alleppey": "alappuzha",
    "trichur": "thrissur",
    "tuticorin": "thoothukudi",
    "mangalore": "mangaluru",
    "pondicherry": "puducherry", "pondy": "puducherry",
    "goa": "panaji", "panjim": "panaji", "mormugao": "vasco da gama",
    "vasco": "vasco da gama",
    "cannanore": "kannur",
    "baleshwar": "balasore",
    "calcutta": "kolkata",
    "andaman": "port blair", "nicobar": "port blair",
    "lakshadweep": "kavaratti",
    "bapatla": "ongole", "chirala": "ongole",
    "kalingapatnam": "srikakulam",
    "antarvedi": "narsapur",
    "colachel": "kanyakumari", "nagercoil": "kanyakumari",
    "muttom": "kanyakumari",
    "tarapur": "mumbai", "vasai": "mumbai", "versova": "mumbai",
    "harnai": "ratnagiri", "dapoli": "ratnagiri",
    "vengurla": "malvan", "sindhudurg": "malvan",
    "honnavar": "bhatkal", "kumta": "karwar",
    "gangolli": "udupi", "kaup": "udupi",
    "azhikkal": "kannur", "thalassery": "kannur",
    "chombala": "kozhikode", "koyilandy": "kozhikode",
    "chellanam": "kochi", "vypin": "kochi",
    "neendakara": "kollam", "sakthikulangara": "kollam",
    "poonthura": "thiruvananthapuram", "anchuthengu": "thiruvananthapuram",
    "jaffrabad": "veraval", "mangrol": "veraval", "sutrapada": "veraval",
    "navabandar": "porbandar", "madhvad": "porbandar",
    "salaya": "okha", "positra": "okha",
    "gandhidham": "kandla",
    "sunderbans": "diamond harbour", "sundarbans": "diamond harbour",
    "kakdwip": "diamond harbour", "namkhana": "diamond harbour",
    "frazerganj": "diamond harbour",
    "shankarpur": "digha", "junput": "digha",
    "astaranga": "puri", "chandrabhaga": "konark",
    "chilika": "gopalpur", "rushikulya": "gopalpur",
    "chandipur": "balasore", "talsari": "balasore",
    "kasimedu harbour": "chennai", "ennore": "chennai",
    "pulicat": "chennai", "kovalam": "chennai",
    "mudasalodai": "cuddalore", "parangipettai": "cuddalore",
    "kodiakarai": "nagapattinam", "vedaranyam": "nagapattinam",
    "pamban": "rameswaram", "mandapam": "rameswaram",
    "thangachimadam": "rameswaram",
    "therespuram": "thoothukudi", "punnakayal": "thoothukudi",
}

# The biggest harbours in the scripts a caller's speech-to-text may return.
# Not exhaustive by design: a name that does not match is reported as
# unrecognised, never mapped to "somewhere near enough".
NATIVE_NAMES: dict[str, str] = {
    "విశాఖపట్నం": "visakhapatnam", "విశాఖ": "visakhapatnam",
    "కాకినాడ": "kakinada", "మచిలీపట్నం": "machilipatnam",
    "నెల్లూరు": "nellore", "శ్రీకాకుళం": "srikakulam", "ఒంగోలు": "ongole",
    "భీమునిపట్నం": "bheemunipatnam", "చెన్నై": "chennai", "కృష్ణపట్నం": "krishnapatnam",
    "சென்னை": "chennai", "தூத்துக்குடி": "thoothukudi",
    "இராமேஸ்வரம்": "rameswaram", "ராமேஸ்வரம்": "rameswaram",
    "நாகப்பட்டினம்": "nagapattinam", "கன்னியாகுமரி": "kanyakumari",
    "கடலூர்": "cuddalore", "புதுச்சேரி": "puducherry",
    "நாகர்கோவில்": "kanyakumari", "மும்பை": "mumbai",
    "കൊച്ചി": "kochi", "കോഴിക്കോട്": "kozhikode", "കൊല്ലം": "kollam",
    "ആലപ്പുഴ": "alappuzha", "കണ്ണൂർ": "kannur",
    "തിരുവനന്തപുരം": "thiruvananthapuram", "മുംബൈ": "mumbai",
    "ബേപ്പൂർ": "beypore", "മുനമ്പം": "munambam",
    "ಮಂಗಳೂರು": "mangaluru", "ಉಡುಪಿ": "udupi", "ಕಾರವಾರ": "karwar",
    "ಮಲ್ಪೆ": "malpe", "ಭಟ್ಕಳ": "bhatkal", "ಮುಂಬೈ": "mumbai",
    "मुंबई": "mumbai", "रत्नागिरी": "ratnagiri", "अलिबाग": "alibag",
    "मालवण": "malvan", "चेन्नई": "chennai", "कोलकाता": "kolkata",
    "विशाखापट्टणम": "visakhapatnam", "कोच्चि": "kochi",
    "મુંબઈ": "mumbai", "કોચી": "kochi", "વેરાવળ": "veraval",
    "પોરબંદર": "porbandar", "ઓખા": "okha", "માંડવી": "mandvi",
    "સુરત": "surat", "દ્વારકા": "dwarka", "ભાવનગર": "bhavnagar",
    "દીવ": "diu", "કંડલા": "kandla", "જખૌ": "jakhau",
    "কলকাতা": "kolkata", "দীঘা": "digha", "হলদিয়া": "haldia",
    "কাকদ্বীপ": "diamond harbour", "ডায়মন্ড হারবার": "diamond harbour",
    "ପାରାଦୀପ": "paradip", "ପୁରୀ": "puri", "ଗୋପାଳପୁର": "gopalpur",
    "ବାଲେଶ୍ୱର": "balasore", "ଧାମରା": "dhamra",
}

# Words a caller wraps a place name in, which must not block a match.
_NOISE = re.compile(
    r"\b(?:i am|i'm|im|from|near|at|in|the|coast|of|port|harbour|harbor|"
    r"district|village|town|city|beach|fishing)\b",
    re.IGNORECASE,
)


def _fold(value: str) -> str:
    """Fold case, accents and punctuation so spellings converge."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\sऀ-෿]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalise(value: str) -> str:
    """Folded, with the filler words a caller wraps a place name in removed."""
    return re.sub(r"\s+", " ", _NOISE.sub(" ", _fold(value))).strip()


def resolve(name: str) -> dict[str, object] | None:
    """A spoken place name to {name, latitude, longitude, state}, or None.

    None is a real answer and the caller must respect it. Picking the closest
    spelling would put a Kochi fisherman's answer on the Bay of Bengal.
    """
    raw = str(name or "").strip()
    if not raw:
        return None

    # Native script first: it needs no folding and is unambiguous.
    for native, key in NATIVE_NAMES.items():
        if native in raw:
            return _entry(key)

    # Two passes. The folded form comes FIRST, because stripping filler words
    # also strips real ones: "port blair" became "blair" and matched nothing.
    # The stripped form is the fallback, for "I am from near Veraval".
    candidates = sorted(
        list(PORTS.keys()) + list(ALIASES.keys()), key=len, reverse=True
    )
    for text in (_fold(raw), _normalise(raw)):
        if not text:
            continue
        if text in PORTS:
            return _entry(text)
        if text in ALIASES:
            return _entry(ALIASES[text])
        # A phrase like "kakinada andhra pradesh": look for a known name inside
        # it, longest first so "vasco da gama" is not cut short by a shorter
        # neighbour.
        for candidate in candidates:
            if re.search(rf"\b{re.escape(candidate)}\b", text):
                return _entry(ALIASES.get(candidate, candidate))
    return None


def _entry(key: str) -> dict[str, object]:
    latitude, longitude, state = PORTS[key]
    return {
        "name": key.title(),
        "latitude": latitude,
        "longitude": longitude,
        "state": state,
    }
