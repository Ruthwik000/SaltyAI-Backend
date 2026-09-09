# -*- coding: utf-8 -*-
"""The location gate on a phone call, in every language the agent answers in.

    python test_location_flow.py

Every marine tool needs a latitude and longitude. A phone call has neither, so
the agent asks the caller where they are - and that question, and the decision
to ask it at all, both have to work in the language the caller is speaking.

Sarvam runs in transcribe mode, so a Telugu caller's words arrive as Telugu
script. While the trigger list was English-only the gate simply never fired for
them: the question went straight to the agent with no position, and every tool
answered about the default coast. These tests are what caught that.

No network and no mocks - this is the decision logic on its own.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.conversation.manager import (  # noqa: E402
    location_from_reply,
    location_in_question,
    needs_location_context,
)
from app.conversation.prompts import PROMPTS, spoken  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  {mark}  {name}" + (f"   {detail}" if detail and not condition else ""))


# A marine question in each language the console supports, as a caller says it.
MARINE_QUESTIONS = [
    ("en-IN", "is it safe to sail tomorrow morning?"),
    ("te-IN", "రేపు ఉదయం సముద్రంలోకి వెళ్ళవచ్చా?"),
    ("hi-IN", "क्या आज समुद्र में जाना सुरक्षित है?"),
    ("ta-IN", "நாளை கடலுக்குப் போகலாமா?"),
    ("ml-IN", "നാളെ കടലിൽ പോകാൻ പറ്റുമോ?"),
    ("kn-IN", "ನಾಳೆ ಸಮುದ್ರಕ್ಕೆ ಹೋಗಬಹುದೇ?"),
    ("bn-IN", "আজ সমুদ্রে যাওয়া কি নিরাপদ?"),
    ("mr-IN", "उद्या समुद्रात जाणे सुरक्षित आहे का?"),
    ("gu-IN", "આજે દરિયામાં જવું સલામત છે?"),
    ("or-IN", "ଆଜି ସମୁଦ୍ରକୁ ଯିବା ନିରାପଦ କି?"),
]

# Questions that need no position at all.
GENERAL_QUESTIONS = [
    "hello",
    "who are you",
    "what is your name",
    "thank you",
]


def main() -> int:
    print("\n1. A marine question triggers the location gate, in every language")
    for language, question in MARINE_QUESTIONS:
        check(f"{language}  {question[:38]}", needs_location_context(question))

    print("\n2. A general question does not")
    for question in GENERAL_QUESTIONS:
        check(f"no gate for {question!r}", not needs_location_context(question))

    print("\n3. The agent asks in the caller's own language")
    for language, _ in MARINE_QUESTIONS:
        text = spoken("ask_location", language)
        english = PROMPTS["ask_location"]["en-IN"]
        check(f"{language} prompt exists", bool(text))
        if language != "en-IN":
            check(f"{language} prompt is not English", text != english, text)

    print("\n4. Every prompt is translated into every language")
    for key, table in PROMPTS.items():
        check(f"{key}: ten languages", len(table) == 10, str(sorted(table)))
        check(f"{key}: no duplicates", len(set(table.values())) == 10)
    check("a bare code still resolves", spoken("ask_location", "te") == PROMPTS["ask_location"]["te-IN"])
    check("an unknown code falls back to English",
          spoken("ask_location", "fr-FR") == PROMPTS["ask_location"]["en-IN"])

    print("\n5. The reply becomes a position, not just a name")
    for reply, expected in [
        ("Kakinada", "Kakinada"),
        ("I am from Vizag", "Visakhapatnam"),
        ("కాకినాడ", "Kakinada"),
        ("విశాఖపట్నం", "Visakhapatnam"),
        ("சென்னை", "Chennai"),
        ("കൊച്ചി", "Kochi"),
        ("ಮಂಗಳೂರು", "Mangaluru"),
        ("મુંબઈ", "Mumbai"),
        ("Cochin", "Kochi"),
        ("near Veraval", "Veraval"),
        ("port blair", "Port Blair"),
    ]:
        found = location_from_reply(reply)
        ok = found is not None and found.name == expected
        check(f"{reply!r} -> {expected}", ok, str(found))
        if ok:
            check(f"  {expected} has coordinates",
                  found.latitude is not None and found.longitude is not None)

    print("\n6. A non-answer is not treated as a place")
    for reply in ["yes", "no", "అవును", "तेलियदు" if False else "पता नहीं",
                  "தெரியாது", "ഇല്ല", "ಗೊತ್ತಿಲ್ಲ", "না", "ના", ""]:
        check(f"{reply!r} rejected", location_from_reply(reply) is None)

    print("\n7. Somewhere real but not coastal is rejected, not guessed at")
    for reply in ["Delhi", "Bangalore", "Hyderabad", "some village nobody knows"]:
        check(f"{reply!r} rejected", location_from_reply(reply) is None)

    print("\n8. A place named inside the question is used without asking again")
    for question, expected in [
        ("kakinada lo weather enti", "Kakinada"),
        ("what is the weather in Chennai", "Chennai"),
        ("కాకినాడ లో వాతావరణం ఎలా ఉంది", "Kakinada"),
        ("मुंबई में मौसम कैसा है", "Mumbai"),
        ("சென்னையில் கடல் நிலவரம் என்ன", "Chennai"),
        ("is it safe near Veraval today", "Veraval"),
    ]:
        found = location_in_question(question)
        ok = found is not None and found.name == expected
        check(f"{question[:40]!r} -> {expected}", ok, str(found))
        if ok:
            check("  and it carries coordinates and a state",
                  found.latitude is not None and bool(found.state))

    print("\n9. A question with no place in it does not invent one")
    for question in MARINE_QUESTIONS[1:4]:
        check(f"{question[0]} question has no place", location_in_question(question[1]) is None,
              str(location_in_question(question[1])))

    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print(f"  failed: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
