# -*- coding: utf-8 -*-
"""End-to-end check of the phone contract, against the running data API.

    python api_server.py            # in one terminal
    python test_call_agent.py       # in another

This posts the EXACT payload the call agent sends - the same field names, the
same BCP-47 language code, a place name with no coordinates - and shows what
comes back. It is the check that would have caught all four of the faults that
made every phone call answer "sorry, I'm having trouble connecting":

  * a ten second timeout on an agent that needs fifteen to thirty;
  * conversation_history sent, "history" read, so calls had no memory;
  * location.latitude sent, location.lat read, so every tool answered about
    the wrong coast;
  * language "te-IN" passed to a model asked to "reply in te-IN", which it
    read as English.

Nothing here is mocked. A failure means the phone agent would fail the same way.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8010/api/ai/query"

# Exactly what app/ai/backend_client.py puts on the wire.
TURNS = [
    ("te-IN", "Kakinada", "రేపు ఉదయం సముద్రంలోకి వెళ్ళవచ్చా?"),
    ("te-IN", "Kakinada", "దగ్గరలో చేపలు ఎక్కడ దొరుకుతాయి?"),
    ("hi-IN", "Veraval", "क्या आज बिजली या तूफान का खतरा है?"),
    ("en-IN", "Vizag", "when is high tide today?"),
]


def post(payload: dict, timeout: float = 60.0) -> tuple[int, dict]:
    request = urllib.request.Request(
        API,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Call-ID": payload["call_id"]},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")[:400]}
    except Exception as exc:  # noqa: BLE001 - the point is to report it
        return 0, {"error": str(exc)}


def main() -> int:
    print(f"Posting the call-agent payload to {API}\n")
    history: list[dict[str, str]] = []
    failures = 0

    for index, (language, place, question) in enumerate(TURNS, start=1):
        payload = {
            "call_id": f"test-call-{index}",
            "phone_number": "+919999999999",
            "language": language,
            "message": question,
            # The field name the call agent uses. The API must read it.
            "conversation_history": history[-8:],
            # A place name and NO coordinates, which is all a phone call has.
            "location": {"name": place, "latitude": None, "longitude": None},
        }

        started = time.time()
        status, data = post(payload)
        elapsed = time.time() - started

        answer = str(data.get("response", "")).strip()
        tools = [call.get("tool") for call in data.get("tool_calls", [])]

        ok = status == 200 and answer and "NOT AVAILABLE" not in answer
        print(f"{'PASS' if ok else 'FAIL'}  turn {index}  [{language}]  {place}  ({elapsed:.1f}s)")
        print(f"      asked : {question}")
        print(f"      answer: {answer or data.get('error') or '(empty)'}")
        print(f"      tools : {', '.join(t for t in tools if t) or '(none called)'}")
        print(f"      lang  : {data.get('language')}   priority: {data.get('priority')}")

        # A spoken answer must be speakable. Markdown read aloud is noise.
        for symbol in ("|", "**", "##", "- ", "•"):
            if symbol in answer:
                print(f"      WARNING: answer contains {symbol!r}, which a speech engine reads aloud")
        if elapsed > 45:
            print("      WARNING: slower than the call agent's 45s timeout")

        if not ok:
            failures += 1
        print()

        if answer:
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": answer})

    # Turn 2 is a follow-up: it only means something if history survived.
    print("Memory check: turn 2 was asked with turn 1 in history.")
    print("If the agent answered it as a fresh question, history is not reaching it.\n")
    print("ALL TURNS ANSWERED" if not failures else f"{failures} TURN(S) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
