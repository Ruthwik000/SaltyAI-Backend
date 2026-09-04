"""Phase 10 forecast and warning-related data discovery.

This module retrieves observations/forecasts exposed by ERDDAP. It does not
run a disaster model and never turns a missing parameter into a warning.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable

from erddap_client import ERDDAPClient, ERDDAPError
from historical import _period, table_records
from weather_forecast import VISAKHAPATNAM_BBOX, load_datasets


SEVERE_PARAMETERS = {
    "cyclone": ("cyclone", "tropical cyclone", "hurricane", "typhoon"),
    "strong wind": ("wind gust", "gust", "strong wind", "wind speed", "wind"),
    "high waves": ("wave height", "significant wave height", "high wave", "wave"),
    "swell": ("swell", "swell height"),
    "rainfall": ("rainfall", "precipitation", "precip", "rain"),
    "lightning": ("lightning", "lightning flash"),
    "thunderstorms": ("thunderstorm", "thunder", "convective storm"),
    "other severe weather": ("storm", "severe", "weather warning", "extreme"),
}


def _text(variable: dict[str, Any]) -> str:
    parts = [variable.get("name", "")]
    parts.extend(f"{key} {value}" for key, value in variable.get("attributes", {}).items())
    return re.sub(r"[^a-z0-9 ]+", " ", " ".join(map(str, parts)).lower())


def discover_severe_candidates(datasets: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Discover forecast/warning candidates from variable metadata only."""
    found = {parameter: [] for parameter in SEVERE_PARAMETERS}
    for dataset in datasets:
        for variable in dataset.get("metadata", {}).get("variables", []):
            if not variable.get("units"):
                continue
            text = _text(variable)
            for parameter, terms in SEVERE_PARAMETERS.items():
                match = next((term for term in terms if term.replace("_", " ").lower() in text), None)
                if match:
                    found[parameter].append({
                        "dataset_id": dataset["dataset_id"],
                        "dataset": dataset.get("title", ""),
                        "variable": variable["name"],
                        "unit": variable["units"],
                        "metadata": dataset["metadata"],
                        "match": match,
                    })
    return found


def _record(response: dict[str, Any], candidate: dict[str, Any], source: str, valid_from: str, valid_to: str) -> list[dict[str, Any]]:
    result = []
    for row in table_records(response):
        lowered = {str(key).lower(): key for key in row}
        time_key = lowered.get("time") or lowered.get("timestamp")
        value_key = next((key for key in row if str(key).lower() not in {"time", "timestamp", "latitude", "longitude", "lat", "lon"}), None)
        if time_key is None or value_key is None:
            continue
        result.append({
            "source": source,
            "dataset": candidate["dataset_id"],
            "variable": candidate["variable"],
            "value": row[value_key],
            "unit": candidate["unit"],
            "timestamp": row[time_key],
            "valid_from": valid_from,
            "valid_to": valid_to,
        })
    return result


def build_severe_weather_data(
    client: ERDDAPClient,
    datasets: list[dict[str, Any]],
    bbox: tuple[float, float, float, float] = VISAKHAPATNAM_BBOX,
    start: str = "2020-01-01",
    end: str = "2020-01-04",
) -> dict[str, Any]:
    """Retrieve available severe-weather inputs, marking missing ones clearly."""
    start, end = _period(start, end)
    discovered = discover_severe_candidates(datasets)
    parameters: dict[str, list[dict[str, Any]] | str] = {}
    sources = []
    for parameter, options in discovered.items():
        for candidate in options:
            try:
                response = client.get_region_data(candidate["dataset_id"], candidate["variable"], bbox, (start, end))
                source = "SYNTHETIC TEST DATA" if response.get("synthetic") else "ERDDAP"
                records = _record(response, candidate, source, start, end)
                if records:
                    parameters[parameter] = records
                    sources.append({"source": source, "dataset": candidate["dataset_id"], "variable": candidate["variable"], "unit": candidate["unit"]})
                    break
            except (ERDDAPError, TypeError, ValueError):
                continue
        parameters.setdefault(parameter, "NOT AVAILABLE")
    return {
        "region": {"min_lat": bbox[0], "max_lat": bbox[1], "min_lon": bbox[2], "max_lon": bbox[3]},
        "valid_from": start,
        "valid_to": end,
        "parameters": parameters,
        "sources": sources,
        "warnings": [],
    }


def build_live_severe_weather_data(
    client: ERDDAPClient,
    bbox: tuple[float, float, float, float] = VISAKHAPATNAM_BBOX,
    start: str = "2020-01-01",
    end: str = "2020-01-04",
) -> dict[str, Any]:
    return build_severe_weather_data(client, load_datasets(client), bbox, start, end)
