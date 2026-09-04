"""
Tests for Sarvam Bulbul TTS client.
"""

import pytest
import respx
import httpx
import base64
from app.speech.tts import SarvamTTSClient


@pytest.mark.asyncio
async def test_tts_synthesize_success(sample_wav_audio):
    """Verify successful TTS synthesis and WAV to PCM conversion."""
    b64_wav = base64.b64encode(sample_wav_audio).decode("ascii")
    client = SarvamTTSClient(api_key="test-key", base_url="https://api.sarvam.ai")

    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/text-to-speech").mock(
            return_value=httpx.Response(
                200,
                json={
                    "request_id": "req-tts-456",
                    "audios": [b64_wav],
                }
            )
        )

        result = await client.synthesize(
            text="நாளை கடல் அமைதியாக இருக்கும்.",
            language_code="ta-IN",
            sample_rate=8000,
        )

        assert len(result.pcm_audio) > 0
        assert result.sample_rate == 8000
        assert result.duration_seconds > 0.5
        assert result.request_id == "req-tts-456"


@pytest.mark.asyncio
async def test_tts_synthesize_empty_text():
    """Verify empty text returns empty PCM audio without API call."""
    client = SarvamTTSClient(api_key="test-key")
    result = await client.synthesize(text="", language_code="ta-IN")
    assert result.pcm_audio == b""
    assert result.duration_seconds == 0.0


@pytest.mark.asyncio
async def test_tts_synthesize_api_error():
    """Verify graceful handling when Sarvam TTS returns error."""
    client = SarvamTTSClient(api_key="test-key", base_url="https://api.sarvam.ai")

    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/text-to-speech").mock(
            return_value=httpx.Response(500, json={"error": "Synthesis failed"})
        )

        result = await client.synthesize(
            text="Testing error case",
            language_code="en-IN",
        )
        assert result.pcm_audio == b""



@pytest.mark.asyncio
async def test_tts_receives_complete_text_and_no_substring_loss(sample_wav_audio):
    """Verify Sarvam Bulbul TTS receives the complete 100% textual payload without truncation."""
    b64_wav = base64.b64encode(sample_wav_audio).decode("ascii")
    client = SarvamTTSClient(api_key="test-key", base_url="https://api.sarvam.ai")

    captured_payload = None

    def capture_tts_request(request):
        nonlocal captured_payload
        import json
        captured_payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"audios": [b64_wav]})

    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/text-to-speech").mock(side_effect=capture_tts_request)

        full_long_text = "రేపు విశాఖపట్నం తీరంలో వాతావరణం అనుకూలంగా ఉంటుంది. గాలి వేగం పది నుండి పదిహేను కిలోమీటర్ల వేగంతో వీస్తుంది. వేటకు వెళ్ళే మత్స్యకారులు జాగ్రత్తలు పాటించండి."
        res = await client.synthesize(text=full_long_text, language_code="te-IN")

        assert captured_payload is not None
        assert captured_payload["inputs"][0] == full_long_text
        assert captured_payload["target_language_code"] == "te-IN"
        assert len(res.pcm_audio) > 0

