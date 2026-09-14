"""Route checking and hazard summary, built only on already-verified sources.

Nothing new is fetched from anywhere. Both functions here are compositions:

  check_route     samples the INCOIS Ocean State Forecast along the track the
                  fisherman intends to run, scores each leg with the project's
                  own MarineRiskModel, and adds the EEZ boundary distance at
                  the far end plus the thunderstorm outlook.

  hazard_zones    answers "where should I not go" from the INCOIS High Wave
                  and Swell Surge advisories in force, the distance to the EEZ
                  boundary, and the thunderstorm hours ahead.

Why compose rather than find another dataset: there is no public feed of
Indian marine restricted areas or vessel-routing corridors. Inventing one on a
safety product is worse than saying so, so hazard_zones reports the hazards it
can actually read and states plainly that gazetted restricted areas are not
among them.

Route sampling is deliberately coarse - the default is five points including
both ends - because each point is a live NCSS request and a fisherman waiting
on an answer is the constraint that matters.
"""

from __future__ import annotations

import math
from typing import Any

from alerts_client import AlertsError, alerts_for
from pfz_client import PfzError, _bearing_deg, _distance_nm, compass, eez_distance
from prediction_models import MarineRiskModel
from thredds_client import ThreddsError, point_conditions
from weather_client import WeatherError, weather_hazards

RISK_PARAMETER = {
    "windSpeed": "wind",
    "waveHeight": "wave height",
    "swellHeight": "swell",
    "currentSpeed": "currents",
}


class RouteError(RuntimeError):
    """Raised when a route cannot be checked."""


def great_circle_points(lat1: float, lon1: float, lat2: float, lon2: float,
                        samples: int) -> list[tuple[float, float]]:
    """`samples` points along the great circle, including both ends."""
    samples = max(2, min(int(samples), 8))
    phi1, lambda1 = math.radians(lat1), math.radians(lon1)
    phi2, lambda2 = math.radians(lat2), math.radians(lon2)
    delta = 2 * math.asin(min(1.0, math.sqrt(
        math.sin((phi2 - phi1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin((lambda2 - lambda1) / 2) ** 2)))
    if delta == 0:
        return [(lat1, lon1)]
    points = []
    for step in range(samples):
        fraction = step / (samples - 1)
        a = math.sin((1 - fraction) * delta) / math.sin(delta)
        b = math.sin(fraction * delta) / math.sin(delta)
        x = a * math.cos(phi1) * math.cos(lambda1) + b * math.cos(phi2) * math.cos(lambda2)
        y = a * math.cos(phi1) * math.sin(lambda1) + b * math.cos(phi2) * math.sin(lambda2)
        z = a * math.sin(phi1) + b * math.sin(phi2)
        points.append((math.degrees(math.atan2(z, math.hypot(x, y))),
                       math.degrees(math.atan2(y, x))))
    return points


def _leg(lat: float, lon: float) -> dict[str, Any]:
    """Conditions and risk at one point on the track."""
    conditions = point_conditions(lat, lon)
    parameters = conditions.get("parameters") or {}
    features = {
        name: parameters[field]["value"]
        for field, name in RISK_PARAMETER.items()
        if isinstance(parameters.get(field), dict)
        and parameters[field].get("value") is not None
    }
    scored = MarineRiskModel().predict_row({
        "timestamp": conditions.get("timestamp"),
        "features": features,
        "missing_values": conditions.get("unavailable_parameters", []),
    })
    return {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "readAt": conditions.get("readAt"),
        "timestamp": conditions.get("timestamp"),
        "waveHeightM": (parameters.get("waveHeight") or {}).get("value"),
        "swellHeightM": (parameters.get("swellHeight") or {}).get("value"),
        "windSpeedMs": (parameters.get("windSpeed") or {}).get("value"),
        "currentSpeedMs": (parameters.get("currentSpeed") or {}).get("value"),
        "riskScore": scored["risk_score"],
        "riskLevel": scored["risk_level"],
        "missing": scored["missing_values"],
    }


def check_route(lat: float, lon: float, destination_lat: float,
                destination_lon: float, samples: int = 5) -> dict[str, Any]:
    """Score the whole track from here to there, leg by leg."""
    points = great_circle_points(lat, lon, destination_lat, destination_lon, samples)
    distance_nm = _distance_nm(lat, lon, destination_lat, destination_lon)
    bearing = _bearing_deg(lat, lon, destination_lat, destination_lon)

    legs: list[dict[str, Any]] = []
    failures: list[str] = []
    for point_lat, point_lon in points:
        try:
            legs.append(_leg(point_lat, point_lon))
        except (ThreddsError, TypeError, ValueError) as exc:
            failures.append(f"{point_lat:.2f},{point_lon:.2f}: {str(exc)[:80]}")

    if not legs:
        raise RouteError(
            "the INCOIS forecast returned nothing along this track: "
            + ("; ".join(failures) if failures else "no reason given"))

    scored = [leg for leg in legs if leg["riskScore"] is not None]
    worst = max(scored, key=lambda leg: leg["riskScore"]) if scored else None

    result: dict[str, Any] = {
        "source": "INCOIS Ocean State Forecast (THREDDS), scored with the SALTY risk model",
        "note": ("Conditions sampled at points along the straight track. It is a check "
                 "of the water on the way, not a navigational route: it knows nothing "
                 "about shoals, traffic, fuel or your boat."),
        "from": {"latitude": lat, "longitude": lon},
        "to": {"latitude": destination_lat, "longitude": destination_lon},
        "distanceNM": round(distance_nm, 1),
        "distanceKm": round(distance_nm * 1.852, 1),
        "bearingDegrees": round(bearing),
        "bearingText": compass(bearing),
        "legs": legs,
        "worstLeg": worst,
        "worstRiskLevel": worst["riskLevel"] if worst else None,
        "pointsNotRead": failures,
    }

    try:
        result["eezAtDestination"] = eez_distance(destination_lat, destination_lon)
    except (PfzError, TypeError, ValueError) as exc:
        result["eezAtDestination"] = {"status": "NOT AVAILABLE", "reason": str(exc)[:120]}

    try:
        hazards = weather_hazards(destination_lat, destination_lon, days=2)
        result["thunderstormOutlook"] = {
            "source": hazards["source"],
            "lightningRisk": hazards["lightningRisk"],
            "thunderstormHours": hazards["thunderstormHours"],
            "thunderstorms": hazards["thunderstorms"],
        }
    except (WeatherError, TypeError, ValueError, KeyError) as exc:
        result["thunderstormOutlook"] = {"status": "NOT AVAILABLE", "reason": str(exc)[:120]}

    return result


def hazard_zones(lat: float, lon: float, place: str | None = None,
                 state: str | None = None) -> dict[str, Any]:
    """What to keep clear of: advisories in force, the EEZ edge, storm hours."""
    hazards: list[dict[str, Any]] = []
    # "No hazards" and "could not check" must never look the same. Every
    # source that answers is counted; if none answered, the tool fails loudly
    # instead of returning a reassuring empty list.
    checked: list[str] = []
    unchecked: list[str] = []
    result: dict[str, Any] = {
        "source": ("INCOIS High Wave and Swell Surge advisories, INCOIS EEZ boundary, "
                   "Open-Meteo thunderstorm outlook"),
        "note": ("These are the hazards SALTY can actually read. India publishes no "
                 "machine-readable feed of gazetted restricted or no-fishing areas, "
                 "so those are NOT covered here and must not be implied - check the "
                 "state fisheries notice and the Coast Guard for those."),
        "position": {"latitude": lat, "longitude": lon},
        "hazards": hazards,
    }

    try:
        alerts = alerts_for(place, state)
        for alert in alerts.get("alerts") or []:
            hazards.append({
                "kind": (alert.get("kind") or "official advisory").lower(),
                "severity": (alert.get("colour") or "").lower() or None,
                "detail": alert.get("message") or alert.get("alert"),
                "area": alert.get("district") or place,
                "issuedOn": alert.get("issuedOn"),
                "source": "INCOIS " + (alert.get("kind") or "advisory"),
            })
        result["advisoryStatus"] = (
            f"{alerts.get('matchedCount', 0)} advisory(ies) in force for "
            f"{alerts.get('scope') or place or 'this district'}")
        result["advisoryIssuedFor"] = {
            "highWave": alerts.get("highWaveIssuedFor"),
            "swellSurge": alerts.get("swellSurgeIssuedFor"),
        }
        checked.append("INCOIS advisories")
    except (AlertsError, TypeError, ValueError) as exc:
        result["advisoryStatus"] = f"NOT AVAILABLE: {str(exc)[:120]}"
        unchecked.append("INCOIS advisories")

    try:
        eez = eez_distance(lat, lon)
        result["eez"] = eez
        if eez["distanceNM"] < 30:
            hazards.append({
                "kind": "maritime boundary",
                "severity": "orange" if eez["distanceNM"] < 10 else "yellow",
                "detail": (f"The edge of Indian waters is about {eez['distanceKm']} "
                           f"kilometres to the {eez['bearingText']}. Do not cross it."),
                "area": "India EEZ boundary",
                "source": "INCOIS GeoServer",
            })
        checked.append("EEZ boundary")
    except (PfzError, TypeError, ValueError) as exc:
        result["eez"] = {"status": "NOT AVAILABLE", "reason": str(exc)[:120]}
        unchecked.append("EEZ boundary")

    try:
        weather = weather_hazards(lat, lon, days=2)
        result["thunderstormOutlook"] = {
            "source": weather["source"],
            "lightningRisk": weather["lightningRisk"],
            "thunderstormHours": weather["thunderstormHours"],
            "thunderstorms": weather["thunderstorms"],
        }
        for storm in weather["thunderstorms"][:3]:
            hazards.append({
                "kind": "thunderstorm",
                "severity": "orange" if storm.get("instability") in ("strong", "extreme") else "yellow",
                "detail": (f"{storm['description']} forecast around {storm['time'][11:16]} "
                           f"on {storm['time'][:10]}."),
                "area": "over this stretch of water",
                "source": "Open-Meteo",
            })
        checked.append("thunderstorm outlook")
    except (WeatherError, TypeError, ValueError, KeyError) as exc:
        result["thunderstormOutlook"] = {"status": "NOT AVAILABLE", "reason": str(exc)[:120]}
        unchecked.append("thunderstorm outlook")

    if not checked:
        raise RouteError(
            "none of the hazard sources could be reached ("
            + ", ".join(unchecked) + "), so I cannot say whether this water is clear")

    result["sourcesChecked"] = checked
    result["sourcesNotChecked"] = unchecked
    result["hazardCount"] = len(hazards)
    if hazards:
        summary = f"{len(hazards)} hazard(s) to keep clear of."
    else:
        summary = "Nothing to keep clear of in what could be checked."
    if unchecked:
        summary += (" Could not check " + ", ".join(unchecked)
                    + ", so this is not the whole picture.")
    result["summary"] = summary
    return result
