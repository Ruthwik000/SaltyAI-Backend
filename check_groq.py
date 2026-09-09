"""Check the Groq credentials in .env, without touching the rest of SALTY.

Run:  py check_groq.py

Prints exactly what Groq says about the key, so a 403 in the chat window can be
told apart from a problem in the agent code.
"""

import json
import os
import urllib.error
import urllib.request

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    print("python-dotenv is not installed (pip install python-dotenv);")
    print("reading the environment only.\n")

KEY = os.getenv("GROQ_API_KEY", "").strip()
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
BASE = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")

if not KEY:
    raise SystemExit("GROQ_API_KEY is empty. Add it to .env as GROQ_API_KEY=gsk_...")

print(f"key   : {len(KEY)} chars, starts {KEY[:4]!r}")
print(f"model : {MODEL}")
print(f"base  : {BASE}\n")

HEADERS = {
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
    "User-Agent": "SALTY/1.0",
}


def call(label, url, body=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers=HEADERS,
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            print(f"{label}: OK ({response.status})")
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        print(f"{label}: HTTP {exc.code}")
        print(f"  Groq said: {detail[:600]}")
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        print(f"{label}: {type(exc).__name__}: {exc}")
    return None


# 1. Is the key accepted at all, and which models can it reach?
models = call("auth  ", f"{BASE}/models")
if models:
    ids = sorted(item["id"] for item in models.get("data", []))
    print(f"  {len(ids)} models available")
    print(f"  {MODEL} available: {'YES' if MODEL in ids else 'NO'}")
    if MODEL not in ids:
        print("  chat-capable ids:", ", ".join(i for i in ids if "whisper" not in i)[:400])

# 2. Can it actually complete with the configured model?
call(
    "chat  ",
    f"{BASE}/chat/completions",
    {"model": MODEL, "messages": [{"role": "user", "content": "reply with OK"}], "max_tokens": 5},
)
