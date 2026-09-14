"""Metadata-driven weather and forecast discovery for SALTY Phase 4."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable

from erddap_client import ERDDAPClient, ERDDAPError


VISAKHAPATNAM_BBOX = (17.0, 18.5, 82.5, 84.5)
WEATHER_TERMS = {
    "temperature": ("air temperature", "air_temperature", "temperature", "temp"),
    "rainfall": ("rainfall", "precipitation", "precip", "rain"),
    "humidity": ("relative humidity", "relative_humidity", "humidity"),
    "wind": ("wind", "wind speed", "wind direction", "wspd", "wdir"),
    "clouds": ("cloud", "cloudiness", "cloud cover", "cloud_fraction"),
    "weather-related forecast variables": ("forecast", "meteorological", "weather", "air temperature", "precipitation"),
}
OCEAN_TEMPERATURE_TERMS = ("sea surface", "sea_water", "sst", "ocean temperature")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _search_text(variable: dict[str, Any], dataset: dict[str, Any]) -> str:
    pieces = [variable.get("name", ""), dataset.get("dataset_id", ""), dataset.get("title", "")]
    pieces.extend(f"{key} {value}" for key, value in variable.get("attributes", {}).items())
    return re.sub(r"[^a-z0-9 ]+", " ", " ".join(map(str, pieces)).lower())


def discover_weather_candidates(datasets: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return metadata-backed candidates grouped by requested weather parameter."""
    found = {parameter: [] for parameter in WEATHER_TERMS}
    for dataset in datasets:
        for variable in dataset["metadata"].get("variables", []):
            if not variable.get("units"):
                continue
            text = _search_text(variable, dataset)
            for parameter, terms in WEATHER_TERMS.items():
                if parameter == "temperature" and any(term in text for term in OCEAN_TEMPERATURE_TERMS):
                    continue
                matched = next((term for term in terms if term.replace("_", " ").lower() in text), None)
                if matched:
                    found[parameter].append({
                        "dataset_id": dataset["dataset_id"],
                        "dataset": dataset.get("title", ""),
                        "metadata": dataset["metadata"],
                        "variable": variable["name"],
                        "unit": variable["units"],
                        "match": matched,
                    })
    return found


def _coverage_window(metadata: dict[str, Any], period: str, now: datetime | None = None) -> tuple[str, str] | None:
    coverage = metadata.get("time_coverage", {})
    start, end = coverage.get("time_coverage_start"), coverage.get("time_coverage_end")
    if not start or not end:
        return None
    now = now or datetime.now(timezone.utc)
    start_dt, end_dt = _parse_time(start), _parse_time(end)
    if period == "current" and start_dt <= now <= end_dt:
        return end, end
    if period == "future" and start_dt > now:
        return start, start
    return None


def normalize_response(response: dict[str, Any], parameter: str, unit: str, dataset: str) -> list[dict[str, Any]]:
    """Convert an ERDDAP JSON table into SALTY's normalized records."""
    table = response.get("table", {})
    columns = table.get("columnNames", [])
    rows = table.get("rows", [])
    lowered = {str(column).lower(): column for column in columns}
    time_column = lowered.get("time")
    lat_column = lowered.get("latitude") or lowered.get("lat")
    lon_column = lowered.get("longitude") or lowered.get("lon")
    value_column = next((column for column in columns if str(column).lower() not in {"time", "latitude", "longitude", "lat", "lon"}), None)
    if not all((time_column, lat_column, lon_column, value_column)):
        return []
    indexes = {column: index for index, column in enumerate(columns)}
    records = []
    for row in rows:
        records.append({
            "timestamp": row[indexes[time_column]],
            "latitude": row[indexes[lat_column]],
            "longitude": row[indexes[lon_column]],
            "parameter": parameter,
            "value": row[indexes[value_column]],
            "unit": unit,
            "dataset": dataset,
        })
    return records


def retrieve_period(client: ERDDAPClient, candidate: dict[str, Any], parameter: str, bbox: tuple[float, float, float, float], period: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """Retrieve the next/latest available period for a metadata-backed candidate."""
    window = _coverage_window(candidate["metadata"], period, now)
    if window is None:
        return []
    response = client.get_region_data(candidate["dataset_id"], candidate["variable"], bbox, window)
    return normalize_response(response, parameter, candidate["unit"], candidate["dataset_id"])


def load_datasets(client: ERDDAPClient) -> list[dict[str, Any]]:
    """Load catalog metadata, skipping datasets that cannot be described."""
    datasets = []
    for listed in client.list_datasets():
        try:
            datasets.append({**listed, "metadata": client.get_dataset_metadata(listed["dataset_id"])})
        except ERDDAPError:
            continue
    return datasets
