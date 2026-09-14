"""Browser voice endpoints: transcription and speech, with Sarvam mocked."""

import math
import struct
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api import voice_api
from app.main import app
from app.models.schemas import STTResponse, TTSResponse
from app.speech.audio_utils import pcm_to_wav


def tone_wav(seconds=1.0, rate=16000):
    samples = [int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(seconds * rate))]
    return pcm_to_wav(struct.pack(f"<{len(samples)}h", *samples), sample_rate=rate)


def test_transcribe_returns_detected_language(monkeypatch):
    mock = AsyncMock(return_value=STTResponse(transcript="రేపు వెళ్ళవచ్చా", language_code="te-IN"))
    monkeypatch.setattr(voice_api.stt, "transcribe", mock)
    client = TestClient(app)
    response = client.post("/api/voice/transcribe", files={"file": ("q.wav", tone_wav(), "audio/wav")})
    assert response.status_code == 200
    assert response.json() == {"transcript": "రేపు వెళ్ళవచ్చా", "language": "te-IN"}
    assert mock.await_args.kwargs["language_code"] == "unknown"
    assert mock.await_args.kwargs["sample_rate"] == 16000


def test_transcribe_rejects_silence_result(monkeypatch):
    monkeypatch.setattr(voice_api.stt, "transcribe", AsyncMock(return_value=STTResponse(transcript="", language_code="en-IN", is_empty=True)))
    client = TestClient(app)
    response = client.post("/api/voice/transcribe", files={"file": ("q.wav", tone_wav(), "audio/wav")})
    assert response.status_code == 422


def test_speak_uses_script_language_and_returns_wav(monkeypatch):
    mock = AsyncMock(return_value=TTSResponse(pcm_audio=b"\x00\x01" * 2205, sample_rate=22050, duration_seconds=0.1))
    monkeypatch.setattr(voice_api.tts, "synthesize", mock)
    client = TestClient(app)
    response = client.post("/api/voice/speak", json={"text": "**ఈరోజు** సముద్రం ప్రశాంతంగా ఉంది."})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["x-speech-language"] == "te-IN"
    assert response.content[:4] == b"RIFF"
    assert "**" not in mock.await_args.args[0]


def test_speak_maps_browser_odia_code():
    assert voice_api.spoken_language("hello there", "or-IN") == "od-IN"
    assert voice_api.spoken_language("ଆଜି ସମୁଦ୍ର ଶାନ୍ତ", None) == "od-IN"
    assert voice_api.spoken_language("Sea is calm today", None) == "en-IN"


def test_chunks_break_at_sentences():
    parts = voice_api.chunks("One. " * 200, limit=100)
    assert all(len(part) <= 100 for part in parts)
    assert "".join(parts).count("One.") == 200
