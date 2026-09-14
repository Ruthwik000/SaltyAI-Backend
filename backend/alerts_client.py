"""INCOIS High Wave Alerts and Swell Surge Advisories.

Provenance
----------
Found by opening the official advisory page,
https://www.incois.gov.in/site/services/hwa.jsp, and reading the endpoints its
embedded map (site/services/Alerts.html) calls. It uses the INCOIS mobile-app
REST API:

    https://sarat.incois.gov.in/incoismobileappdata/rest/incois/hwassalatestdata

Verified 2026-09-08 against the live service:

    {"LatestHWADate": "20260908",
     "HWAJson": "[{\\"OBJECTID\\":267,\\"District\\":\\"THOOTHUKKUDI\\",
                   \\"STATE\\":\\"TAMIL NADU\\",\\"Alert\\":\\"HIGH WAVE WATCH\\",
                   \\"Color\\":\\"Yellow\\",\\"Issue Date\\":\\"08-09-2026\\",
                   \\"Message\\":\\"High Wave Watch for the coast of ...\\"}, ...]",
     "LatestSSADate": "20260908", "SSAJson": "[...]"}

14 high-wave alerts and 64 swell-surge advisories that day. Note both *Json
fields arrive as JSON *strings* that need a second parse.

That API has no tide endpoint. Names are echoed by a catch-all handler that
answers "Hello World RESTful Jersey '<name>'" with HTTP 200, while a real
endpoint answers 202 with JSON — so a missing service is easy to tell from a
working one, and every tide name tried hit the catch-all.
"""

from __future__ import annotations

import gzip
import json
import ssl
import time
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://sarat.incois.gov.in/incoismobileappdata/rest/incois/hwassalatestdata"

# INCOIS colour codes, in the order a fisherman should care about them.
SEVERITY = {"red": 4, "orange": 3, "yellow": 2, "green": 1, "none": 0}

_CACHE_TTL = 900.0
_cache: dict[str, Any] = {}
_cache_at = 0.0
_CTX = ssl._create_unverified_context()


class AlertsError(RuntimeError):
    """Raised when the INCOIS advisory service cannot be read."""


def _fetch(url: str, timeout: float = 25.0) -> str:
    request = Request(url, headers={"User-Agent": "SALTY/1.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout, context=_CTX) as response:
            body = response.read()
    except HTTPError as exc:
        raise AlertsError(f"INCOIS advisories returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AlertsError(f"Could not reach the INCOIS advisory service: {exc}") from exc
    # The service gzips its body whether or not it was asked to.
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body.decode("utf-8", "replace")


def _inner(payload: Any) -> list[dict[str, Any]]:
    """The *Json fields are JSON encoded inside JSON."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, str) and payload.strip():
        try:
            return _inner(json.loads(payload))
        except json.JSONDecodeError:
            return []
    return []


def _normalise(row: dict[str, Any], kind: str, issued: str | None) -> dict[str, Any]:
    colour = str(row.get("Color") or "none").strip()
    return {
        "kind": kind,
        "alert": (row.get("Alert") or "").strip(),
        "colour": colour,
        "severity": SEVERITY.get(colour.lower(), 0),
        "district": (row.get("District") or "").strip(),
        "state": (row.get("STATE") or "").strip(),
        "issuedOn": (row.get("Issue Date") or "").strip() or issued,
        "message": (row.get("Message") or "").strip(),
    }


def fetch_alerts(force: bool = False) -> dict[str, Any]:
    """All current INCOIS high-wave and swell-surge advisories."""
    global _cache, _cache_at
    if _cache and not force and time.time() - _cache_at < _CACHE_TTL:
        return _cache

    try:
        payload = json.loads(_fetch(ENDPOINT))
    except json.JSONDecodeError as exc:
        raise AlertsError(f"INCOIS advisories returned unreadable JSON: {exc}") from exc
    alerts = [_normalise(row, "High wave", payload.get("LatestHWADate"))
              for row in _inner(payload.get("HWAJson"))]
    alerts += [_normalise(row, "Swell surge", payload.get("LatestSSADate"))
               for row in _inner(payload.get("SSAJson"))]
    alerts.sort(key=lambda a: (-a["severity"], a["state"], a["district"]))

    def as_date(stamp: str | None) -> str | None:
        try:
            return datetime.strptime(str(stamp), "%Y%m%d").date().isoformat()
        except (TypeError, ValueError):
            return None

    _cache = {
        "source": "INCOIS High Wave Alert / Swell Surge Advisory",
        "highWaveIssuedFor": as_date(payload.get("LatestHWADate")),
        "swellSurgeIssuedFor": as_date(payload.get("LatestSSADate")),
        "count": len(alerts),
        "alerts": alerts,
    }
    _cache_at = time.time()
    return _cache


def alerts_for(place: str | None = None, state: str | None = None,
               limit: int = 6) -> dict[str, Any]:
    """Advisories relevant to a district or state, most severe first.

    Matching is by the district and state names INCOIS itself publishes. If a
    place matches nothing, that is reported as "no advisory for your district"
    rather than silently returning another coast's warning.
    """
    everything = fetch_alerts()
    alerts = everything["alerts"]

    matched = alerts
    scope = "all coasts"
    if place or state:
        needles = [n.strip().lower() for n in (place, state) if n and n.strip()]
        matched = [
            alert for alert in alerts
            if any(
                needle in alert["district"].lower() or alert["district"].lower() in needle
                or needle in alert["state"].lower() or alert["state"].lower() in needle
                for needle in needles
            )
        ]
        scope = place or state or "all coasts"

    return {
        "source": everything["source"],
        "highWaveIssuedFor": everything["highWaveIssuedFor"],
        "swellSurgeIssuedFor": everything["swellSurgeIssuedFor"],
        "scope": scope,
        "matchedCount": len(matched),
        "totalNationwide": everything["count"],
        "alerts": matched[:limit],
        "note": (
            "No high-wave or swell-surge advisory is in force for this district."
            if not matched else None
        ),
    }
