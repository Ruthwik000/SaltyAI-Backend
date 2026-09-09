# -*- coding: utf-8 -*-
"""Which Groq model gives this account the most room, measured not guessed.

    python check_groq_models.py

Groq returns the rate limits for your organisation in the response headers of
every call, so this makes one tiny tool-calling request per candidate model and
reads them back. It reports tokens per minute, requests per minute, and whether
the model handled a tool call at all - a model that cannot call tools is no use
here however generous its limit.

Nothing is changed. It prints the line to put in .env.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")

# Models on Groq that support tool calling. The agent is useless without it.
CANDIDATES = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct",
    "qwen/qwen3-32b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
]

PROBE_TOOL = [{
    "type": "function",
    "function": {
        "name": "get_current_conditions",
        "description": "Sea and weather right now at the user's place.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]


def load_key() -> str:
    for name in ("GROQ_API_KEYS", "GROQ_API_KEY"):
        value = os.getenv(name, "")
        if value:
            return value.split(",")[0].strip()
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, ".env"), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("GROQ_API_KEY=") and "your" not in line.lower():
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def probe(model: str, key: str) -> dict:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "What is the sea like right now?"}],
        "tools": PROBE_TOOL,
        "tool_choice": "auto",
        "max_tokens": 32,
        "temperature": 0,
    }).encode()
    request = urllib.request.Request(
        f"{BASE}/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "User-Agent": "SALTY/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            headers = response.headers
            body = json.loads(response.read().decode("utf-8"))
            message = (body.get("choices") or [{}])[0].get("message", {})
            return {
                "ok": True,
                "tpm": headers.get("x-ratelimit-limit-tokens"),
                "rpm": headers.get("x-ratelimit-limit-requests"),
                "tokens_left": headers.get("x-ratelimit-remaining-tokens"),
                "called_tool": bool(message.get("tool_calls")),
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:160]
        # A 429 still carries the limit headers, which is what we came for.
        return {
            "ok": exc.code == 429,
            "tpm": exc.headers.get("x-ratelimit-limit-tokens"),
            "rpm": exc.headers.get("x-ratelimit-limit-requests"),
            "tokens_left": exc.headers.get("x-ratelimit-remaining-tokens"),
            "called_tool": None,
            "note": "rate limited right now" if exc.code == 429 else f"HTTP {exc.code}: {detail}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "note": str(exc)[:120]}


def main() -> int:
    key = load_key()
    if not key:
        print("No usable GROQ_API_KEY found in the environment or .env")
        return 1

    print(f"Asking Groq what this account is allowed, per model.")
    print(f"A SALTY voice turn now costs about 2,700 tokens.\n")
    print(f"{'model':46} {'TPM':>8} {'RPM':>6}  {'tools':>5}  turns/min")
    print("-" * 82)

    rows = []
    for model in CANDIDATES:
        result = probe(model, key)
        tpm = result.get("tpm")
        rpm = result.get("rpm")
        tools = {True: "yes", False: "NO", None: "?"}[result.get("called_tool")]
        try:
            turns = int(tpm) // 2700 if tpm else 0
        except (TypeError, ValueError):
            turns = 0
        rows.append((turns, model, tpm, rpm, tools, result.get("note", "")))
        print(f"{model:46} {tpm or '-':>8} {rpm or '-':>6}  {tools:>5}  "
              f"{turns if turns else '-':>9}   {result.get('note','')}")

    usable = [r for r in rows if r[0] and r[4] != "NO"]
    print()
    if usable:
        best = max(usable)
        print(f"Most room for this account: {best[1]}")
        print(f"  about {best[0]} voice turns a minute at {best[2]} tokens/min")
        print()
        print("To switch, in SaltyAI-Backend/.env:")
        print(f"  GROQ_MODEL={best[1]}")
        print()
        print("Then restart api_server.py and run:  python test_call_agent.py")
        print("Check the answers are still sensible - a bigger budget is no use if")
        print("the model stops calling the right tool.")
    else:
        print("Could not read limits for any model. If everything says 'rate limited")
        print("right now', wait a minute and run this again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
