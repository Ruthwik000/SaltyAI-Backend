"""
Tests for Sarvam Saaras STT client.
"""

import pytest
import respx
import httpx
from app.speech.stt import SarvamSTTClient


@pytest.mark.asyncio
async def test_stt_transcribe_success(sample_pcm_audio):
    """Verify successful STT transcription handling."""
    client = SarvamSTTClient(api_key="test-key", base_url="https://api.sarvam.ai")

    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/speech-to-text").mock(
            return_value=httpx.Response(
                200,
                json={
                    "request_id": "req-stt-123",
                    "transcript": "நாளைக்கு மீன்பிடிக்க போகலாமா?",
                    "language_code": "ta-IN",
                }
            )
        )

        result = await client.transcribe(
            pcm_audio=sample_pcm_audio,
            sample_rate=8000,
            language_code="unknown",
        )

        assert not result.is_empty
        assert result.transcript == "நாளைக்கு மீன்பிடிக்க போகலாமா?"
        assert result.language_code == "ta-IN"
        assert result.request_id == "req-stt-123"


@pytest.mark.asyncio
async def test_stt_transcribe_empty_audio():
    """Verify empty/short audio returns is_empty=True without calling external API."""
    client = SarvamSTTClient(api_key="test-key")
    result = await client.transcribe(pcm_audio=b"", sample_rate=8000)
    assert result.is_empty
    assert result.transcript == ""


@pytest.mark.asyncio
async def test_stt_transcribe_api_error(sample_pcm_audio):
    """Verify graceful handling when Sarvam STT returns HTTP 500 error."""
    client = SarvamSTTClient(api_key="test-key", base_url="https://api.sarvam.ai")

    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/speech-to-text").mock(
            return_value=httpx.Response(500, json={"error": "Internal server error"})
        )

        result = await client.transcribe(
            pcm_audio=sample_pcm_audio,
            sample_rate=8000,
            language_code="ta-IN",
        )

        assert result.is_empty
        assert result.transcript == ""
        assert result.language_code == "ta-IN"
