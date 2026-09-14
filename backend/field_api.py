"""Live adapters behind the web console's fisherman and operations screens.

The console (ui2/lib/fisherman-api.js, ui2/lib/operations-api.js) calls these
first and only falls back to its bundled demo data when they fail. Everything
here is read off a real service - INCOIS THREDDS, PFZ WFS and advisories,
Open-Meteo - or comes from a real device (trip registrations and pings).

Fields a source does not publish are returned as null or empty, never filled
in: INCOIS PFZ advisories carry no suitability score, depth or species list,
so none is invented for them.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from typing import Any

from alerts_client import alerts_for
from pfz_client import compass, nearest_zones
from ports import PORTS
from route_client import check_route
from prediction_models import MarineRiskModel
from thredds_client import point_conditions, series_conditions
from tides_client import tide_forecast
import weather_client
from weather_client import weather_hazards

MS_TO_KNOTS = 1.94384

# Advisory colour -> the severity words the console colours by.
_SEVERITY = {"red": "Critical", "orange": "Severe", "yellow": "Warning", "green": "Advisory"}
_LEVEL = {"low": "Low", "moderate": "Moderate", "high": "High"}

# Last zones served, so a detail request can find the advisory line again.
_zones: dict[str, dict[str, Any]] = {}
_zones_lock = threading.Lock()


def _value(parameters: dict[str, Any], field: str) -> float | None:
    entry = parameters.get(field)
    return entry.get("value") if isinstance(entry, dict) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def nearest_port(lat: float, lon: float) -> tuple[str, str]:
    """Closest known harbour and its state, for the district-keyed advisory feed."""
    name, (_, _, state) = min(
        PORTS.items(), key=lambda item: _distance_nm(lat, lon, item[1][0], item[1][1])
    )
    return name.title(), state


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

def conditions(lat: float, lon: float) -> dict[str, Any]:
    """PointConditions: INCOIS sea state plus Open-Meteo air weather."""
    sea = point_conditions(lat, lon)
    parameters = sea.get("parameters") or {}
    wind = parameters.get("windSpeed") or {}
    result: dict[str, Any] = {
        "sst": _value(parameters, "seaSurfaceTemperature"),
        "chlorophyll": None,
        "waveHeight": _value(parameters, "waveHeight"),
        "wavePeriod": _value(parameters, "wavePeriod"),
        "swellHeight": _value(parameters, "swellHeight"),
        "currentSpeed": _value(parameters, "currentSpeed"),
        # INCOIS publishes current magnitude only at this endpoint.
        "currentDirection": None,
        "windSpeed": round(wind["value"] * MS_TO_KNOTS, 1) if wind.get("value") is not None else None,
        "windDirection": compass(wind["directionDeg"]) if wind.get("directionDeg") is not None else None,
        "airTemp": None,
        "humidity": None,
        "visibility": None,
        "pressure": None,
        "condition": None,
        "observedAt": sea.get("timestamp"),
        "readAt": sea.get("readAt"),
        "sources": [sea.get("source")],
        "unavailable": sea.get("unavailable_parameters", []),
    }
    try:
        air = weather_hazards(lat, lon, days=1)
        now = air.get("now") or {}
        result.update({
            "airTemp": now.get("airTemperatureC"),
            "humidity": now.get("humidityPercent"),
            "condition": now.get("description"),
            "lightningRisk": air.get("lightningRisk"),
        })
        result["sources"].append(air.get("source"))
    except Exception as exc:  # air weather is secondary; sea state still stands
        result["unavailable"].append(f"air weather: {str(exc)[:80]}")
    return result


# ---------------------------------------------------------------------------
# Potential Fishing Zones
# ---------------------------------------------------------------------------

def pfz_zones(lat: float, lon: float, limit: int = 10) -> list[dict[str, Any]]:
    """PfzZoneFeature[]: today's INCOIS PFZ advisory lines nearest the position."""
    data = nearest_zones(lat, lon, limit=limit, box_degrees=3.0)
    reference = data.get("sectorReference") or {}
    features = []
    for zone in data.get("zones") or []:
        point = zone.get("nearestPoint") or {}
        zone_id = f"pfz-{zone.get('uid') or zone.get('id')}"
        issued = zone.get("issuedFor")
        feature = {
            "id": zone_id,
            "name": f"INCOIS PFZ {zone.get('sector') or 'line'} #{zone.get('id')}",
            "lat": point.get("latitude"),
            "lon": point.get("longitude"),
            # Half the advisory line's length: the band a boat works along it.
            "radiusNM": round((zone.get("lengthKm") or 0.0) / 1.852 / 2, 1),
            "suitabilityScore": None,
            "suitabilityText": f"INCOIS advisory for {issued}" if issued else "INCOIS advisory",
            "distanceNM": zone.get("distanceNM"),
            "bearingDeg": zone.get("bearingDeg"),
            "bearing": zone.get("bearingText"),
            "depthMeters": None,
            "referencePort": None,
            "primarySpecies": [],
            "issuedFor": issued,
            "isToday": zone.get("isToday"),
            "lengthKm": zone.get("lengthKm"),
            "source": data.get("source"),
        }
        features.append(feature)
        with _zones_lock:
            _zones[zone_id] = {"feature": feature, "reference": reference}
    return features


def zone_detail(zone_id: str) -> dict[str, Any] | None:
    """ZoneDetail for a zone previously served by pfz_zones."""
    with _zones_lock:
        cached = _zones.get(zone_id)
    if not cached:
        return None
    zone, reference = cached["feature"], cached["reference"]
    species = [
        {"name": name, "abundance": "Usually landed on this coast", "depthRange": reference.get("usualDepthBand")}
        for name in reference.get("usualSpecies") or []
    ]
    notes = [
        f"Advisory line {zone['lengthKm']} km long, nearest point {zone['distanceNM']} NM {zone['bearing']}.",
        "INCOIS publishes the line, its date and sector; it does not publish species, depth or gear.",
    ]
    if species:
        notes.append(reference.get("note") or "Species listed are background for this coast, not part of the advisory.")
    return {
        "zoneId": zone["id"],
        "name": zone["name"],
        "lat": zone["lat"],
        "lon": zone["lon"],
        "radiusNM": zone["radiusNM"],
        "distanceNM": zone["distanceNM"],
        "bearing": zone["bearing"],
        "bearingDeg": zone["bearingDeg"],
        "depthMeters": None,
        "suitabilityScore": None,
        "suitabilityText": zone["suitabilityText"],
        "species": species,
        "conditions": conditions(zone["lat"], zone["lon"]),
        "recommendedGear": "Not published by INCOIS",
        "advisoryValidity": zone["issuedFor"] or "Unknown",
        "notes": notes,
        "source": zone["source"],
    }


# ---------------------------------------------------------------------------
# Advisories
# ---------------------------------------------------------------------------

def ocean_alerts(lat: float, lon: float) -> list[dict[str, Any]]:
    """OceanAlert[]: INCOIS high-wave and swell-surge advisories for the nearest coast."""
    port, state = nearest_port(lat, lon)
    data = alerts_for(None, state, limit=20)
    out = []
    for index, alert in enumerate(data.get("alerts") or []):
        kind = alert.get("kind") or "Advisory"
        district = alert.get("district") or state
        out.append({
            "id": f"incois-{kind.lower().replace(' ', '-')}-{district.lower().replace(' ', '-')}-{index}",
            "type": kind,
            "severity": _SEVERITY.get((alert.get("colour") or "").lower(), "Advisory"),
            "title": f"{kind} alert: {district}, {alert.get('state')}",
            "summary": alert.get("message") or alert.get("alert"),
            "action": alert.get("alert") or "Follow the INCOIS advisory before sailing.",
            "operationalAction": alert.get("alert"),
            "issuedAt": alert.get("issuedOn"),
            "expiresAt": None,
            "lat": None,
            "lon": None,
            "affectedRegions": [district],
            "source": f"Official {data.get('source')}",
        })
    return out


# ---------------------------------------------------------------------------
# Trip risk
# ---------------------------------------------------------------------------

def trip_risk(request: dict[str, Any]) -> dict[str, Any]:
    """TripRiskResult: the SALTY risk model over INCOIS conditions along the track."""
    lat, lon = float(request["departureLat"]), float(request["departureLon"])
    dest_lat, dest_lon = float(request["destinationLat"]), float(request["destinationLon"])
    route = check_route(lat, lon, dest_lat, dest_lon, samples=5)
    worst = route.get("worstLeg") or {}
    score = worst.get("riskScore")
    level = _LEVEL.get(str(route.get("worstRiskLevel") or "").lower(), "Unknown")
    storms = route.get("thunderstormOutlook") or {}

    def fmt(value: Any, unit: str) -> str:
        return f"{value} {unit}" if value is not None else "not available"

    factors = [
        {"name": "Sea state (worst leg)", "value": fmt(worst.get("waveHeightM"), "m"), "score": score},
        {"name": "Swell (worst leg)", "value": fmt(worst.get("swellHeightM"), "m"), "score": score},
        {"name": "Wind (worst leg)", "value": fmt(worst.get("windSpeedMs"), "m/s"), "score": score},
        {"name": "Crossing distance", "value": f"{route['distanceNM']} NM {route['bearingText']}", "score": None},
        {"name": "Thunderstorm hours (48 h)", "value": str(storms.get("thunderstormHours", "not available")), "score": None},
    ]

    precautions = []
    if storms.get("thunderstormHours"):
        precautions.append(f"Thunderstorms forecast for {storms['thunderstormHours']} h near the destination "
                           f"(lightning risk {storms.get('lightningRisk')}).")
    eez = route.get("eezAtDestination") or {}
    if isinstance(eez.get("distanceNM"), (int, float)) and eez["distanceNM"] < 10:
        precautions.append(f"The destination is {eez['distanceNM']} NM from the EEZ boundary.")
    if route.get("pointsNotRead"):
        precautions.append(f"{len(route['pointsNotRead'])} point(s) on the track could not be read from INCOIS.")
    precautions.append("Check the INCOIS bulletin and Coast Guard advisories on the morning you sail.")

    recommendations = (
        ["Conditions along the track are within the low-risk band."] if level == "Low"
        else ["Consider an earlier departure or a closer zone; the worst leg is not low risk."]
        if level in ("Moderate", "High") else
        ["The risk model could not score this track; do not treat it as safe."]
    )

    return {
        "score": score,
        "level": level,
        "summary": (f"{level} risk from {request.get('departurePort') or 'your position'} to "
                    f"{request.get('destinationZoneName') or 'the destination'}: "
                    f"{route['distanceNM']} NM {route['bearingText']}, worst leg scored {score}. "
                    f"{route['note']}"),
        "safeWindow": None,
        "factors": factors,
        "recommendations": recommendations,
        "precautions": precautions,
        "legs": route.get("legs"),
        "source": route.get("source"),
    }


# ---------------------------------------------------------------------------
# Trips and fleet (real device registrations, held in memory)
# ---------------------------------------------------------------------------

_trips: dict[str, dict[str, Any]] = {}
_trips_lock = threading.Lock()
# No ping for this long and the boat is flagged for the operator.
OVERDUE_AFTER_SECONDS = 30 * 60


def start_trip(request: dict[str, Any]) -> dict[str, Any]:
    trip_id = f"trip-{uuid.uuid4().hex[:10]}"
    trip = {
        "tripId": trip_id,
        "startedAt": _now(),
        "departurePort": request.get("departurePort"),
        "destinationZoneName": request.get("destinationZoneName"),
        "destinationZoneId": request.get("destinationZoneId"),
        "boatType": request.get("boatType"),
        "expectedReturnAt": request.get("expectedReturnAt"),
    }
    with _trips_lock:
        _trips[trip_id] = {
            **trip,
            "track": [],
            "lastPingAt": None,
            "lastPingEpoch": time.time(),
            "lat": request.get("departureLat"),
            "lon": request.get("departureLon"),
            "ended": False,
        }
    return trip


def ping_trip(trip_id: str, point: dict[str, Any]) -> bool:
    with _trips_lock:
        trip = _trips.get(trip_id)
        if not trip or trip["ended"]:
            return False
        position = {"lat": point.get("lat"), "lon": point.get("lon")}
        trip["track"] = (trip["track"] + [position])[-50:]
        trip.update({
            "lat": point.get("lat"),
            "lon": point.get("lon"),
            "speedKnots": point.get("speedKnots"),
            "headingDeg": point.get("headingDeg"),
            "lastPingAt": point.get("at") or _now(),
            "lastPingEpoch": time.time(),
        })
    return True


def end_trip(trip_id: str) -> bool:
    with _trips_lock:
        trip = _trips.get(trip_id)
        if not trip:
            return False
        trip["ended"] = True
    return True


def fleet(lat: float, lon: float) -> list[dict[str, Any]]:
    """TrackedFisherman[]: trips registered from the SALTY app and still at sea."""
    now = time.time()
    with _trips_lock:
        active = [dict(t) for t in _trips.values() if not t["ended"] and t.get("lat") is not None]
    units = []
    for trip in active:
        heading = trip.get("headingDeg")
        units.append({
            "id": trip["tripId"],
            "skipper": None,
            "boatName": trip.get("boatType") or "Registered trip",
            "regNumber": None,
            "vesselType": trip.get("boatType"),
            "crewCount": None,
            "homePort": trip.get("departurePort"),
            "mmsi": None,
            "lat": trip["lat"],
            "lon": trip["lon"],
            "headingDeg": heading,
            "headingText": compass(heading) if isinstance(heading, (int, float)) else None,
            "speedKnots": trip.get("speedKnots"),
            "lastPingAt": trip.get("lastPingAt") or trip["startedAt"],
            "distanceFromPortNM": None,
            "status": "overdue" if now - trip["lastPingEpoch"] > OVERDUE_AFTER_SECONDS else "underway",
            "destinationZoneId": trip.get("destinationZoneId"),
            "destinationZoneName": trip.get("destinationZoneName"),
            "destinationLat": None,
            "destinationLon": None,
            "distanceToZoneNM": None,
            "track": trip["track"],
        })
    units.sort(key=lambda unit: _distance_nm(lat, lon, unit["lat"], unit["lon"]))
    return units


# ---------------------------------------------------------------------------
# Search and rescue drift
# ---------------------------------------------------------------------------

# Leeway as a fraction of wind speed, by target (IAMSAR ordering).
_LEEWAY = {"piw": 0.011, "craft": 0.03, "trawler": 0.025, "raft": 0.04}


def sar_predict(request: dict[str, Any]) -> dict[str, Any]:
    """SarPrediction: downwind leeway from the live INCOIS wind at the last known position.

    INCOIS gives current magnitude but not direction at a point, so the current
    cannot set the drift bearing. Its speed instead widens the search radius, in
    every direction, rather than being assumed to run downwind.
    """
    lat, lon = float(request["lastKnownLat"]), float(request["lastKnownLon"])
    hours = max(float(request.get("elapsedHours") or 0.0), 0.0)
    sea = point_conditions(lat, lon)
    parameters = sea.get("parameters") or {}
    wind = parameters.get("windSpeed") or {}
    if wind.get("value") is None or wind.get("directionDeg") is None:
        raise ValueError("INCOIS returned no wind at the last known position; drift cannot be computed")

    wind_knots = wind["value"] * MS_TO_KNOTS
    current_ms = _value(parameters, "currentSpeed")
    current_knots = (current_ms or 0.0) * MS_TO_KNOTS
    leeway_knots = wind_knots * _LEEWAY.get(request.get("targetType") or "craft", 0.03)
    bearing = (wind["directionDeg"] + 180.0) % 360.0  # wind blows FROM directionDeg
    drift_nm = leeway_knots * hours

    radians = math.radians(bearing)

    def along(nm: float) -> dict[str, float]:
        d_lat = (nm / 60) * math.cos(radians)
        d_lon = (nm / 60) * math.sin(radians) / math.cos(math.radians(lat))
        return {"lat": round(lat + d_lat, 4), "lon": round(lon + d_lon, 4)}

    path = [{"lat": lat, "lon": lon}] + [along(drift_nm * i / 6) for i in range(1, 7)]
    datum = path[-1]
    radius = max(1.5, drift_nm * 0.3 + current_knots * hours)
    return {
        "datumLat": datum["lat"],
        "datumLon": datum["lon"],
        "driftDistanceNM": round(drift_nm, 1),
        "driftBearingDeg": round(bearing),
        "driftBearingText": compass(bearing),
        "searchRadiusNM": round(radius, 1),
        "searchAreaSqNM": round(math.pi * radius ** 2, 1),
        "windLeewayKnots": round(leeway_knots, 2),
        "currentKnots": round(current_knots, 2),
        "tideKnots": 0,
        "recommendedPattern": ("Parallel track search, 2 NM spacing" if radius > 8
                               else "Expanding square search from the datum"),
        "driftPath": path,
        "observedAt": sea.get("timestamp"),
        "notes": [
            "Leeway from the live INCOIS wind; current speed widens the radius because its direction is not published.",
            "Tidal stream is not modelled.",
        ],
        "source": sea.get("source"),
    }


# ---------------------------------------------------------------------------
# Hourly and 7-day forecast
# ---------------------------------------------------------------------------

def _score(wind_ms: float | None, wave: float | None, swell: float | None) -> dict[str, Any]:
    features = {"wind": wind_ms, "wave height": wave, "swell": swell}
    return MarineRiskModel().predict_row({"features": {k: v for k, v in features.items() if v is not None}})


def _air_forecast(lat: float, lon: float) -> dict[str, Any]:
    """Open-Meteo hourly and daily air weather in the location's own timezone."""
    return weather_client._fetch(f"{weather_client.ENDPOINT}?" + urlencode({
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": 7,
    }))


def forecast(lat: float, lon: float) -> dict[str, Any]:
    """Hourly (next 24 h) and daily (7 days) marine forecast.

    Sea state and the risk level are INCOIS 3-hourly forecast steps scored with
    the SALTY risk model; air temperature, rain probability and the sky
    condition are Open-Meteo. A field neither source returned stays null.
    """
    now = datetime.now(timezone.utc)
    stamp = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    sea = series_conditions(lat, lon, stamp(now - timedelta(hours=3)), stamp(now + timedelta(days=7)))
    sources = [sea.get("source")]
    unavailable: list[str] = []

    offset = timedelta(0)
    air_hourly: dict[str, dict[str, Any]] = {}
    air_daily: dict[str, dict[str, Any]] = {}
    try:
        air = _air_forecast(lat, lon)
        offset = timedelta(seconds=air.get("utc_offset_seconds") or 0)
        hourly = air.get("hourly") or {}
        for i, t in enumerate(hourly.get("time") or []):
            air_hourly[t[:13]] = {
                "temp": (hourly.get("temperature_2m") or [None])[i] if i < len(hourly.get("temperature_2m") or []) else None,
                "rain": (hourly.get("precipitation_probability") or [None])[i] if i < len(hourly.get("precipitation_probability") or []) else None,
            }
        daily = air.get("daily") or {}
        for i, d in enumerate(daily.get("time") or []):
            pick = lambda key: (daily.get(key) or [None] * (i + 1))[i]
            code = pick("weather_code")
            air_daily[d] = {
                "tempMax": pick("temperature_2m_max"),
                "tempMin": pick("temperature_2m_min"),
                "condition": weather_client.WMO.get(code, None) if isinstance(code, int) else None,
                "rainMax": pick("precipitation_probability_max"),
            }
        sources.append(weather_client.SOURCE)
    except Exception as exc:  # sea state still stands without air weather
        unavailable.append(f"air weather: {str(exc)[:80]}")

    local_now = now + offset
    hourly_out: list[dict[str, Any]] = []
    days: dict[str, dict[str, Any]] = {}
    for step in sea.get("steps") or []:
        if step.get("waveHeight") is None or not step.get("time"):
            continue
        moment = datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        local = moment + offset
        wind_ms = step.get("windSpeed")
        scored = _score(wind_ms, step.get("waveHeight"), step.get("swellHeight"))
        level = scored["risk_level"].capitalize()
        wind_kts = round(wind_ms * MS_TO_KNOTS) if wind_ms is not None else None

        if moment >= now - timedelta(hours=3) and moment <= now + timedelta(hours=24):
            air_hour = air_hourly.get(local.strftime("%Y-%m-%dT%H"), {})
            hourly_out.append({
                "time": step["time"],
                "hour": ("Now " if not hourly_out else "") + local.strftime("%H:%M"),
                "temp": air_hour.get("temp"),
                "wind": wind_kts,
                "windDirection": compass(step["windDirectionDeg"]) if step.get("windDirectionDeg") is not None else None,
                "wave": step["waveHeight"],
                "swell": step.get("swellHeight"),
                "period": step.get("wavePeriod"),
                "rain": air_hour.get("rain"),
                "riskScore": scored["risk_score"],
                "status": "Safe" if level == "Low" else level,
            })

        key = local.strftime("%Y-%m-%d")
        day = days.setdefault(key, {"waveMax": 0.0, "windMax": 0, "score": 0.0, "date": local})
        day["waveMax"] = max(day["waveMax"], step["waveHeight"])
        day["windMax"] = max(day["windMax"], wind_kts or 0)
        day["score"] = max(day["score"], scored["risk_score"])

    daily_out = []
    for index, (key, day) in enumerate(sorted(days.items())[:7]):
        label = "Today" if key == local_now.strftime("%Y-%m-%d") else (
            "Tomorrow" if key == (local_now + timedelta(days=1)).strftime("%Y-%m-%d")
            else day["date"].strftime("%a %d %b"))
        air_day = air_daily.get(key, {})
        score = day["score"]
        daily_out.append({
            "date": key,
            "day": label,
            "condition": air_day.get("condition") or "—",
            "tempMax": air_day.get("tempMax"),
            "tempMin": air_day.get("tempMin"),
            "rainMax": air_day.get("rainMax"),
            "windMax": day["windMax"],
            "waveMax": round(day["waveMax"], 2),
            "riskScore": score,
            "risk": "High" if score >= 70 else "Moderate" if score >= 35 else "Low",
        })

    tides = None
    try:
        tides = tide_forecast(lat, lon, days=2)
        sources.append(tides.get("source"))
    except Exception as exc:
        unavailable.append(f"tides: {str(exc)[:80]}")

    return {
        "hourly": hourly_out[:8],
        "daily": daily_out,
        "tides": tides,
        "readAt": sea.get("readAt"),
        "sources": [src for src in sources if src],
        "unavailable": unavailable,
    }
