# -*- coding: utf-8 -*-
"""Place a fake phone call straight into the agent, with no Exotel and no ngrok.

    python test_call_local.py
    python test_call_local.py --text "కాకినాడ లో ఈరోజు సముద్రం ఎలా ఉంది?"

This speaks to ws://127.0.0.1:8001/ws/exotel/stream exactly the way Exotel's
Voicebot applet does: a start frame, then base64 8 kHz PCM media frames, then a
stop. It synthesises the caller's voice with Sarvam so the whole chain runs -
speech in, transcription, the location gate, the data API, the marine tools,
speech out - and writes the agent's reply to reply.wav so you can listen to it.

Use it to settle the question the phone cannot answer: is the agent broken, or
is the Exotel flow pointed at the wrong place? If this test talks back, the
agent works and the problem is in the Exotel dashboard.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import httpx
    import websockets
except ImportError as exc:  # pragma: no cover
    print(f"Missing dependency: {exc}. Run:  pip install websockets httpx")
    raise SystemExit(1)

from app.config import settings  # noqa: E402
from app.speech.audio_utils import pcm_to_b64  # noqa: E402

WS_URL = f"ws://127.0.0.1:{settings.PORT}/ws/exotel/stream"
SAMPLE_RATE = settings.AUDIO_SAMPLE_RATE          # 8000, telephony
CHUNK_BYTES = settings.AUDIO_CHUNK_BYTES          # 3200
REAL_TIME = CHUNK_BYTES / (SAMPLE_RATE * 2)       # seconds of audio per chunk

DEFAULT_QUESTION = "కాకినాడ లో ఈరోజు సముద్రం ఎలా ఉంది?"


async def speak_as_caller(text: str, language: str) -> bytes:
    """Sarvam speaks the caller's line, so there is real speech to transcribe."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{settings.SARVAM_BASE_URL.rstrip('/')}/text-to-speech",
            headers={"api-subscription-key": settings.SARVAM_API_KEY},
            json={
                "text": text,
                "target_language_code": language,
                "speaker": settings.SARVAM_DEFAULT_SPEAKER,
                "model": settings.SARVAM_TTS_MODEL,
                "speech_sample_rate": SAMPLE_RATE,
            },
        )
    if response.status_code != 200:
        raise RuntimeError(f"Sarvam TTS returned HTTP {response.status_code}: {response.text[:200]}")
    audio = base64.b64decode(response.json()["audios"][0])
    # Sarvam returns a WAV; the stream carries raw PCM, so drop the 44-byte header.
    return audio[44:] if audio[:4] == b"RIFF" else audio


def write_wav(path: str, pcm: bytes) -> None:
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)


async def run(text: str, language: str, wait: float) -> int:
    print(f"Calling {WS_URL}")
    print(f"Caller says ({language}): {text}\n")

    try:
        caller_pcm = await speak_as_caller(text, language)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  could not synthesise the caller's voice: {exc}")
        return 1
    print(f"  caller audio: {len(caller_pcm)} bytes, {len(caller_pcm) / (SAMPLE_RATE * 2):.1f}s")

    stream_sid = f"local-test-{int(time.time())}"
    reply = bytearray()
    events: list[str] = []

    try:
        async with websockets.connect(WS_URL, max_size=None) as socket:
            print("  PASS  WebSocket accepted\n")

            await socket.send(json.dumps({"event": "connected"}))
            await socket.send(json.dumps({
                "event": "start",
                "sequence_number": "1",
                "stream_sid": stream_sid,
                "start": {
                    "stream_sid": stream_sid,
                    "call_sid": stream_sid,
                    "account_sid": settings.EXOTEL_ACCOUNT_SID or "test",
                    "from": "+919999999999",
                    "to": settings.EXOTEL_CALLER_ID or "04045902324",
                    "media_format": {"encoding": "base64", "sample_rate": SAMPLE_RATE, "bit_rate": "16"},
                    "custom_parameters": {},
                },
            }))

            async def receive() -> None:
                """Collect everything the agent sends back until the call ends."""
                try:
                    while True:
                        raw = await socket.recv()
                        frame = json.loads(raw)
                        event = frame.get("event")
                        events.append(event)
                        if event == "media":
                            reply.extend(base64.b64decode(frame["media"]["payload"]))
                        elif event == "mark":
                            print(f"  agent mark: {frame.get('mark', {}).get('name')}")
                except Exception:
                    return

            listener = asyncio.create_task(receive())

            # Stream the caller's speech at real time, the way a phone does.
            for offset in range(0, len(caller_pcm), CHUNK_BYTES):
                chunk = caller_pcm[offset:offset + CHUNK_BYTES]
                await socket.send(json.dumps({
                    "event": "media",
                    "stream_sid": stream_sid,
                    "media": {"payload": pcm_to_b64(chunk)},
                }))
                await asyncio.sleep(REAL_TIME)

            # Silence, so the agent's voice-activity detector knows the turn ended.
            silence = b"\x00" * CHUNK_BYTES
            for _ in range(int(1.5 / REAL_TIME)):
                await socket.send(json.dumps({
                    "event": "media",
                    "stream_sid": stream_sid,
                    "media": {"payload": pcm_to_b64(silence)},
                }))
                await asyncio.sleep(REAL_TIME)

            print(f"  waiting up to {wait:.0f}s for the answer "
                  f"(a live INCOIS answer takes 15-30s)...")
            deadline = time.time() + wait
            settled = 0
            while time.time() < deadline:
                before = len(reply)
                await asyncio.sleep(1.0)
                if len(reply) == before and reply:
                    settled += 1
                    if settled >= 3:
                        break
                else:
                    settled = 0

            await socket.send(json.dumps({"event": "stop", "stream_sid": stream_sid}))
            listener.cancel()

    except ConnectionRefusedError:
        print(f"FAIL  nothing is listening on port {settings.PORT}.")
        print(f"      Start it with:  uvicorn app.main:app --host 0.0.0.0 --port {settings.PORT}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  the call failed: {type(exc).__name__}: {exc}")
        return 1

    print()
    if reply:
        write_wav("reply.wav", bytes(reply))
        seconds = len(reply) / (SAMPLE_RATE * 2)
        print(f"  PASS  the agent replied with {seconds:.1f}s of audio")
        print( "        saved to reply.wav — play it and check it is your language")
        print(f"        frames received: {', '.join(sorted(set(events)))}")
        print()
        print("The agent works end to end. If a real phone call still drops, the")
        print("problem is the Exotel flow, not this service — see the log line the")
        print("agent prints when Exotel fetches / instead of opening the WebSocket.")
        return 0

    print("  FAIL  the agent sent no audio back")
    print(f"        frames received: {', '.join(sorted(set(events))) or 'none'}")
    print( "        Check the agent's own terminal: it logs the transcript, the")
    print( "        language it detected, and whether the data API answered.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default=DEFAULT_QUESTION, help="what the caller says")
    parser.add_argument("--language", default=settings.DEFAULT_FALLBACK_LANGUAGE,
                        help="BCP-47 code for the caller's voice, e.g. te-IN")
    parser.add_argument("--wait", type=float, default=75.0,
                        help="seconds to wait for the answer")
    args = parser.parse_args()
    return asyncio.run(run(args.text, args.language, args.wait))


if __name__ == "__main__":
    raise SystemExit(main())
