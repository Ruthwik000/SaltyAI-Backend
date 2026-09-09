"""Air weather and thunderstorm risk from Open-Meteo.

Why this exists alongside INCOIS
--------------------------------
The INCOIS Ocean State Forecast is an OCEAN model: waves, swell, currents, SST,
wind over water. It carries no air temperature, no humidity, no rainfall and no
thunderstorm signal. INCOIS issues High Wave and Swell Surge advisories, which
cover cyclone-driven seas, but publishes no lightning product and no
machine-readable cyclone bulletin - IMD's cyclone warnings are pages, not an API.

Open-Meteo fills exactly that gap, free and keyless. Verified live for
Visakhapatnam on 2026-09-08:

    https://api.open-meteo.com/v1/forecast
      ?latitude=..&longitude=..&hourly=weather_code,cape,precipitation,wind_gusts_10m
      &current=...&timezone=auto&wind_speed_unit=ms

  -> weather_code 95 (thunderstorm) on two forecast hours, CAPE to 4860 J/kg,
     gusts to 7.2 m/s, timezone resolved to Asia/Kolkata.

This is NOT an INCOIS product. Everything returned names its source.
"""

from __future__ import annotations

import json
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
SOURCE = "Open-Meteo weather forecast"

# WMO present-weather codes, in the words a fisherman would use.
WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}

THUNDER_CODES = {95, 96, 99}

# CAPE, convective available potential energy, in J/kg. Above ~2500 the
# atmosphere is unstable enough for strong thunderstorms; these thresholds are
# the standard forecasting bands, not something derived here.
CAPE_BANDS = ((3500, "extreme"), (2500, "strong"), (1000, "moderate"), (0, "low"))

_CACHE_TTL = 900.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CTX = ssl._create_unverified_context()


class WeatherError(RuntimeError):
    """Raised when the weather forecast cannot be read."""


def _fetch(url: str, timeout: float = 20.0) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "SALTY/1.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout, context=_CTX) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        raise WeatherError(f"Weather service returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise WeatherError(f"Could not reach the weather service: {exc}") from exc


def cape_band(value: Any) -> str | None:
    if value is None:
        return None
    for threshold, label in CAPE_BANDS:
        if value >= threshold:
            return label
    return "low"


def weather_hazards(lat: float, lon: float, days: int = 2) -> dict[str, Any]:
    """Air weather now, and any thunderstorm hours in the forecast window."""
    key = f"{lat:.3f},{lon:.3f},{days}"
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    url = f"{ENDPOINT}?" + urlencode({
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "hourly": "weather_code,cape,precipitation,wind_gusts_10m",
        "current": ("weather_code,cape,temperature_2m,relative_humidity_2m,"
                    "precipitation,wind_speed_10m,wind_gusts_10m"),
        "wind_speed_unit": "ms",
        "timezone": "auto",
        "forecast_days": max(1, min(int(days), 7)),
    })
    payload = _fetch(url)

    current = payload.get("current") or {}
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    codes = hourly.get("weather_code") or []
    capes = hourly.get("cape") or []
    gusts = hourly.get("wind_gusts_10m") or []
    rain = hourly.get("precipitation") or []

    thunderstorms = [
        {
            "time": times[i],
            "description": WMO.get(codes[i], "thunderstorm"),
            "capeJkg": capes[i] if i < len(capes) else None,
            "instability": cape_band(capes[i] if i < len(capes) else None),
            "gustMs": gusts[i] if i < len(gusts) else None,
        }
        for i, code in enumerate(codes)
        if code in THUNDER_CODES and i < len(times)
    ]

    numeric_gusts = [g for g in gusts if g is not None]
    numeric_rain = [r for r in rain if r is not None]

    result = {
        "source": SOURCE,
        "note": ("Air weather and thunderstorm risk from Open-Meteo, not from INCOIS. "
                 "INCOIS issues no lightning product, and IMD cyclone bulletins are not "
                 "machine readable - for a named cyclone, check the IMD bulletin directly."),
        "timezone": payload.get("timezone"),
        "position": {"latitude": lat, "longitude": lon},
        "now": {
            "description": WMO.get(current.get("weather_code"), "unknown"),
            "airTemperatureC": current.get("temperature_2m"),
            "humidityPercent": current.get("relative_humidity_2m"),
            "rainfallMm": current.get("precipitation"),
            "windMs": current.get("wind_speed_10m"),
            "gustMs": current.get("wind_gusts_10m"),
            "instability": cape_band(current.get("cape")),
        },
        "thunderstormHours": len(thunderstorms),
        "thunderstorms": thunderstorms[:6],
        "maxGustMs": round(max(numeric_gusts), 1) if numeric_gusts else None,
        "totalRainfallMm": round(sum(numeric_rain), 1) if numeric_rain else None,
        "lightningRisk": (
            "none forecast" if not thunderstorms
            else f"thunderstorms forecast on {len(thunderstorms)} hour(s)"
        ),
    }
    _cache[key] = (time.time(), result)
    return result
