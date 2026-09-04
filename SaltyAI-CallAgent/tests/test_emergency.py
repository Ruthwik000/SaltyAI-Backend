"""
Tests for Layered Emergency Detection and Forwarding.
"""

import pytest
import respx
import httpx
from app.api.emergency import EmergencyDetector


def test_layer1_emergency_detection_multilingual():
    """Verify Layer 1 keyword/regex detection across multiple regional languages."""
    detector = EmergencyDetector()

    # Tamil
    is_em, reason = detector.detect("படகு மூழ்குது அண்ணா உடனே வாங்க")
    assert is_em
    assert "layer_1" in reason

    is_em, _ = detector.detect("காப்பாத்துங்க என்ஜின் பழுது")
    assert is_em

    # Hindi
    is_em, reason = detector.detect("नाव डूब रही है हमें बचाओ")
    assert is_em
    assert "layer_1" in reason

    # Telugu
    is_em, _ = detector.detect("మమ్మల్ని రక్షించండి పడవ ప్రమాదం")
    assert is_em

    # Malayalam
    is_em, _ = detector.detect("ഞങ്ങളെ രക്ഷിക്കണേ ബോട്ട് മുങ്ങുന്നു")
    assert is_em

    # Bengali
    is_em, _ = detector.detect("বাঁচাও নৌকা ডুবছে")
    assert is_em

    # Marathi
    is_em, _ = detector.detect("वाचवा बोट बुडत आहे")
    assert is_em

    # English Universal
    is_em, _ = detector.detect("Mayday mayday, we are sinking")
    assert is_em

    is_em, _ = detector.detect("Engine failed and drifting away into deep ocean")
    assert is_em


def test_layer2_semantic_emergency_detection():
    """Verify Layer 2 semantic intent detection on non-standard phrasing."""
    detector = EmergencyDetector()

    # Co-occurrence of vessel + failure keywords
    is_em, reason = detector.detect("The motor is dead and we are leaking water")
    assert is_em
    assert "layer_2" in reason

    # Normal non-emergency fishing question
    is_em, _ = detector.detect("Can I go fishing tomorrow near Chennai harbour?")
    assert not is_em

    # Normal sea condition question
    is_em, _ = detector.detect("PFZ enga irukku anna? Weather epdi?")
    assert not is_em


@pytest.mark.asyncio
async def test_layer3_emergency_dispatch():
    """Verify Layer 3 async dispatch to Main SALTY AI Backend."""
    detector = EmergencyDetector(backend_url="http://mock-rescue-backend:8080")

    with respx.mock(base_url="http://mock-rescue-backend:8080") as respx_mock:
        respx_mock.post("/api/emergency").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "acknowledged",
                    "rescue_id": "RESCUE-2026-9081",
                    "message": "Coastal rescue team alerted",
                }
            )
        )

        resp = await detector.dispatch_emergency(
            call_id="call-sos-101",
            phone_number="+919876543210",
            transcript="Boat sinking near Rameswaram",
            language="en-IN",
        )

        assert resp.status == "acknowledged"
        assert resp.rescue_id == "RESCUE-2026-9081"
