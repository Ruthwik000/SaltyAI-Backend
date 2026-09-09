# -*- coding: utf-8 -*-
"""Check every credential and every hop the phone agent depends on.

    python verify_call_agent.py

Run it from the SaltyAI-CallAgent folder, with the data API already running.
It checks the chain a real call travels, in order, and tells you which link is
broken rather than leaving you to infer it from a caller hearing silence:

    fisherman -> Exotel -> ngrok -> call agent -> data API -> INCOIS/Groq
                                       |
                                    Sarvam (speech in, speech out)

No secrets are printed. Keys are reported by length and first four characters,
which is enough to tell a real key from a placeholder and not enough to use.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, ".."))

PASS, WARN, FAIL = [], [], []


def report(status: str, name: str, detail: str = "") -> None:
    {"PASS": PASS, "WARN": WARN, "FAIL": FAIL}[status].append(name)
    print(f"  {status:4}  {name}")
    if detail:
        for line in str(detail).splitlines():
            print(f"        {line}")


def load_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def masked(value: str) -> str:
    return f"{len(value)} chars, starts {value[:4]!r}" if value else "EMPTY"


def get(url: str, headers: dict[str, str] | None = None, timeout: float = 15.0):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def post(url: str, payload: Any, headers: dict[str, str], timeout: float = 60.0):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def main() -> int:
    agent_env = load_env(os.path.join(HERE, ".env"))
    api_env = load_env(os.path.join(BACKEND, ".env"))

    # ------------------------------------------------------------------
    print("\n1. Configuration")
    # ------------------------------------------------------------------
    required = {
        "EXOTEL_API_KEY": agent_env.get("EXOTEL_API_KEY", ""),
        "EXOTEL_API_TOKEN": agent_env.get("EXOTEL_API_TOKEN", ""),
        "EXOTEL_ACCOUNT_SID": agent_env.get("EXOTEL_ACCOUNT_SID", ""),
        "EXOTEL_CALLER_ID": agent_env.get("EXOTEL_CALLER_ID", ""),
        "EXOTEL_STREAM_URL": agent_env.get("EXOTEL_STREAM_URL", ""),
        "SARVAM_API_KEY": agent_env.get("SARVAM_API_KEY", ""),
    }
    for name, value in required.items():
        secret = "KEY" in name or "TOKEN" in name or "SID" in name
        if not value:
            report("FAIL", f"{name} is set", "not present in SaltyAI-CallAgent/.env")
        elif value.lower().startswith(("your", "xxx", "changeme", "<")):
            report("FAIL", f"{name} is set", f"still a placeholder: {masked(value)}")
        else:
            report("PASS", f"{name} is set", masked(value) if secret else value)

    backend_key = api_env.get("GROQ_API_KEY", "")
    if backend_key.startswith("gsk_") and len(backend_key) > 40:
        report("PASS", "data API GROQ_API_KEY looks real", masked(backend_key))
    else:
        report("FAIL", "data API GROQ_API_KEY looks real",
               f"SaltyAI-Backend/.env has {masked(backend_key)} — the agent cannot reason without it")

    agent_key = agent_env.get("GROQ_API_KEY", "")
    if agent_key.lower().startswith(("your", "xxx", "")) and not agent_key.startswith("gsk_"):
        report("WARN", "call agent GROQ_API_KEY is a placeholder",
               "Harmless: the call agent no longer reasons for itself, it asks the\n"
               "data API. It is only used when CALL_AGENT_TEST_MODE=true, which\n"
               "must stay false — that mode answers with no marine data at all.")

    if agent_env.get("CALL_AGENT_TEST_MODE", "false").lower() == "true":
        report("FAIL", "CALL_AGENT_TEST_MODE is off",
               "It is ON. Callers get a model with no marine data, answering from memory.")
    else:
        report("PASS", "CALL_AGENT_TEST_MODE is off")

    timeout = float(agent_env.get("AI_BACKEND_TIMEOUT_SECONDS", "10"))
    if timeout >= 30:
        report("PASS", "AI_BACKEND_TIMEOUT_SECONDS allows a real answer", f"{timeout}s")
    else:
        report("FAIL", "AI_BACKEND_TIMEOUT_SECONDS allows a real answer",
               f"{timeout}s is below the 15-30s a live INCOIS answer takes; every call will time out")

    port = agent_env.get("PORT", "8000")
    stream = agent_env.get("EXOTEL_STREAM_URL", "")
    if stream.startswith("wss://") and stream.endswith("/ws/exotel/stream"):
        report("PASS", "EXOTEL_STREAM_URL points at the stream endpoint")
    else:
        report("FAIL", "EXOTEL_STREAM_URL points at the stream endpoint",
               f"expected wss://<host>/ws/exotel/stream, got {stream!r}")

    if agent_env.get("PLAY_GREETING", "true").lower() != "true":
        report("WARN", "PLAY_GREETING is off",
               "The caller hears silence until they speak first. Set PLAY_GREETING=true\n"
               "so the line announces itself — on a demo call, silence reads as a dead line.")

    # ------------------------------------------------------------------
    print("\n2. The data API — where every answer actually comes from")
    # ------------------------------------------------------------------
    api_url = agent_env.get("AI_BACKEND_URL", "http://127.0.0.1:8010").rstrip("/")
    try:
        status, body = get(f"{api_url}/api/health", timeout=10)
        report("PASS" if status == 200 else "FAIL", "data API is running",
               "" if status == 200 else f"HTTP {status} from {api_url}/api/health")
    except Exception as exc:  # noqa: BLE001
        report("FAIL", "data API is running",
               f"{api_url} unreachable: {exc}\nStart it with:  python api_server.py")
        print("\nStopping here — nothing downstream can work without it.")
        return summary()

    try:
        status, body = post(
            f"{api_url}/api/ai/query",
            {
                "call_id": "verify", "phone_number": "+910000000000",
                "language": "te-IN", "message": "ఈరోజు సముద్రం ఎలా ఉంది?",
                "conversation_history": [],
                "location": {"name": "Kakinada", "latitude": None, "longitude": None},
            },
            {"Content-Type": "application/json"},
            timeout=90,
        )
        data = json.loads(body)
        answer = str(data.get("response", "")).strip()
        tools = [call.get("tool") for call in data.get("tool_calls", [])]
        if status == 200 and answer and "NOT AVAILABLE" not in answer:
            report("PASS", "the agent answers a Telugu question about a named place",
                   f"tools: {', '.join(t for t in tools if t) or 'none'}\nsaid: {answer[:150]}")
        else:
            report("FAIL", "the agent answers a Telugu question about a named place",
                   f"HTTP {status}: {answer or data}")
        for symbol in ("|", "**", "##"):
            if symbol in answer:
                report("WARN", "the spoken answer is free of markdown",
                       f"contains {symbol!r}, which the speech engine reads aloud")
                break
        else:
            if answer:
                report("PASS", "the spoken answer is free of markdown")
    except Exception as exc:  # noqa: BLE001
        report("FAIL", "the agent answers a Telugu question about a named place", str(exc))

    # ------------------------------------------------------------------
    print("\n3. Sarvam — the caller's ears and voice")
    # ------------------------------------------------------------------
    sarvam_key = agent_env.get("SARVAM_API_KEY", "")
    sarvam_base = agent_env.get("SARVAM_BASE_URL", "https://api.sarvam.ai").rstrip("/")
    if sarvam_key and not sarvam_key.lower().startswith("your"):
        try:
            status, body = post(
                f"{sarvam_base}/text-to-speech",
                {
                    "text": "సముద్రం ప్రశాంతంగా ఉంది.",
                    "target_language_code": "te-IN",
                    "speaker": agent_env.get("SARVAM_DEFAULT_SPEAKER", "shubh"),
                    "model": agent_env.get("SARVAM_TTS_MODEL", "bulbul:v3"),
                },
                {"Content-Type": "application/json", "api-subscription-key": sarvam_key},
                timeout=45,
            )
            payload = json.loads(body)
            audio = (payload.get("audios") or [""])[0]
            if status == 200 and len(audio) > 1000:
                report("PASS", "Sarvam speaks Telugu", f"{len(base64.b64decode(audio))} bytes of audio")
            else:
                report("FAIL", "Sarvam speaks Telugu", f"HTTP {status}, {len(audio)} chars of audio")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            hint = "  (the key is rejected)" if exc.code in (401, 403) else ""
            report("FAIL", "Sarvam speaks Telugu", f"HTTP {exc.code}{hint}: {body}")
        except Exception as exc:  # noqa: BLE001
            report("FAIL", "Sarvam speaks Telugu", str(exc))
    else:
        report("FAIL", "Sarvam speaks Telugu", "no usable SARVAM_API_KEY")

    # ------------------------------------------------------------------
    print("\n4. Exotel — the telephone line")
    # ------------------------------------------------------------------
    sid = agent_env.get("EXOTEL_ACCOUNT_SID", "")
    key = agent_env.get("EXOTEL_API_KEY", "")
    token = agent_env.get("EXOTEL_API_TOKEN", "")
    domain = agent_env.get("EXOTEL_SUB_DOMAIN", "api.exotel.com")
    if sid and key and token:
        auth = base64.b64encode(f"{key}:{token}".encode()).decode()
        try:
            status, body = get(
                f"https://{domain}/v1/Accounts/{sid}",
                {"Authorization": f"Basic {auth}"},
                timeout=20,
            )
            report("PASS" if status == 200 else "FAIL",
                   "Exotel accepts the API key, token and account SID",
                   "" if status == 200 else f"HTTP {status}")
        except urllib.error.HTTPError as exc:
            hint = {401: "key/token rejected", 403: "key/token rejected",
                    404: "account SID not found"}.get(exc.code, "")
            report("FAIL", "Exotel accepts the API key, token and account SID",
                   f"HTTP {exc.code} {hint}")
        except Exception as exc:  # noqa: BLE001
            report("FAIL", "Exotel accepts the API key, token and account SID", str(exc))
    else:
        report("FAIL", "Exotel accepts the API key, token and account SID", "credentials missing")

    # ------------------------------------------------------------------
    print("\n5. The public tunnel Exotel has to reach")
    # ------------------------------------------------------------------
    host = urllib.parse.urlparse(stream).hostname if stream else None
    if host:
        try:
            socket.getaddrinfo(host, 443)
            report("PASS", "the tunnel host resolves", host)
        except Exception as exc:  # noqa: BLE001
            report("FAIL", "the tunnel host resolves", f"{host}: {exc}")
        try:
            status, _ = get(f"https://{host}/health", timeout=20)
            report("PASS" if status == 200 else "WARN",
                   "the call agent answers through the tunnel",
                   "" if status == 200 else f"HTTP {status} — is the call agent running on port {port}?")
        except Exception as exc:  # noqa: BLE001
            report("FAIL", "the call agent answers through the tunnel",
                   f"{exc}\nThe agent must be running AND the tunnel must be up:\n"
                   f"  uvicorn app.main:app --host 0.0.0.0 --port {port}\n"
                   f"  ngrok http --url={host} {port}\n"
                   f"If the tunnel address has changed, update EXOTEL_STREAM_URL here AND\n"
                   f"the Voicebot applet URL in the Exotel dashboard — Exotel keeps calling\n"
                   f"whatever address it was last given.")
    else:
        report("FAIL", "the tunnel host resolves", "EXOTEL_STREAM_URL has no host")

    # ------------------------------------------------------------------
    print("\n6. The number to dial")
    # ------------------------------------------------------------------
    caller_id = agent_env.get("EXOTEL_CALLER_ID", "")
    if caller_id:
        pretty = caller_id if caller_id.startswith("0") else f"0{caller_id}"
        print(f"        Dial: {pretty}")
        print( "        This is your Exotel virtual number (ExoPhone). It only reaches the")
        print( "        agent if that ExoPhone has an App flow with a Voicebot applet")
        print(f"        pointing at:  {stream}")
        print( "        Set that in the Exotel dashboard: App Bazaar -> your flow ->")
        print( "        Voicebot applet -> URL. It is not configurable from this code.")
        print()
        print( "        To have it call YOU instead, with the agent already running:")
        print(f'          curl -X POST http://127.0.0.1:{port}/exotel/call \\')
        print( '            -H "Content-Type: application/json" \\')
        print( '            -d "{\\"phone_number\\": \\"+91XXXXXXXXXX\\"}"')
    else:
        report("FAIL", "an ExoPhone number is configured", "EXOTEL_CALLER_ID is empty")

    return summary()


def summary() -> int:
    print()
    print(f"{len(PASS)} passed, {len(WARN)} warning(s), {len(FAIL)} failed")
    for name in FAIL:
        print(f"  BROKEN:  {name}")
    for name in WARN:
        print(f"  CHECK:   {name}")
    if not FAIL:
        print("\nEvery link in the chain is up. The line is ready to take a call.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
