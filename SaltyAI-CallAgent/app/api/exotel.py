"""Exotel outbound calling API used by the main SALTY application."""

import logging
import re
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/exotel", tags=["Exotel"])


class OutboundCallRequest(BaseModel):
    phone_number: str = Field(description="Destination phone number in E.164 format")


class OutboundCallResponse(BaseModel):
    status: str
    call_sid: str | None = None
    message: str


@router.get("/status")
async def exotel_status():
    """Return safe, non-secret Exotel configuration status for the main UI."""
    configured = all(
        (
            settings.EXOTEL_API_KEY,
            settings.EXOTEL_API_TOKEN,
            settings.EXOTEL_ACCOUNT_SID,
            settings.EXOTEL_CALLER_ID,
            settings.EXOTEL_STREAM_URL,
        )
    ) and settings.EXOTEL_STREAM_URL.startswith("wss://")
    return {
        "status": "ready" if configured else "not_configured",
        "provider": "Exotel",
        "stream_configured": bool(settings.EXOTEL_STREAM_URL),
    }


def _validate_phone(phone_number: str) -> str:
    normalized = re.sub(r"[\s()-]", "", phone_number)
    if not re.fullmatch(r"\+[1-9]\d{7,14}", normalized):
        raise HTTPException(status_code=422, detail="phone_number must be a valid E.164 number")
    return normalized


def _xml_value(body: str, tag: str) -> str | None:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    element = root.find(f".//{tag}")
    return element.text if element is not None else None


@router.post("/call", response_model=OutboundCallResponse)
async def start_outbound_call(request: OutboundCallRequest):
    """Ask Exotel to call the user and attach the call to SALTY AgentStream.

    This endpoint intentionally uses Exotel's HTTPS Calls/connect API. It does
    not create a SIP call or expose Exotel credentials to the browser.
    """
    destination = _validate_phone(request.phone_number)
    required = {
        "EXOTEL_API_KEY": settings.EXOTEL_API_KEY,
        "EXOTEL_API_TOKEN": settings.EXOTEL_API_TOKEN,
        "EXOTEL_ACCOUNT_SID": settings.EXOTEL_ACCOUNT_SID,
        "EXOTEL_CALLER_ID": settings.EXOTEL_CALLER_ID,
        "EXOTEL_STREAM_URL": settings.EXOTEL_STREAM_URL,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise HTTPException(status_code=503, detail=f"Exotel is not fully configured: {', '.join(missing)}")
    if not settings.EXOTEL_STREAM_URL.startswith("wss://"):
        raise HTTPException(status_code=503, detail="EXOTEL_STREAM_URL must be a public wss:// URL")

    endpoint = (
        f"https://{settings.EXOTEL_SUB_DOMAIN}/v1/Accounts/"
        f"{settings.EXOTEL_ACCOUNT_SID}/Calls/connect"
    )
    form = {
        "From": destination,
        "CallerId": settings.EXOTEL_CALLER_ID,
        "StreamUrl": settings.EXOTEL_STREAM_URL,
        "StreamType": "bidirectional",
        "CallType": "trans",
        "TimeLimit": str(settings.EXOTEL_CALL_TIME_LIMIT_SECONDS),
        "TimeOut": str(settings.EXOTEL_CALL_TIMEOUT_SECONDS),
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                endpoint,
                data=form,
                auth=(settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN),
            )
    except httpx.HTTPError as exc:
        logger.error("Exotel call request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Exotel") from exc

    if response.status_code >= 400:
        logger.warning("Exotel rejected outbound call: HTTP %s", response.status_code)
        detail = _xml_value(response.text, "message") or "Exotel rejected the call request"
        raise HTTPException(status_code=502, detail=detail)

    call_sid = _xml_value(response.text, "sid")
    logger.info("Exotel outbound call requested for %s, call_sid=%s", destination, call_sid)
    return OutboundCallResponse(
        status="initiated",
        call_sid=call_sid,
        message="Exotel is calling your phone. Answer to start the SALTY voice session.",
    )
