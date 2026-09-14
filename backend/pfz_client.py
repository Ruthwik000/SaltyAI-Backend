"""INCOIS Potential Fishing Zone advisories (GeoServer WFS).

Provenance
----------
Captured from the official PFZ WebGIS at
https://www.incois.gov.in/DataInfo/MFASPFZ/index.html by reading the requests
that application itself makes. The map draws these layers as WMS tiles; the
same GeoServer also answers WFS, which returns the advisory geometry as
GeoJSON instead of a picture.

Verified 2026-09-08 against the live server:

    /geoserver/PFZ_Automation/ows?service=WFS&version=1.0.0
      &request=GetFeature&typeName=PFZ_Automation:pfzlines
      &outputFormat=application/json&BBOX=<minLon,minLat,maxLon,maxLat,EPSG:4326>

  -> FeatureCollection of MultiLineString in EPSG:4326, properties:
     {Year: 2026, Julian_day: "251", Sno: "047", UID: 2026251047,
      Length: 18.1, SECTORBOUN: 10}

  106 advisories nationally that day; 8 within the Visakhapatnam box.

Year + Julian_day identify the issue date, so a stale advisory can be detected
rather than presented as today's. Nothing here is derived or interpolated: the
lines are INCOIS's own, and the only computation is the distance from the
fisherman to them.
"""

from __future__ import annotations

import json
import math
import ssl
from datetime import date, datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WFS = "https://www.incois.gov.in/geoserver/PFZ_Automation/ows"
LAYER = "PFZ_Automation:pfzlines"
EARTH_RADIUS_NM = 3440.065

_CTX = ssl._create_unverified_context()


class PfzError(RuntimeError):
    """Raised when the PFZ advisory service cannot be read."""


def _fetch(url: str, timeout: float = 30.0) -> str:
    request = Request(url, headers={"User-Agent": "SALTY/1.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout, context=_CTX) as response:
            return response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        raise PfzError(f"INCOIS GeoServer returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise PfzError(f"Could not reach the INCOIS PFZ service: {exc}") from exc


def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


_POINTS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


def compass(degrees: float) -> str:
    return _POINTS[int((degrees + 22.5) % 360 // 45)]


def _vertices(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    """Flatten a (Multi)LineString into (lat, lon) pairs. GeoJSON is lon,lat."""
    kind = geometry.get("type")
    raw = geometry.get("coordinates") or []
    parts = raw if kind == "MultiLineString" else [raw]
    points: list[tuple[float, float]] = []
    for part in parts:
        for pair in part:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                points.append((float(pair[1]), float(pair[0])))
    return points


def julian_to_date(year: int, julian_day: int) -> date:
    return datetime.strptime(f"{year} {julian_day}", "%Y %j").date()


def fetch_advisories(lat: float, lon: float, box_degrees: float = 1.5) -> dict[str, Any]:
    """Today's PFZ advisory lines around a position, straight from INCOIS."""
    params = {
        "service": "WFS",
        "version": "1.0.0",
        "request": "GetFeature",
        "typeName": LAYER,
        "outputFormat": "application/json",
        "BBOX": (f"{lon - box_degrees},{lat - box_degrees},"
                 f"{lon + box_degrees},{lat + box_degrees},EPSG:4326"),
    }
    body = _fetch(f"{WFS}?{urlencode(params)}")
    if not body.lstrip().startswith("{"):
        raise PfzError("The PFZ service did not return GeoJSON (it may be busy)")
    try:
        collection = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PfzError("The PFZ service returned malformed GeoJSON") from exc
    return collection


def nearest_zones(lat: float, lon: float, limit: int = 3,
                  box_degrees: float = 1.5) -> dict[str, Any]:
    """The closest PFZ advisories to a position, with distance and bearing.

    Distance is measured to the nearest vertex of each advisory line. The lines
    carry ~450 vertices each, so vertex distance is within a few hundred metres
    of true perpendicular distance — well inside the advisory's own precision.
    """
    collection = fetch_advisories(lat, lon, box_degrees)
    features = collection.get("features") or []
    today = datetime.now(timezone.utc).date()

    zones = []
    issued_dates: set[str] = set()
    for feature in features:
        properties = feature.get("properties") or {}
        points = _vertices(feature.get("geometry") or {})
        if not points:
            continue
        best = min(points, key=lambda p: _distance_nm(lat, lon, p[0], p[1]))
        distance = _distance_nm(lat, lon, best[0], best[1])
        bearing = _bearing_deg(lat, lon, best[0], best[1])

        issued = None
        try:
            issued = julian_to_date(int(properties["Year"]), int(properties["Julian_day"]))
            issued_dates.add(issued.isoformat())
        except (KeyError, TypeError, ValueError):
            pass

        background = SECTOR_REFERENCE.get(properties.get("SECTORBOUN") or 0) or {}
        zones.append({
            "id": properties.get("Sno"),
            "sector": background.get("name"),
            "uid": properties.get("UID"),
            "issuedFor": issued.isoformat() if issued else None,
            "isToday": issued == today if issued else None,
            "lengthKm": round(float(properties.get("Length") or 0.0), 1),
            "sectorId": properties.get("SECTORBOUN"),
            "nearestPoint": {"latitude": round(best[0], 4), "longitude": round(best[1], 4)},
            "distanceNM": round(distance, 1),
            # Given in kilometres too. A working fisherman does not think in
            # nautical miles, and asking the model to convert invites it to do
            # arithmetic badly in the middle of a sentence.
            "distanceKm": round(distance * 1.852, 1),
            "bearingDeg": round(bearing, 1),
            "bearingText": compass(bearing),
        })

    zones.sort(key=lambda zone: zone["distanceNM"])

    nearest_sector = zones[0].get("sector") if zones else None
    background = next(
        (entry for entry in SECTOR_REFERENCE.values() if entry["name"] == nearest_sector),
        None,
    )

    return {
        "source": "INCOIS Potential Fishing Zone advisory (GeoServer WFS, PFZ_Automation:pfzlines)",
        "from": {"latitude": lat, "longitude": lon},
        "issuedFor": sorted(issued_dates),
        "advisoryIsForToday": bool(issued_dates) and all(d == today.isoformat() for d in issued_dates),
        "searchedWithinDegrees": box_degrees,
        "distanceUnits": "distanceNM is nautical miles, distanceKm is kilometres",
        "count": len(zones),
        "zones": zones[:limit],
        # Background about the coast, kept in its own block so it can never be
        # read as something INCOIS said about today.
        "sectorReference": None if not background else {
            "sector": background["name"],
            "usualSpecies": background["species"],
            "usualDepthBand": background["depthBand"],
            "note": ("Species usually landed on this coast. Not part of today's "
                     "INCOIS advisory and not a measurement of what is there now."),
        },
    }


# INCOIS sector ids (SECTORBOUN), from PFZ_Sectors:sector_new, with the species
# commonly landed on each stretch of coast and the depth band the shelf sits in.
#
# This is NOT part of the advisory. INCOIS publishes a line, a length, a sector
# and a date - never a species list. What follows is background about the coast,
# shipped with the app, and it must always be described that way: "usually caught
# here", never "there are tuna there today". It mirrors lib/pfz-reference.js on
# the frontend so the chat and the zone panel say the same thing.
SECTOR_REFERENCE: dict[int, dict[str, Any]] = {
    1:  {"name": "Gujarat", "species": ["Bombay Duck", "Indian Mackerel", "Ribbonfish", "Penaeid Prawns"], "depthBand": "20-70 m"},
    2:  {"name": "Daman & Diu", "species": ["Bombay Duck", "Ribbonfish", "Croaker"], "depthBand": "15-50 m"},
    3:  {"name": "Maharashtra", "species": ["Indian Mackerel", "Bombay Duck", "Pomfret", "Sardine"], "depthBand": "20-80 m"},
    4:  {"name": "Goa", "species": ["Sardine", "Indian Mackerel", "Seer Fish"], "depthBand": "20-60 m"},
    5:  {"name": "Karnataka", "species": ["Oil Sardine", "Indian Mackerel", "Seer Fish"], "depthBand": "20-70 m"},
    6:  {"name": "Kerala", "species": ["Oil Sardine", "Indian Mackerel", "Anchovy", "Tuna"], "depthBand": "25-90 m"},
    7:  {"name": "Tamil Nadu", "species": ["Seer Fish", "Tuna", "Carangids", "Sardine"], "depthBand": "30-100 m"},
    8:  {"name": "Puducherry", "species": ["Seer Fish", "Carangids", "Sardine"], "depthBand": "30-90 m"},
    9:  {"name": "Andhra Pradesh (south)", "species": ["Seer Fish", "Carangids", "Tuna", "Croaker"], "depthBand": "30-90 m"},
    10: {"name": "Andhra Pradesh", "species": ["Yellowfin Tuna", "Seer Fish", "Indian Mackerel", "Carangids"], "depthBand": "40-110 m"},
    11: {"name": "Odisha", "species": ["Hilsa", "Croaker", "Penaeid Prawns", "Indian Mackerel"], "depthBand": "20-70 m"},
    12: {"name": "West Bengal", "species": ["Hilsa", "Bombay Duck", "Penaeid Prawns"], "depthBand": "15-60 m"},
}

# ---------------------------------------------------------------------------
# Exclusive Economic Zone
# ---------------------------------------------------------------------------
# Same GeoServer, different workspace. The WFS prefix is PFZ_EEZ:indiaeez even
# though the WMS request for the identical layer uses PFZ_Automation:indiaeez;
# asking WFS for the WMS spelling returns an empty collection, not an error,
# which is how that trap stays hidden.
#
# A BBOX filter also comes back empty here, so the whole layer is fetched. It
# is 19 MultiLineString features, 1005 vertices, about 29 KB - verified live
# 2026-09-08 - which is small enough to hold for the process lifetime.

EEZ_WFS = "https://www.incois.gov.in/geoserver/PFZ_EEZ/ows"
EEZ_LAYER = "PFZ_EEZ:indiaeez"

_eez_cache: list[tuple[float, float]] | None = None


def eez_vertices() -> list[tuple[float, float]]:
    """Every vertex of India's EEZ boundary, as (lat, lon)."""
    global _eez_cache
    if _eez_cache is not None:
        return _eez_cache
    params = {
        "service": "WFS",
        "version": "1.0.0",
        "request": "GetFeature",
        "typeName": EEZ_LAYER,
        "outputFormat": "application/json",
    }
    body = _fetch(f"{EEZ_WFS}?{urlencode(params)}")
    if not body.lstrip().startswith("{"):
        raise PfzError("The EEZ service did not return GeoJSON (it may be busy)")
    try:
        collection = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PfzError("The EEZ service returned malformed GeoJSON") from exc
    points: list[tuple[float, float]] = []
    for feature in collection.get("features") or []:
        points.extend(_vertices(feature.get("geometry") or {}))
    if not points:
        raise PfzError("The EEZ layer came back empty")
    _eez_cache = points
    return points


def eez_distance(lat: float, lon: float) -> dict[str, Any]:
    """How far the user is from the edge of India's EEZ, and in which direction.

    Distance is to the nearest boundary vertex. The boundary is a coarse
    polyline, so this is an approximation and is reported as one: it answers
    "am I anywhere near the limit", never "you are 0.3 nm inside it".
    """
    points = eez_vertices()
    nearest = min(points, key=lambda p: _distance_nm(lat, lon, p[0], p[1]))
    distance_nm = _distance_nm(lat, lon, nearest[0], nearest[1])
    bearing = _bearing_deg(lat, lon, nearest[0], nearest[1])
    if distance_nm < 10:
        proximity = "at the boundary"
    elif distance_nm < 30:
        proximity = "close to the boundary"
    else:
        proximity = "well inside Indian waters"
    return {
        "source": "INCOIS GeoServer, India EEZ boundary (PFZ_EEZ:indiaeez)",
        "note": ("Distance to the nearest point of the published EEZ boundary line, "
                 "measured to the nearest vertex of a coarse polyline. Treat it as "
                 "approximate and never as a legal position fix."),
        "distanceNM": round(distance_nm, 1),
        "distanceKm": round(distance_nm * 1.852, 1),
        "bearingDegrees": round(bearing),
        "bearingText": compass(bearing),
        "proximity": proximity,
        "nearestPoint": {"latitude": round(nearest[0], 4),
                         "longitude": round(nearest[1], 4)},
    }
