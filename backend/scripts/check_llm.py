"""Check the NVIDIA NIM credentials in .env, without touching the rest of SALTY.

Run from backend/:  python -m scripts.check_llm

Prints exactly what NIM says about the key, the model, and whether the model
actually emits a tool call - the marine agent is useless without that.
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

from marine_agent import DEFAULT_NIM_BASE_URL, DEFAULT_NIM_MODEL

KEY = (os.getenv("NVIDIA_API_KEYS", "").split(",")[0] or os.getenv("NVIDIA_API_KEY", "")).strip()
MODEL = os.getenv("NIM_MODEL", DEFAULT_NIM_MODEL)
BASE = os.getenv("NIM_BASE_URL", DEFAULT_NIM_BASE_URL).rstrip("/")

if not KEY:
    raise SystemExit("NVIDIA_API_KEY is empty. Add it to .env as NVIDIA_API_KEY=nvapi-...")

print(f"key   : {len(KEY)} chars, starts {KEY[:6]!r}")
print(f"model : {MODEL}")
print(f"base  : {BASE}\n")

HEADERS = {
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "SALTY/1.0",
}


def call(label, url, body=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers=HEADERS,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            print(f"{label}: OK ({response.status})")
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        print(f"{label}: HTTP {exc.code}")
        print(f"  NIM said: {detail[:600]}")
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        print(f"{label}: {type(exc).__name__}: {exc}")
    return None


# 1. Is the model hosted?
models = call("models", f"{BASE}/models")
if models:
    ids = sorted(item["id"] for item in models.get("data", []))
    print(f"  {len(ids)} models hosted; {MODEL} hosted: {'YES' if MODEL in ids else 'NO'}")

# 2. Is the key accepted, and does the model call a tool when it should?
reply = call(
    "tools ",
    f"{BASE}/chat/completions",
    {
        "model": MODEL,
        "messages": [{"role": "user", "content": "What is the sea state at 17.7N 83.3E right now?"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_current_conditions",
                "description": "Live sea state at one position.",
                "parameters": {
                    "type": "object",
                    "properties": {"latitude": {"type": "number"}, "longitude": {"type": "number"}},
                    "required": ["latitude", "longitude"],
                },
            },
        }],
        "tool_choice": "auto",
        "max_tokens": 400,
    },
)
if reply:
    message = (reply.get("choices") or [{}])[0].get("message") or {}
    calls = message.get("tool_calls") or []
    if calls:
        fn = calls[0].get("function", {})
        print(f"  tool call: {fn.get('name')}({fn.get('arguments')})  -> tool calling works")
    else:
        print(f"  NO tool call; the model replied: {(message.get('content') or '')[:200]!r}")
        print("  Pick a NIM model with tool calling support via NIM_MODEL.")
