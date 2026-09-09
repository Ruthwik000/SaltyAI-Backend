"""Groq tool-calling bridge for the SALTY data layer.

The model is an interpreter and response writer only. All numerical answers
must come from an ERDDAP tool result; absent data is reported as unavailable.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from erddap_client import ERDDAPClient, ERDDAPError, table_records
from prediction_models import build_predictions
from risk_features import build_72h_feature_dataset
from alerts_client import AlertsError, alerts_for
from pfz_client import PfzError, nearest_zones
from thredds_client import ThreddsError, point_conditions, series_conditions
from tides_client import TideError, tide_forecast
from weather_client import WeatherError, weather_hazards
from ocean_color_client import (OceanColorError, productivity_patches,
                                productivity_trend)
from route_client import RouteError, check_route, hazard_zones
from weather_forecast import VISAKHAPATNAM_BBOX, load_datasets


# A number followed by a physical unit. Used to catch an answer that quotes
# measurements when no tool actually returned any — the model reciting
# climatology from memory. Prompting alone did not reliably stop this, and on
# a safety product it must not depend on the model choosing to comply.
# Prior turns kept per request. Enough for a real follow-up, small enough
# that a long session does not blow up the prompt.
MAX_HISTORY_TURNS = 8


_MEASUREMENT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:°\s*[CF]|℃|℉|deg\s*[CF]|kts?\b|knots?\b|km\s*/?\s*h|"
    r"m\s*/\s*s|mps\b|metres?\b|meters?\b|m\b|%|hPa|mbar\b|mb\b|mm\b)",
    re.IGNORECASE,
)


# Only an actual go/no-go question forces the safety tool. This used to match
# "sea conditions" and "weather conditions" too, so "how is the weather today"
# and "what are the sea conditions near me" both came back as a risk score.
# Earlier still it matched the bare word "fishing".
# The old note follows:
# Only an actual go/no-go question should bypass the model for the grounded
# safety answer. The previous test was a bare word list containing "fishing",
# "boat" and "risk", so "what fishing zones are near me" — a question about
# where to fish — came back as a marine-risk score.
_SAFETY_INTENT = re.compile(
    r"\b(?:is|are|will)\s+(?:it|the\s+\w+|conditions)\s+\w*\s*safe"
    r"|\bsafe\s+(?:to|for)\s+\w+"
    r"|\bsafe\s+(?:today|tomorrow|now|tonight)\b"
    r"|\b(?:should|can|shall)\s+i\s+(?:go|sail|venture|head)\b"
    r"|\bgo\s+out\s+(?:today|tomorrow|now|tonight)\b"
    r"|\brisk\s+(?:score|level|of\s+going)\b"
    r"|\b(?:worsen|deteriorat\w+)\b"
    r"|\bsafe\s+to\s+sail\b",
    re.IGNORECASE,
)


class _RateLimited(Exception):
    """Groq said 429. It also said how long to wait, which is worth keeping."""

    def __init__(self, detail: str, retry_after: float):
        super().__init__(detail)
        self.detail = detail
        self.retry_after = retry_after


def _retry_after_seconds(detail: str, headers: Any = None, default: float = 5.0) -> float:
    """How long Groq wants us to wait, from its own message or its headers."""
    match = re.search(r"try again in ([\d.]+)\s*s", detail or "", re.IGNORECASE)
    if match:
        try:
            return float(match.group(1)) + 0.5
        except ValueError:
            pass
    for header in ("retry-after", "x-ratelimit-reset-tokens"):
        raw = None
        try:
            raw = headers.get(header) if headers else None
        except Exception:
            raw = None
        if raw:
            value = re.match(r"([\d.]+)", str(raw))
            if value:
                try:
                    return float(value.group(1)) + 0.5
                except ValueError:
                    pass
    return default


class GroqError(RuntimeError):
    """Base exception for Groq API and tool-calling errors."""
    pass


class ERDDAPTools:
    """Allowlist of data tools exposed to Groq."""

    def __init__(self, client: ERDDAPClient, latitude: float | None = None,
                 longitude: float | None = None, place: str | None = None,
                 state: str | None = None):
        self.client = client
        # The console always knows which port the user picked. Carrying it here
        # means a weather question does not depend on the model remembering to
        # put coordinates in the tool call — which it does not reliably do.
        self.latitude = latitude
        self.longitude = longitude
        self.place = place
        self.state = state

    def search_datasets(self, query: str) -> dict[str, Any]:
        terms = str(query).lower().split()
        try:
            datasets = self.client.list_datasets()
        except ERDDAPError:
            return {"query": query, "datasets": "NOT AVAILABLE"}
        return {
            "query": query,
            "datasets": [
                dataset
                for dataset in datasets
                if all(term in f"{dataset['dataset_id']} {dataset['title']}".lower() for term in terms)
            ],
        }

    def get_dataset_metadata(self, dataset_id: str) -> dict[str, Any]:
        try:
            return self.client.get_dataset_metadata(dataset_id)
        except ERDDAPError:
            return {"dataset_id": dataset_id, "status": "NOT AVAILABLE"}

    @staticmethod
    def _normalized(response: dict[str, Any]) -> dict[str, Any]:
        # No synthetic branch: the client cannot manufacture rows any more, so
        # anything arriving here came off the wire.
        return {"records": table_records(response)}

    def get_point_data(self, dataset_id: str, variable: str, time: str, latitude: float, longitude: float) -> dict[str, Any]:
        return self._normalized(self.client.get_point_data(dataset_id, variable, time, latitude, longitude))

    def get_region_data(self, dataset_id: str, variable: str, bbox: list[float], time_range: list[str]) -> dict[str, Any]:
        return self._normalized(self.client.get_region_data(dataset_id, variable, bbox, tuple(time_range)))

    def get_time_series(self, dataset_id: str, variable: str, bbox: list[float], start: str, end: str) -> dict[str, Any]:
        return self._normalized(self.client.get_time_series(dataset_id, variable, bbox, start, end))

    def get_forecast(self, dataset_id: str, variables: list[str], bbox: list[float], start: str, end: str) -> dict[str, Any]:
        return self._normalized(self.client.get_forecast(dataset_id, variables, bbox, start, end))

    # THREDDS layer key -> the parameter name MarineRiskModel scores.
    # The model's thresholds (wind 12/24, wave 2/4, swell 1.5/3, current 0.8/1.5)
    # are already in m/s and m, which is exactly what these OSF layers report.
    _RISK_PARAMETER = {
        "windSpeed": "wind",
        "waveHeight": "wave height",
        "swellHeight": "swell",
        "currentSpeed": "currents",
    }

    def get_marine_safety_forecast(self, bbox: list[float] | None = None) -> dict[str, Any]:
        """Score sailing conditions from the live INCOIS Ocean State Forecast.

        This used to read ERDDAP, which is an archive of past satellite passes
        and contains no forecast, so it could only ever answer NOT AVAILABLE.
        The forecast is on THREDDS; the risk model itself is unchanged.
        """
        region = tuple(bbox or VISAKHAPATNAM_BBOX)
        latitude = (region[0] + region[1]) / 2
        longitude = (region[2] + region[3]) / 2
        try:
            conditions = point_conditions(latitude, longitude)
        except (ThreddsError, TypeError, ValueError) as exc:
            return {
                "status": "NOT AVAILABLE",
                "reason": str(exc)[:200],
                "marine_risk": [],
                "fishing_window": {"status": "NOT AVAILABLE"},
            }

        features: dict[str, Any] = {}
        units: dict[str, str] = {}
        sources: dict[str, str] = {}
        timestamp = None
        for key, parameter in self._RISK_PARAMETER.items():
            entry = conditions["parameters"].get(key)
            if isinstance(entry, dict) and entry.get("value") is not None:
                features[parameter] = entry["value"]
                units[parameter] = entry.get("unit", "")
                sources[parameter] = entry.get("dataset", "")
                timestamp = timestamp or entry.get("timestamp")

        if not features:
            return {
                "status": "NOT AVAILABLE",
                "reason": "the INCOIS forecast returned no values at this position",
                "unavailable_parameters": conditions.get("unavailable_parameters", []),
                "marine_risk": [],
                "fishing_window": {"status": "NOT AVAILABLE"},
            }

        row = {
            "timestamp": timestamp or "the latest forecast step",
            "latitude": latitude,
            "longitude": longitude,
            "features": features,
            "missing_values": conditions.get("unavailable_parameters", []),
            "units": units,
            "source_dataset": sources,
        }
        result = build_predictions({
            "records": [row],
            "feature_units": units,
            "source_datasets": sources,
            "unavailable_parameters": conditions.get("unavailable_parameters", []),
            "forecast_timestamps": [row["timestamp"]],
        })
        result["source"] = conditions["source"]
        result["position"] = conditions.get("readAt") or {
            "latitude": latitude,
            "longitude": longitude,
        }
        result["requested"] = conditions.get("requested")
        result["observed"] = {
            parameter: {"value": value, "unit": units.get(parameter, "")}
            for parameter, value in features.items()
        }
        # A go/no-go answer that ignores lightning is not a safety answer, and
        # the INCOIS ocean model carries no thunderstorm signal at all. This
        # comes from Open-Meteo and says so. Best effort: if the weather
        # service is down the sea-state verdict still stands on its own.
        try:
            hazards = weather_hazards(latitude, longitude, days=2)
            result["thunderstormOutlook"] = {
                "source": hazards["source"],
                "lightningRisk": hazards["lightningRisk"],
                "thunderstormHours": hazards["thunderstormHours"],
                "thunderstorms": hazards["thunderstorms"],
                "maxGustMs": hazards["maxGustMs"],
            }
        except (WeatherError, TypeError, ValueError, KeyError) as exc:
            result["thunderstormOutlook"] = {
                "status": "NOT AVAILABLE", "reason": str(exc)[:120]}
        return result

    def get_current_conditions(self, latitude: float | None = None,
                               longitude: float | None = None) -> dict[str, Any]:
        """Live sea state at one position, from the INCOIS Ocean State Forecast.

        This is the plain "what is it like out there right now" tool. Without
        it the model had no choice but to reach for get_marine_safety_forecast
        for every weather question, because the other tools all require a
        dataset id it cannot know.

        Reads THREDDS, not ERDDAP: ERDDAP is an archive of past satellite
        passes and holds no forecast at all.
        """
        lat = latitude if latitude is not None else self.latitude
        lon = longitude if longitude is not None else self.longitude
        if lat is None or lon is None:
            return {
                "status": "NOT AVAILABLE",
                "reason": "no position available for this request",
                "parameters": {},
            }
        try:
            return point_conditions(float(lat), float(lon))
        except (ThreddsError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200], "parameters": {}}

    def get_forecast_window(self, start_hours_from_now: float = 0.0,
                            end_hours_from_now: float = 24.0,
                            latitude: float | None = None,
                            longitude: float | None = None) -> dict[str, Any]:
        """Forecast steps over a future window — for "tomorrow morning" style questions."""
        lat = latitude if latitude is not None else self.latitude
        lon = longitude if longitude is not None else self.longitude
        if lat is None or lon is None:
            return {"status": "NOT AVAILABLE", "reason": "no position available", "steps": []}
        try:
            now = datetime.now(timezone.utc)
            start = now + timedelta(hours=float(start_hours_from_now))
            end = now + timedelta(hours=float(end_hours_from_now))
            if end <= start:
                end = start + timedelta(hours=3)
            stamp = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%SZ")
            return series_conditions(float(lat), float(lon), stamp(start), stamp(end))
        except (ThreddsError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200], "steps": []}

    def get_nearest_fishing_zones(self, latitude: float | None = None,
                                  longitude: float | None = None,
                                  limit: int = 3) -> dict[str, Any]:
        """Today's INCOIS PFZ advisories nearest the user, with distance and bearing."""
        lat = latitude if latitude is not None else self.latitude
        lon = longitude if longitude is not None else self.longitude
        if lat is None or lon is None:
            return {"status": "NOT AVAILABLE", "reason": "no position available", "zones": []}
        try:
            return nearest_zones(float(lat), float(lon), limit=int(limit))
        except (PfzError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200], "zones": []}

    def get_marine_alerts(self, place: str | None = None,
                          state: str | None = None) -> dict[str, Any]:
        """INCOIS high-wave and swell-surge advisories in force, most severe first."""
        try:
            return alerts_for(place or self.place, state or self.state)
        except (AlertsError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200], "alerts": []}

    def get_tides(self, latitude: float | None = None,
                  longitude: float | None = None, days: int = 2) -> dict[str, Any]:
        """High and low water at the user's location, in local time."""
        lat = latitude if latitude is not None else self.latitude
        lon = longitude if longitude is not None else self.longitude
        if lat is None or lon is None:
            return {"status": "NOT AVAILABLE", "reason": "no position available",
                    "turningPoints": []}
        try:
            return tide_forecast(float(lat), float(lon), days=int(days))
        except (TideError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200],
                    "turningPoints": []}

    def get_weather_hazards(self, latitude: float | None = None,
                            longitude: float | None = None, days: int = 2) -> dict[str, Any]:
        """Thunderstorm and lightning outlook, and air weather, at the user's location.

        INCOIS is an ocean model: it carries no lightning product and no air
        temperature. Open-Meteo fills exactly that gap, and every result names
        it as the source.
        """
        lat = latitude if latitude is not None else self.latitude
        lon = longitude if longitude is not None else self.longitude
        if lat is None or lon is None:
            return {"status": "NOT AVAILABLE", "reason": "no position available",
                    "thunderstorms": []}
        try:
            return weather_hazards(float(lat), float(lon), days=int(days))
        except (WeatherError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200],
                    "thunderstorms": []}

    def get_ocean_productivity(self, latitude: float | None = None,
                               longitude: float | None = None,
                               limit: int = 5) -> dict[str, Any]:
        """Where the water nearby is carrying the most food, from satellite.

        INCOIS ERDDAP's newest chlorophyll pixel is from 2020, and its own
        ocean-colour archive stops in 2006, so this reads NOAA CoastWatch. It
        is the raw signal, not a PFZ advisory, and it says so.
        """
        lat = latitude if latitude is not None else self.latitude
        lon = longitude if longitude is not None else self.longitude
        if lat is None or lon is None:
            return {"status": "NOT AVAILABLE", "reason": "no position available",
                    "patches": []}
        try:
            return productivity_patches(float(lat), float(lon), limit=int(limit))
        except (OceanColorError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200],
                    "patches": []}

    def get_productivity_trend(self, latitude: float | None = None,
                               longitude: float | None = None,
                               days: int = 60) -> dict[str, Any]:
        """How chlorophyll and sea temperature here have moved over recent weeks."""
        lat = latitude if latitude is not None else self.latitude
        lon = longitude if longitude is not None else self.longitude
        if lat is None or lon is None:
            return {"status": "NOT AVAILABLE", "reason": "no position available"}
        try:
            return productivity_trend(float(lat), float(lon), days=int(days))
        except (OceanColorError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200]}

    def check_route(self, destination_latitude: float,
                    destination_longitude: float,
                    latitude: float | None = None,
                    longitude: float | None = None,
                    samples: int = 5) -> dict[str, Any]:
        """Conditions along the track from here to a destination."""
        lat = latitude if latitude is not None else self.latitude
        lon = longitude if longitude is not None else self.longitude
        if lat is None or lon is None:
            return {"status": "NOT AVAILABLE", "reason": "no starting position available",
                    "legs": []}
        try:
            return check_route(float(lat), float(lon), float(destination_latitude),
                               float(destination_longitude), samples=int(samples))
        except (RouteError, ThreddsError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200], "legs": []}

    def get_hazard_zones(self, latitude: float | None = None,
                         longitude: float | None = None,
                         place: str | None = None,
                         state: str | None = None) -> dict[str, Any]:
        """Water to keep clear of: advisories in force, the EEZ edge, storm hours."""
        lat = latitude if latitude is not None else self.latitude
        lon = longitude if longitude is not None else self.longitude
        if lat is None or lon is None:
            return {"status": "NOT AVAILABLE", "reason": "no position available",
                    "hazards": []}
        try:
            return hazard_zones(float(lat), float(lon),
                                place or self.place, state or self.state)
        except (RouteError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc)[:200], "hazards": []}

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        functions: dict[str, Callable[..., dict[str, Any]]] = {
            "search_datasets": self.search_datasets,
            "get_dataset_metadata": self.get_dataset_metadata,
            "get_point_data": self.get_point_data,
            "get_region_data": self.get_region_data,
            "get_time_series": self.get_time_series,
            "get_forecast": self.get_forecast,
            "get_current_conditions": self.get_current_conditions,
            "get_tides": self.get_tides,
            "get_weather_hazards": self.get_weather_hazards,
            "get_ocean_productivity": self.get_ocean_productivity,
            "get_productivity_trend": self.get_productivity_trend,
            "check_route": self.check_route,
            "get_hazard_zones": self.get_hazard_zones,
            "get_marine_alerts": self.get_marine_alerts,
            "get_nearest_fishing_zones": self.get_nearest_fishing_zones,
            "get_forecast_window": self.get_forecast_window,
            "get_marine_safety_forecast": self.get_marine_safety_forecast,
        }
        if name not in functions:
            # Telling the model it guessed wrong lets it correct itself on the
            # next round. Raising here threw away the whole request - and every
            # tool result already gathered - over one hallucinated name.
            return {
                "status": "NOT AVAILABLE",
                "reason": f"There is no tool called {name}. Available tools: "
                          + ", ".join(sorted(functions)),
            }
        try:
            return functions[name](**arguments)
        except TypeError as exc:
            # Wrong or missing arguments. Same reasoning: report it to the model
            # rather than failing the request.
            return {"status": "NOT AVAILABLE",
                    "reason": f"{name} was called with arguments it does not accept: {exc}"}


# The six ERDDAP catalogue tools are for research questions: find a dataset,
# read its metadata, pull a series. A fisherman on a phone never needs them,
# and their schemas cost about 580 tokens on every round of every call.
RESEARCH_ONLY_TOOLS = {
    "search_datasets", "get_dataset_metadata", "get_point_data",
    "get_region_data", "get_time_series", "get_forecast",
}


# One line per tool for the phone and the fisherman console.
#
# The full descriptions are written for a research console, where the model has
# to choose between seventeen tools and the difference between two of them is
# subtle. On a phone call there are eleven, the questions are plainer, and the
# long text costs about a thousand tokens on EVERY round against a per-minute
# budget. These keep the words that decide the routing and drop the rest.
COMPACT_DESCRIPTIONS = {
    "get_current_conditions":
        "Sea and weather right now: waves, swell, wind, current, water temperature. "
        "The default for any question about how the sea is. No arguments uses the user's place.",
    "get_forecast_window":
        "Sea conditions later today, tonight, tomorrow or the next few days.",
    "get_marine_safety_forecast":
        "Go or no-go verdict for putting to sea. Only for an explicit 'is it safe' question.",
    "get_tides":
        "High and low water times. Any question about the tide or when the water turns. "
        "Open-Meteo, not INCOIS - say so.",
    "get_weather_hazards":
        "Lightning, thunderstorms, rain, air temperature, gusts. Any question about storms. "
        "Open-Meteo, not INCOIS - say so.",
    "get_marine_alerts":
        "Official INCOIS high-wave and swell-surge warnings in force. "
        "Any question about warnings, alerts, cyclones or danger.",
    "get_nearest_fishing_zones":
        "Today's INCOIS fishing zone advisories with distance in kilometres and a bearing. "
        "Also returns the fish usually caught on that coast, so use it for questions about fish.",
    "get_ocean_productivity":
        "Where the water is richest, from satellite chlorophyll and sea temperature. "
        "Use when there is no fishing advisory today. NOAA satellite, one to three days old.",
    "get_productivity_trend":
        "How chlorophyll and sea temperature here have changed over recent weeks. "
        "Use for 'why is the catch poor'. NOAA satellite.",
    "check_route":
        "Sea conditions along the way to somewhere, leg by leg, plus how close that is to "
        "the edge of Indian waters. Needs the destination latitude and longitude.",
    "get_hazard_zones":
        "What to keep clear of: warnings in force, how close the edge of Indian waters is, "
        "and storm hours. Does NOT cover gazetted restricted areas.",
}


def _compact(tool: dict[str, Any]) -> dict[str, Any]:
    """The same tool, described in one line, with parameter prose dropped."""
    function = tool["function"]
    short = COMPACT_DESCRIPTIONS.get(function["name"])
    if not short:
        return tool
    parameters = json.loads(json.dumps(function.get("parameters", {})))
    for spec in (parameters.get("properties") or {}).values():
        # The types and the required list carry the contract. The prose beside
        # them is for a human reading the schema, and is paid for every round.
        spec.pop("description", None)
    return {
        "type": "function",
        "function": {
            "name": function["name"],
            "description": short,
            "parameters": parameters,
        },
    }


def tools_for(mode: str) -> list[dict[str, Any]]:
    """The tool schemas worth sending for this mode.

    Every schema is re-sent on every round, so this is the difference between
    fitting inside a tokens-per-minute limit and not. Research keeps the full
    text, because there the model is choosing between seventeen tools and the
    distinctions are fine. Nothing a mode could actually use is hidden.
    """
    if mode == "research":
        return TOOL_DEFINITIONS
    return [_compact(t) for t in TOOL_DEFINITIONS
            if t["function"]["name"] not in RESEARCH_ONLY_TOOLS]


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_conditions",
            "description": (
                "Current sea and weather conditions at a latitude/longitude, read live from the "
                "INCOIS Ocean State Forecast: significant wave height, swell, wind speed, wave "
                "period, surface current and sea surface temperature. Call this for ANY question "
                "about the weather, the sea, or conditions right now or today at a place. This is "
                "the default tool for weather questions; only use get_marine_safety_forecast when "
                "the user specifically asks whether it is safe to go out. Call it with no "
                "arguments to use the location the user has selected in the app."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number", "description": "Decimal degrees north. Omit to use the location the user selected in the app."},
                    "longitude": {"type": "number", "description": "Decimal degrees east. Omit to use the location the user selected in the app."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tides",
            "description": (
                "High and low water times at the user's location, in local time, with the "
                "predicted height of each. Call this for any question about tides, high "
                "water, low water, or when the water turns. Call with no arguments to use "
                "the user's location. This is an Open-Meteo tide prediction, not an INCOIS "
                "product, and the answer must say so."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "days": {"type": "number", "description": "Days ahead, 1 to 5 (default 2)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_hazards",
            "description": (
                "Thunderstorm and lightning outlook, plus air weather - air temperature, "
                "humidity, rainfall and wind gusts - at the user's location. Call this for "
                "any question about lightning, thunder, thunderstorms, rain, or how hot or "
                "humid it is. For a cyclone question call this AND get_marine_alerts: this "
                "tool gives the storm outlook, get_marine_alerts gives the official INCOIS "
                "advisory in force. Call with no arguments to use the user's location. The "
                "source is Open-Meteo, not INCOIS, and the answer must say so."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "days": {"type": "number", "description": "Days ahead, 1 to 7 (default 2)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ocean_productivity",
            "description": (
                "Satellite chlorophyll and sea surface temperature around the user, "
                "returning the patches of water carrying the most food, each with its "
                "distance and compass bearing. Call this when the user asks where the "
                "water is rich or productive, about chlorophyll, plankton, blooms, "
                "upwelling, or favourable sea temperature, and as a SECOND source when "
                "get_nearest_fishing_zones has no advisory for today. The official "
                "product is the INCOIS PFZ advisory: call that first. This is the raw "
                "satellite signal from NOAA CoastWatch, it is one to three days old, "
                "and the answer must say so. It shows food in the water, never fish."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "limit": {"type": "number", "description": "How many patches, 1 to 8 (default 5)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_productivity_trend",
            "description": (
                "How chlorophyll and sea surface temperature at the user's location "
                "have changed over recent weeks, with the sea temperature anomaly "
                "against the long-term baseline. Call this when the user asks why the "
                "catch or fish productivity has fallen or risen, whether the water has "
                "warmed, or what has changed this season. Source is NOAA CoastWatch "
                "satellite. It explains the water, not fishing effort, and the answer "
                "must not present it as the only cause."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "days": {"type": "number", "description": "Weeks to look back over, in days, 14 to 180 (default 60)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_route",
            "description": (
                "Sea conditions along the track from the user's position to somewhere "
                "they intend to go, sampled at points on the way and scored leg by leg, "
                "with the worst leg named. Call this whenever the user asks about going "
                "TO a place, the safest way out, whether a passage is safe, or which "
                "direction to head. It needs the destination as latitude and longitude: "
                "if the user names a place instead, ask for the position, or use the "
                "coordinates the app has for it. It also reports how close the "
                "destination is to the edge of Indian waters. It checks the water only, "
                "not shoals, traffic or fuel, and the answer must say so."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "destination_latitude": {"type": "number", "description": "Decimal degrees north of where the user wants to go."},
                    "destination_longitude": {"type": "number", "description": "Decimal degrees east of where the user wants to go."},
                    "latitude": {"type": "number", "description": "Starting point. Omit to start from the user's location."},
                    "longitude": {"type": "number", "description": "Starting point. Omit to start from the user's location."},
                    "samples": {"type": "number", "description": "Points to check along the way, 2 to 8 (default 5)."},
                },
                "required": ["destination_latitude", "destination_longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hazard_zones",
            "description": (
                "Water to keep clear of around the user: the INCOIS advisories in "
                "force, how close the edge of Indian waters is and in which direction, "
                "and any thunderstorm hours forecast. Call this when the user asks "
                "which areas to avoid, where it is dangerous, about restricted or "
                "no-go zones, or about crossing the maritime boundary. India publishes "
                "no machine-readable list of gazetted restricted or no-fishing areas, "
                "so this does NOT cover those and the answer must say so and point the "
                "user at the state fisheries notice. Call with no arguments to use the "
                "user's location."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "place": {"type": "string", "description": "District or port name."},
                    "state": {"type": "string", "description": "Coastal state name."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_marine_alerts",
            "description": (
                "Official INCOIS High Wave Alerts and Swell Surge Advisories currently in "
                "force, for the user's district by default. Call this for any question about "
                "warnings, alerts, hazards, cyclones, storms, or whether it is dangerous. "
                "Each carries a colour code (Yellow watch, Orange warning, Red alert) and the "
                "official message. Call with no arguments to use the user's location."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "place": {"type": "string", "description": "District or port name."},
                    "state": {"type": "string", "description": "Coastal state name."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nearest_fishing_zones",
            "description": (
                "Today's INCOIS Potential Fishing Zone (PFZ) advisories nearest the user, "
                "each with distance in nautical miles and a compass bearing. Call this for "
                "any question about where to fish, fishing zones, PFZ, or where the catch "
                "is likely to be good. Call with no arguments to use the user's location. "
                "The result also carries sectorReference: the species usually landed on that "
                "coast, so call this for any question about what fish are around too."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "limit": {"type": "number", "description": "How many zones to return (default 3)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_forecast_window",
            "description": (
                "Forecast for a FUTURE time window at the user's location, in 3-hourly steps: "
                "wave height, swell, wave period and wind. Use this for any question about later "
                "today, tonight, tomorrow morning, or the next few days. Hours are counted from "
                "now, so tomorrow morning is roughly start 18 to end 30."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_hours_from_now": {"type": "number", "description": "Window start, hours from now."},
                    "end_hours_from_now": {"type": "number", "description": "Window end, hours from now."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_datasets",
            "description": "Search the ERDDAP dataset catalog.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query keywords"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dataset_metadata",
            "description": "Get metadata and variables for one ERDDAP dataset.",
            "parameters": {
                "type": "object",
                "properties": {"dataset_id": {"type": "string", "description": "Unique dataset identifier"}},
                "required": ["dataset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_point_data",
            "description": "Get one value at a specific time and lat/lon coordinate point.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "variable": {"type": "string"},
                    "time": {"type": "string"},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                },
                "required": ["dataset_id", "variable", "time", "latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_region_data",
            "description": "Get a spatial region for a time range. bbox is [min_lat, max_lat, min_lon, max_lon].",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "variable": {"type": "string"},
                    "bbox": {"type": "array", "items": {"type": "number"}},
                    "time_range": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["dataset_id", "variable", "bbox", "time_range"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time_series",
            "description": "Get a time series over a spatial region.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "variable": {"type": "string"},
                    "bbox": {"type": "array", "items": {"type": "number"}},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
                "required": ["dataset_id", "variable", "bbox", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_forecast",
            "description": "Get several variables over the same forecast window and region.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "variables": {"type": "array", "items": {"type": "string"}},
                    "bbox": {"type": "array", "items": {"type": "number"}},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
                "required": ["dataset_id", "variables", "bbox", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_marine_safety_forecast",
            "description": "Get a real 72-hour marine forecast and risk assessment for sailing, boating, fishing, departure planning, sea state, wind, waves, swell, currents, rainfall, and hazards. Use this whenever the user asks whether conditions are safe or suitable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Optional min_lat,max_lat,min_lon,max_lon; defaults to Visakhapatnam.",
                    }
                },
                "required": [],
            },
        },
    },
]


class GroqAgent:
    """Production Groq agent for the SALTY data layer with OpenAI-compatible tool calling."""

    def __init__(
        self,
        tools: ERDDAPTools,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.tools = tools
        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        self.base_url = (base_url or os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")).rstrip("/")
        # One key, or several.
        #
        # Groq's rate limit is per ORGANISATION, not per key, so a second key
        # cut from the same account shares the same tokens-per-minute budget
        # and buys nothing. Keys from DIFFERENT accounts - one per teammate,
        # say - are separate budgets, and rotating to the next one on a 429 is
        # instant where waiting is twenty seconds of silence on a live call.
        #
        # GROQ_API_KEYS takes a comma-separated list; GROQ_API_KEY still works
        # on its own.
        listed = [k.strip() for k in os.getenv("GROQ_API_KEYS", "").split(",") if k.strip()]
        single = api_key or os.getenv("GROQ_API_KEY", "")
        self.api_keys = listed or ([single] if single else [])
        self._key_index = 0
        self.timeout = timeout

    def _chat(self, messages: list[dict[str, Any]], include_tools: bool = True,
              mode: str = "normal") -> dict[str, Any]:
        """One chat completion, retrying when Groq asks us to wait."""
        if not self.api_keys:
            raise GroqError("GROQ_API_KEY environment variable is not configured")

        payload_data: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            # Completion tokens count against the same per-minute budget, and an
            # uncapped model will spend hundreds of them on an answer that has
            # to be two spoken sentences.
            "max_tokens": self._MAX_COMPLETION.get(mode, 700),
        }
        if include_tools:
            # Only the schemas this mode can use. They are re-sent on every
            # round, so a phone call carrying six unusable research tools pays
            # for them again and again against a per-minute token budget.
            payload_data["tools"] = tools_for(mode)
            payload_data["tool_choice"] = "auto"

        payload = json.dumps(payload_data).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_keys[self._key_index]}",
            # Without this urllib sends "Python-urllib/3.x", which some
            # edge/WAF configurations reject outright with a 403.
            "User-Agent": "SALTY/1.0",
        }
        # A free Groq tier allows a few thousand tokens per minute, and one
        # marine answer is a decent slice of that. When the limit is hit Groq
        # replies 429 AND says exactly how long to wait ("try again in 7.995s").
        # Failing the whole request while holding that number is throwing away
        # the answer, and on a phone call it is the difference between a pause
        # and a dropped call. Note the limit is per ORGANISATION, so a fresh
        # API key does not reset it.
        # Rotations and waits are counted separately. Folding them into one
        # loop counter meant trying a second key silently spent a retry, and
        # the request then died with a fallthrough message instead of saying
        # the rate limit was hit on every key.
        waits = 0
        keys_tried = 0
        while True:
            headers["Authorization"] = f"Bearer {self.api_keys[self._key_index]}"
            try:
                return self._send(payload, headers)
            except _RateLimited as limited:
                keys_tried += 1
                # Another key first, if one has not been tried on this request.
                # Switching accounts is free; waiting is not, and twenty
                # seconds of silence on a phone call is a dropped call.
                if keys_tried < len(self.api_keys):
                    self._key_index = (self._key_index + 1) % len(self.api_keys)
                    print(f"  Groq rate limit: switching to key "
                          f"{self._key_index + 1} of {len(self.api_keys)}",
                          file=sys.stderr, flush=True)
                    continue
                if waits >= self._RATE_LIMIT_RETRIES:
                    raise GroqError(
                        f"Groq rate limit reached on all {len(self.api_keys)} key(s), "
                        f"and still limited after waiting {waits} time(s). "
                        f"{limited.detail}"
                    ) from None
                wait = min(limited.retry_after, self._RATE_LIMIT_MAX_WAIT)
                print(f"  Groq rate limit: waiting {wait:.1f}s and retrying",
                      file=sys.stderr, flush=True)
                time.sleep(wait)
                waits += 1
                keys_tried = 0

    _RATE_LIMIT_RETRIES = 2
    _RATE_LIMIT_MAX_WAIT = 20.0

    # A phone answer is one or two sentences; a research answer carries tables.
    _MAX_COMPLETION = {"voice": 300, "normal": 500, "research": 2000}

    def _send(self, payload: bytes, headers: dict[str, str]) -> dict[str, Any]:
        request = Request(f"{self.base_url}/chat/completions", data=payload, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                choices = data.get("choices") or []
                if choices:
                    return {"message": choices[0].get("message", {})}
                return {"message": {}}
        except HTTPError as exc:
            # Groq explains every rejection in the response body ("invalid
            # api key", "model not found", "org restricted"). urllib's str()
            # shows only "HTTP Error 403: Forbidden", which says nothing, so
            # read the body and pass the real reason on.
            try:
                detail = exc.read().decode("utf-8", "replace").strip()[:400]
            except Exception:
                detail = ""
            if exc.code == 429:
                raise _RateLimited(detail, _retry_after_seconds(detail, exc.headers)) from exc
            raise GroqError(
                f"Groq API returned HTTP {exc.code} for model {self.model}: "
                f"{detail or exc.reason}"
            ) from exc
        except (OSError, URLError, json.JSONDecodeError) as exc:
            raise GroqError(f"Groq API is unavailable at {self.base_url}: {exc}") from exc

    @staticmethod
    def _grounded_safety_response(data: dict[str, Any]) -> str:
        if data.get("input_status") != "AVAILABLE":
            reason = data.get("reason") or ", ".join(data.get("unavailable_parameters", []))
            suffix = f" ({reason})" if reason else ""
            return f"The live marine forecast is unavailable right now{suffix}. I cannot make a current sailing-safety determination without forecast data."

        risks = data.get("marine_risk", [])
        window = data.get("fishing_window", {}).get("best_window") or (risks[0] if risks else {})
        if not window:
            return "The forecast service returned no usable marine conditions, so I cannot make a current sailing-safety determination."

        level = window.get("risk_level", "unknown")
        score = window.get("risk_score", "unavailable")
        timestamp = window.get("timestamp", "the forecast period")
        missing = window.get("missing_values", [])
        response = f"The live INCOIS forecast indicates {level} marine risk at {timestamp} (risk score {score}/100)."

        observed = data.get("observed") or {}
        if observed:
            readings = ", ".join(
                f"{parameter} {entry['value']}{(' ' + entry['unit']) if entry.get('unit') else ''}"
                for parameter, entry in observed.items()
            )
            response += f" Measured now: {readings}."
        if missing:
            response += f" Not available: {', '.join(map(str, missing))}."
        response += " Check the latest INCOIS bulletin before departure and follow local maritime warnings."
        return response

    # Where each tool's numbers actually came from, as a page a researcher can
    # open. A "Sources: get_time_series" chip tells them which function ran; it
    # does not let them check the data, which is the whole point of citing it.
    _SOURCE_PAGES: dict[str, tuple[str, str]] = {
        "get_current_conditions": (
            "INCOIS Ocean State Forecast",
            "https://www.incois.gov.in/oceanservices/osfforecast.jsp"),
        "get_forecast_window": (
            "INCOIS Ocean State Forecast",
            "https://www.incois.gov.in/oceanservices/osfforecast.jsp"),
        "get_marine_safety_forecast": (
            "INCOIS Ocean State Forecast",
            "https://www.incois.gov.in/oceanservices/osfforecast.jsp"),
        "check_route": (
            "INCOIS Ocean State Forecast",
            "https://www.incois.gov.in/oceanservices/osfforecast.jsp"),
        "get_nearest_fishing_zones": (
            "INCOIS Potential Fishing Zone advisories",
            "https://www.incois.gov.in/MarineFisheries/PfzWebGis"),
        "get_marine_alerts": (
            "INCOIS High Wave Alerts and Swell Surge Advisories",
            "https://www.incois.gov.in/site/services/hwa.jsp"),
        "get_hazard_zones": (
            "INCOIS High Wave Alerts and Swell Surge Advisories",
            "https://www.incois.gov.in/site/services/hwa.jsp"),
        "get_tides": (
            "Open-Meteo Marine, sea level above mean sea level",
            "https://open-meteo.com/en/docs/marine-weather-api"),
        "get_weather_hazards": (
            "Open-Meteo Forecast, WMO weather code and CAPE",
            "https://open-meteo.com/en/docs"),
        "get_ocean_productivity": (
            "NOAA CoastWatch VIIRS gap-filled chlorophyll-a, 9 km daily",
            "https://coastwatch.noaa.gov/erddap/info/noaacwNPPN20VIIRSDINEOFDaily/index.html"),
        "get_productivity_trend": (
            "NOAA CoastWatch CoralTemp SST and SST anomaly, 5 km daily",
            "https://coastwatch.noaa.gov/erddap/info/noaacrwsstDaily/index.html"),
        "search_datasets": (
            "INCOIS ERDDAP catalogue",
            "https://erddap.incois.gov.in/erddap/info/index.html"),
    }

    # ERDDAP tools name their dataset in the call arguments; that dataset has a
    # page of its own, which is the one worth linking.
    _ERDDAP_TOOLS = {
        "get_dataset_metadata", "get_point_data", "get_region_data",
        "get_time_series", "get_forecast",
    }
    _ERDDAP_INFO = "https://erddap.incois.gov.in/erddap/info/{dataset}/index.html"

    @classmethod
    def _references(cls, calls: list[dict[str, Any]],
                    tool_results: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Openable pages for the tools that actually returned data.

        Only tools whose result was usable are cited. A call that came back
        empty is not a source, and listing it makes an unanswered question look
        like it consulted a dataset.
        """
        usable = {
            item.get("tool")
            for item in tool_results
            if item.get("usable")
        }
        references: list[dict[str, str]] = []
        seen: set[str] = set()

        for call in calls:
            name = call.get("tool")
            if name not in usable:
                continue
            arguments = call.get("arguments") or {}

            if name in cls._ERDDAP_TOOLS:
                dataset = str(arguments.get("dataset_id") or "").strip()
                if dataset:
                    url = cls._ERDDAP_INFO.format(dataset=dataset)
                    title = f"INCOIS ERDDAP: {dataset}"
                else:
                    title, url = cls._SOURCE_PAGES["search_datasets"]
            else:
                entry = cls._SOURCE_PAGES.get(name)
                if not entry:
                    continue
                title, url = entry

            if url in seen:
                continue
            seen.add(url)
            references.append({"title": title, "url": url, "tool": name})

        return references

    # Fields the model never reads, and which dominate the prompt when they
    # are there. A single PFZ advisory carries a MultiLineString of 120-odd
    # coordinate pairs: 2,600 tokens of numbers the model cannot use, re-sent
    # on every subsequent round of the same turn. The map needs that geometry;
    # the language model does not.
    _PROMPT_NOISE = {"geometry", "coordinates", "series", "records", "legs"}
    _PROMPT_LIST_CAP = 6
    _PROMPT_CHAR_CAP = 3000

    @classmethod
    def _for_model(cls, value: Any, depth: int = 0) -> Any:
        """A tool result with the bulk taken out, for putting in the prompt.

        The full result still goes to the caller in returned_data - this only
        trims the copy the model sees. Rate limits are counted in tokens per
        minute, and a turn that re-sends a coordinate list three times spends
        its whole budget on numbers nobody reads.
        """
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if key in cls._PROMPT_NOISE and isinstance(item, (list, dict)):
                    length = len(item)
                    if key in {"series", "records"} and length:
                        # A series matters, but the model needs its shape and
                        # its ends, not every point.
                        kept = list(item)[: cls._PROMPT_LIST_CAP]
                        out[key] = cls._for_model(kept, depth + 1)
                        if length > len(kept):
                            out[f"{key}Note"] = f"{length} points total, first {len(kept)} shown"
                    else:
                        out[key] = f"<{length} items omitted: not needed to answer>"
                    continue
                out[key] = cls._for_model(item, depth + 1)
            return out
        if isinstance(value, list):
            kept = [cls._for_model(item, depth + 1) for item in value[: cls._PROMPT_LIST_CAP]]
            if len(value) > cls._PROMPT_LIST_CAP:
                kept.append(f"<{len(value) - cls._PROMPT_LIST_CAP} more omitted>")
            return kept
        return value

    @classmethod
    def _tool_message_content(cls, data: Any) -> str:
        """The trimmed result as JSON, hard-capped so one tool cannot eat the budget."""
        text = json.dumps(cls._for_model(data), default=str)
        if len(text) > cls._PROMPT_CHAR_CAP:
            text = text[: cls._PROMPT_CHAR_CAP] + '..."<truncated>"}'
        return text

    @staticmethod
    def _result_usable(data: Any) -> bool:
        """Did this one tool call actually come back with data?

        A call that was made but returned nothing is not a source. The UI
        listed every attempted call under "Sources", which made an empty
        result look like a consulted dataset.
        """
        if not isinstance(data, dict):
            return False
        if data.get("status") == "NOT AVAILABLE":
            return False
        if data.get("input_status") not in (None, "AVAILABLE"):
            return False
        if data.get("datasets") == "NOT AVAILABLE":
            return False
        if "records" in data and not data["records"]:
            return False
        if "marine_risk" in data and not data["marine_risk"]:
            return False
        if "steps" in data and not data["steps"]:
            return False
        if "zones" in data and not data["zones"]:
            return False
        if "patches" in data and not data["patches"]:
            return False
        if "legs" in data and not data["legs"]:
            return False
        if "turningPoints" in data and not data["turningPoints"]:
            return False
        # An empty thunderstorm list IS an answer ("none forecast"), so the
        # list being empty must not disqualify it. What disqualifies it is
        # Open-Meteo returning a shell with nothing measured in it.
        if "lightningRisk" in data and not any(
            value is not None for value in (data.get("now") or {}).values()
        ):
            return False
        if "alerts" in data and data.get("status") == "NOT AVAILABLE":
            return False
        if "parameters" in data and not any(
            isinstance(entry, dict) and entry.get("value") is not None
            for entry in data["parameters"].values()
        ):
            return False
        return True

    @classmethod
    def _tool_data_usable(cls, tool_results: list[dict[str, Any]]) -> bool:
        """True only if some tool actually returned data to reason from."""
        return any(cls._result_usable(item.get("data")) for item in tool_results)

    @staticmethod
    def _no_data_message(tool_results: list[dict[str, Any]]) -> str:
        missing: list[str] = []
        for item in tool_results:
            data = item.get("data")
            if isinstance(data, dict):
                missing.extend(data.get("unavailable_parameters") or [])
        detail = f" Missing: {', '.join(sorted(set(missing)))}." if missing else ""
        return (
            "I could not retrieve live marine data for this location, so I cannot tell you "
            "the current conditions." + detail + " I will not quote typical or seasonal "
            "figures instead, because out on the water a remembered number looks exactly "
            "like a measurement. Check the INCOIS ocean state bulletin or the local Coast "
            "Guard advisory before you go out."
        )

    def _render_in_language(self, text: str, user_query: str, language: str) -> str:
        """Say `text` in the user's language without changing any of its facts."""
        if not language or language.strip().lower() in {"english", "en"}:
            return text
        messages = [
            {
                "role": "system",
                "content": (
                    "Rewrite the MESSAGE below in "
                    f"{language}, answering the user's question directly. Use ONLY the "
                    "facts in the message: do not add, remove or change any value, time, "
                    "score or warning, and do not add conditions it does not state. Keep "
                    "every number and unit exactly as written. Reply with the rewritten "
                    "message and nothing else."
                ),
            },
            {"role": "user", "content": f"Question: {user_query}\n\nMESSAGE: {text}"},
        ]
        try:
            message = self._chat(messages, include_tools=False).get("message", {})
            rendered = (message.get("content") or "").strip()
        except GroqError:
            return text
        return rendered or text


    def answer(
        self,
        user_query: str,
        trace: bool = True,
        mode: str = "normal",
        context: str = "",
        bbox: list[float] | None = None,
        language: str = "English",
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        # What SALTY can and cannot answer, stated plainly so the model says
        # "I do not have that" instead of reaching for the nearest tool or, worse,
        # its own memory.
        # The long coverage statement below is written for a researcher who can
        # reach seventeen tools and needs to know the edges of each. A fisherman
        # on a phone reaches eleven and asks plainer questions, and every word
        # of it is re-sent on every round against a per-minute token budget.
        # This says the same things that change an answer, in a fifth of the space.
        compact_coverage = (
            " SOURCES: sea state, waves, wind and water temperature are the live"
            " INCOIS forecast. Fishing zones and warnings are official INCOIS."
            " Tides, lightning and storms are Open-Meteo, and rich-water"
            " chlorophyll is NOAA satellite - for those two say the name, not"
            " INCOIS.\n"
            " YOU DO NOT HAVE: market prices, and restricted or banned fishing"
            " areas - India publishes no feed of those, so say so and send them"
            " to the fisheries notice. For a named cyclone give the warnings in"
            " force and the storm outlook, then say the bulletin comes from IMD."
            " Never invent one.\n"
            " FISH: get_nearest_fishing_zones also returns the fish usually landed"
            " on that coast. Use it, and say it that way - what is usually caught"
            " there, never what is in the water today."
        )

        coverage = (
            " DATA YOU HAVE: sea state from the live INCOIS Ocean State Forecast -"
            " wave height, swell, wave period, wind speed and direction, surface"
            " current, sea surface temperature - for now and for future steps up to"
            " about a week ahead. Also the INCOIS ERDDAP archive of past satellite"
            " observations (SST, chlorophyll, winds) for research questions. And today's"
            " INCOIS Potential Fishing Zone (PFZ) advisories with distance and bearing,"
            " and the official INCOIS High Wave and Swell Surge advisories in force.\n"
            " Tide times come from Open-Meteo, not INCOIS: give them when asked, and"
            " say where they come from.\n"
            " Thunderstorm and lightning risk, air temperature, humidity, rainfall and"
            " wind gusts come from get_weather_hazards, which is Open-Meteo and not"
            " INCOIS - INCOIS publishes no lightning product. Use that tool for any"
            " question about lightning, thunder, storms or rain, and name Open-Meteo.\n"
            " Satellite chlorophyll and sea surface temperature come from"
            " get_ocean_productivity, and their trend over recent weeks from"
            " get_productivity_trend. Both are NOAA CoastWatch, not INCOIS - INCOIS"
            " stopped publishing chlorophyll in 2020 - and they are one to three days"
            " old. Use them for where the water is rich, and for why a season has been"
            " poor. Chlorophyll is food in the water, never a count of fish.\n"
            " check_route scores the sea along a track to a destination, and"
            " get_hazard_zones lists what to keep clear of: advisories in force, how"
            " close the edge of Indian waters is, and thunderstorm hours.\n"
            " DATA YOU DO NOT HAVE, and must say so plainly if asked:"
            " market prices, and gazetted restricted or no-fishing areas - India"
            " publishes no machine-readable feed of those, so point the user at the"
            " state fisheries notice and the Coast Guard. check_route checks the water"
            " on the way; it is not navigation and knows nothing about shoals, traffic"
            " or fuel."
            " For a NAMED cyclone you have no bulletin: give the INCOIS High Wave and"
            " Swell Surge advisories in force plus the thunderstorm outlook, then say"
            " the named-cyclone bulletin has to come from IMD. Never invent one.\n"
            " SPECIES: get_nearest_fishing_zones also returns sectorReference, the fish"
            " usually landed on that stretch of coast. Use it. Answer a question about"
            " fish with what is usually caught there, phrased that way: 'the fish"
            " usually caught off this coast are ...'. Never reply that you have no"
            " information about fish. Equally, never say those fish are there today or"
            " that anyone has counted them: it is background about the coast, not an"
            " observation of what is in the water now."
        )

        if mode == "research":
            voice = (
                "You are answering a marine researcher who will check your working."
                " Be precise, quantitative and structured. Rules:\n"
                "- Open with the finding in one or two sentences, then the evidence.\n"
                "- USE A MARKDOWN TABLE whenever you report three or more values, or"
                " compare parameters, positions or times. The console renders tables"
                " properly, and a time series or a set of positions in a table is"
                " drawn as a chart underneath it. Prose full of numbers is not.\n"
                "- Table columns: put the label or timestamp FIRST and the numeric"
                " columns after it, one measure per column, units in the header"
                " ('Wave height (m)'). That shape is what gets plotted.\n"
                "- Every value carries its unit, its timestamp, and the dataset it came"
                " from. Name datasets by their real id - the ERDDAP dataset_id, or the"
                " INCOIS product - never as 'the model'.\n"
                "- Use nautical miles for distance, the standard marine unit at sea,"
                " and give the kilometre value alongside where a figure is operational.\n"
                "- State the relation behind any derived quantity, in symbols:"
                " 'wind speed = hypot(UWND, VWND)', 'anomaly = SST - 1985-2012 baseline'.\n"
                "- Say the grid resolution and the sampling interval when they bound"
                " the conclusion, and say where the reading was actually taken if the"
                " tool reported a readAt different from the position requested.\n"
                "- Close with LIMITS: missing parameters, the age of the observation,"
                " and what the data cannot settle. A correlation is not a cause, and"
                " satellite chlorophyll is food in the water, not a count of fish.\n"
                "- Headings are welcome. Never pad: no restating the question, no"
                " summary of what you are about to say."
            )
        elif mode == "voice":
            # A phone call, not a screen. The answer is handed straight to a
            # speech engine and comes out of a handset held next to an engine,
            # so it has to survive being heard once, with no chance to re-read.
            # Every word here is re-sent on every round of every call, so this
            # says only what changes the answer. The long form said the same
            # things three ways.
            voice = (
                "You are SPEAKING to a fisherman on a phone. He hears this once,"
                " read aloud by a machine, over engine noise. Rules:\n"
                "- ONE or TWO short sentences, forty spoken words at most.\n"
                "- Lead with the answer. Never repeat his question.\n"
                "- At most TWO numbers, each with what it MEANS: 'waves about one"
                " metre, that is calm'.\n"
                "- Plain words: 'waves' not 'significant wave height'.\n"
                "- Distances in KILOMETRES from distanceKm, never nautical miles."
                " Spell every unit as a word - 'fourteen kilometres', 'eight metres"
                " a second'. Never nm, km, m/s, kt or degC: they are read as letters.\n"
                "- Directions and times in words: 'to the south-east', 'about six"
                " in the morning'.\n"
                "- No markdown, lists, headings, tables or asterisks. Sentences only.\n"
                "- Never say dataset names, filenames or coordinates.\n"
                "- If you need something only he knows - where he is, when he sails -"
                " ask ONE short question in his language and say nothing else.\n"
                "- End with what to DO, in a few words.\n"
                "- No data: say so and send him to the local bulletin. Never guess."
            )
        else:
            voice = (
                "You are answering a working fisherman on a phone, at sea, who may read"
                " slowly and may have your answer read aloud to them. Rules:\n"
                "- Two to four short sentences. Never more.\n"
                "- Lead with the answer, not the data.\n"
                "- NO tables, NO headings, NO bullet points, NO markdown of any kind.\n"
                "- At most two or three numbers, each with what it MEANS: 'waves about"
                " one metre, that is calm' or 'wind 8 metres a second, a fresh breeze'.\n"
                "- Plain everyday words. Say 'waves' not 'significant wave height', and"
                " 'water temperature' not 'SST'.\n"
                "- UNITS: distances in KILOMETRES, using distanceKm from the tool - never"
                " nautical miles, and never do the conversion yourself. Write every unit"
                " as a word: '14 kilometres', '8 metres a second', '1 metre', '28 degrees'."
                " Never write nm, NM, km, m, m/s, kt or degC: those are read aloud to"
                " someone who cannot see the screen, and 'nm' means nothing to them.\n"
                "- Give direction in words too - 'to the south-east', not '135 degrees'.\n"
                "- End with what to DO, in one short sentence.\n"
                "- Never mention dataset filenames, coordinates or technical sources."
            )

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are SALTY, a marine assistant for the Indian coast. Use the"
                    " supplied tools whenever a factual or current value is needed. For a"
                    " question about right now, call get_current_conditions. For later"
                    " today, tonight, tomorrow or the coming days, call"
                    " get_forecast_window. For an explicit go/no-go safety question, call"
                    " get_marine_safety_forecast.\n\n"
                    + voice + "\n\n"
                    + (coverage if mode == "research" else compact_coverage) + "\n\n"
                    "CRITICAL: every number you state must come from a tool result in this"
                    " conversation. If a tool returns no usable data, say plainly that it"
                    " is unavailable. Never quote a remembered or seasonal figure: it is"
                    " indistinguishable from a measurement to the person reading it, which"
                    " makes it more dangerous than saying nothing."
                    + context
                ),
            },
        ]
        # Prior turns, so a follow-up like "what about tomorrow" means
        # something. Without this every message started from nothing and the
        # same question asked twice returned a byte-identical answer.
        for turn in (history or [])[-MAX_HISTORY_TURNS:]:
            role = turn.get("role")
            content = str(turn.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content[:2000]})
        messages.append({"role": "user", "content": user_query})

        calls = []
        tool_results = []
        safety_question = bool(_SAFETY_INTENT.search(user_query))
        if safety_question:
            # A go/no-go question always fetches the forecast - that part is not
            # left to the model. But the ANSWER is now written by the model from
            # that result, rather than returned as a fixed sentence.
            #
            # The fixed sentence was there to guarantee grounding back when the
            # model was a 0.6B local one that ignored tool results. It also meant
            # every safety question got byte-identical English full of timestamps
            # and m/s, ignoring what was actually asked and ignoring the
            # fisherman voice entirely. Grounding is now enforced by the
            # fabrication guard below instead, which is a better trade.
            name = "get_marine_safety_forecast"
            arguments: dict[str, Any] = {"bbox": list(bbox)} if bbox else {}
            data = self.tools.execute(name, arguments)
            calls.append({"tool": name, "arguments": arguments})
            tool_results.append({"tool": name, "data": data, "usable": self._result_usable(data)})
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "forced_safety",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": "forced_safety",
                "name": name,
                "content": self._tool_message_content(data),
            })

        # Research questions chain: find a dataset, read its metadata, pull a
        # series, compare. Four rounds is enough for "is it safe today" and far
        # too few for that, which is why research mode failed with "exceeded the
        # maximum tool-call rounds" and the console showed "the grounded marine
        # agent is unavailable".
        max_rounds = 10 if mode == "research" else 4

        for _ in range(max_rounds):
            # Once a tool has actually returned data, a phone answer needs
            # writing, not more fetching. Withdrawing the schemas at that point
            # saves a thousand tokens on the round that produces the answer and
            # stops the caller waiting through a fourth lookup. It is keyed on
            # having usable data rather than on a round count, so a first tool
            # that came back empty still gets a second chance.
            offer_tools = not (mode == "voice" and self._tool_data_usable(tool_results))
            message = self._chat(messages, include_tools=offer_tools,
                                 mode=mode).get("message", {})
            messages.append(message)
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                response = message.get("content", "NOT AVAILABLE")
                # No tool produced usable data, yet the answer quotes figures:
                # the model filled the gap from memory. Replace it outright.
                if not self._tool_data_usable(tool_results) and _MEASUREMENT.search(response):
                    response = self._render_in_language(
                        self._no_data_message(tool_results), user_query, language
                    )
                return {
                    "user_query": user_query,
                    "tool_calls": calls,
                    "returned_data": tool_results,
                    "references": self._references(calls, tool_results),
                    "response": response,
                }

            for call in tool_calls:
                call_id = call.get("id", "call_tool")
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                data = self.tools.execute(name, arguments)
                calls.append({"tool": name, "arguments": arguments})
                tool_results.append(
                    {"tool": name, "data": data, "usable": self._result_usable(data)}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": self._tool_message_content(data),
                    }
                )

        # Out of rounds with tool results in hand. Throwing here discarded
        # every dataset already fetched and showed the user a failure, when the
        # honest thing is to answer from what was gathered. Ask once more with
        # the tools withdrawn, so the model must write instead of fetching.
        messages.append({
            "role": "user",
            "content": (
                "Stop calling tools now and answer from the tool results already"
                " in this conversation. If they are not enough to answer fully,"
                " say what you established and what is still missing. Do not"
                " state any number that is not in a tool result above."
            ),
        })
        try:
            final = self._chat(messages, include_tools=False, mode=mode).get("message", {})
            response = (final.get("content") or "").strip()
        except GroqError:
            response = ""
        if not response:
            response = self._no_data_message(tool_results)
        if not self._tool_data_usable(tool_results) and _MEASUREMENT.search(response):
            response = self._render_in_language(
                self._no_data_message(tool_results), user_query, language
            )
        return {
            "user_query": user_query,
            "tool_calls": calls,
            "returned_data": tool_results,
            "references": self._references(calls, tool_results),
            "response": response,
            "truncated": True,
        }
