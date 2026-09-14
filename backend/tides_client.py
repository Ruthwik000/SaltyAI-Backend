"""Tide predictions from Open-Meteo Marine.

Why not INCOIS
--------------
INCOIS has no tide service. Its own "Predicted Astronomical Tide" link goes to
an under-construction page, and the INCOIS mobile API answers every tide-shaped
endpoint name with its catch-all handler ("Hello World RESTful Jersey
'tidelatestdata'", HTTP 200) rather than data - a real endpoint there answers
202 with JSON, so the absence is unambiguous. Names tried: tidelatestdata,
tideslatestdata, tidaldata, tidedata, predictedtide, astronomicaltide,
tidelatest.

Open-Meteo Marine publishes `sea_level_height_msl`, hourly, worldwide, with no
API key. Verified against the live service on 2026-09-08 for Visakhapatnam:

    https://marine-api.open-meteo.com/v1/marine
      ?latitude=17.6868&longitude=83.2185
      &hourly=sea_level_height_msl&timezone=auto

  -> 0.03 m to 1.31 m over 48 hours, semi-diurnal, turning points six hours
     apart - which is what the Bay of Bengal actually does.

This is NOT an INCOIS product and must never be presented as one. Everything
returned carries the source, and the agent is told to name it.

Height is metres above mean sea level, not chart datum, so it is a guide to
when the water turns rather than a navigational depth.
"""

from __future__ import annotations

import json
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENDPOINT = "https://marine-api.open-meteo.com/v1/marine"
SOURCE = "Open-Meteo Marine tide prediction (sea level above mean sea level)"

_CACHE_TTL = 1800.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CTX = ssl._create_unverified_context()


class TideError(RuntimeError):
    """Raised when tide predictions cannot be read."""


def _fetch(url: str, timeout: float = 20.0) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "SALTY/1.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout, context=_CTX) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        raise TideError(f"Tide service returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise TideError(f"Could not reach the tide service: {exc}") from exc


def turning_points(times: list[str], heights: list[Any]) -> list[dict[str, Any]]:
    """High and low water: the local maxima and minima of the hourly curve.

    Hourly sampling puts each turn within half an hour of the true one, which
    is close enough to decide when to leave and far from precise enough to
    navigate on. The caller says so.
    """
    turns: list[dict[str, Any]] = []
    for index in range(1, len(heights) - 1):
        before, here, after = heights[index - 1], heights[index], heights[index + 1]
        if before is None or here is None or after is None:
            continue
        if here > before and here >= after:
            turns.append({"kind": "high", "time": times[index], "heightM": round(here, 2)})
        elif here < before and here <= after:
            turns.append({"kind": "low", "time": times[index], "heightM": round(here, 2)})
    return turns


def tide_forecast(lat: float, lon: float, days: int = 2) -> dict[str, Any]:
    """Tide turning points at a position, in the local timezone."""
    key = f"{lat:.3f},{lon:.3f},{days}"
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    url = f"{ENDPOINT}?" + urlencode({
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "hourly": "sea_level_height_msl",
        "timezone": "auto",
        "forecast_days": max(1, min(int(days), 5)),
    })
    payload = _fetch(url)
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    heights = hourly.get("sea_level_height_msl") or []
    if not times or not heights:
        raise TideError("The tide service returned no sea-level series")

    turns = turning_points(times, heights)
    now = payload.get("current_time") or time.strftime("%Y-%m-%dT%H:%M")
    upcoming = [turn for turn in turns if turn["time"] >= now] or turns

    result = {
        "source": SOURCE,
        "note": ("Predicted tide, not an INCOIS product. Heights are metres above mean "
                 "sea level, so this tells you when the water turns, not how deep it is."),
        "timezone": payload.get("timezone"),
        "position": {"latitude": lat, "longitude": lon},
        "nextHigh": next((t for t in upcoming if t["kind"] == "high"), None),
        "nextLow": next((t for t in upcoming if t["kind"] == "low"), None),
        "turningPoints": upcoming[:8],
        "rangeM": [round(min(h for h in heights if h is not None), 2),
                   round(max(h for h in heights if h is not None), 2)],
    }
    _cache[key] = (time.time(), result)
    return result
