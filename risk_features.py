"""Phase 5 marine-risk feature discovery and normalization.

This module only retrieves and shapes data. It deliberately contains no model,
prediction, or inference code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable

from erddap_client import ERDDAPClient, ERDDAPError
from weather_forecast import VISAKHAPATNAM_BBOX, _parse_time, load_datasets


RISK_TERMS = {
    "wind": ("wind", "wspd", "wdir"),
    "wave height": ("wave height", "wave_height", "significant wave height", "significant_wave_height"),
    "wave period": ("wave period", "wave_period", "significant wave period"),
    "swell": ("swell",),
    "currents": ("current", "currents", "eastward_sea_water_velocity", "northward_sea_water_velocity"),
    "rainfall": ("rainfall", "precipitation", "precip", "rain"),
    "temperature": ("temperature", "temp", "sst", "sea surface temperature"),
    "cyclone/storm": ("cyclone", "storm", "hurricane", "typhoon", "wind gust"),
    "lightning/thunderstorm": ("lightning", "thunderstorm", "thunder", "lightning flash"),
}


def _text(variable: dict[str, Any], dataset: dict[str, Any]) -> str:
    # Candidate identity must come from the variable's own metadata. A
    # dataset title such as "storm forecast" must not label every variable as
    # a storm feature.
    parts = [variable.get("name", "")]
    parts.extend(f"{key} {value}" for key, value in variable.get("attributes", {}).items())
    return re.sub(r"[^a-z0-9 ]+", " ", " ".join(map(str, parts)).lower())


def discover_risk_candidates(datasets: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Find parameter candidates using only names/attributes in metadata."""
    found = {parameter: [] for parameter in RISK_TERMS}
    for dataset in datasets:
        for variable in dataset["metadata"].get("variables", []):
            if not variable.get("units"):
                continue
            text = _text(variable, dataset)
            for parameter, terms in RISK_TERMS.items():
                matched = next((term for term in terms if term.replace("_", " ").lower() in text), None)
                if matched:
                    found[parameter].append({
                        "dataset_id": dataset["dataset_id"],
                        "variable": variable["name"],
                        "unit": variable["units"],
                        "dataset": dataset.get("title", ""),
                        "metadata": dataset["metadata"],
                        "match": matched,
                    })
    return found


def forecast_window(metadata: dict[str, Any], now: datetime, hours: int = 72) -> tuple[str, str] | None:
    """Return a complete future window, or None when the coverage is unsuitable."""
    coverage = metadata.get("time_coverage", {})
    start, end = coverage.get("time_coverage_start"), coverage.get("time_coverage_end")
    if not start or not end:
        return None
    start_dt, end_dt = _parse_time(start), _parse_time(end)
    now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    forecast_start = max(start_dt, now)
    forecast_end = forecast_start + timedelta(hours=hours)
    if start_dt <= now or forecast_end > end_dt:
        return None
    return start, forecast_end.isoformat().replace("+00:00", "Z")


def _merge_records(records: Iterable[dict[str, Any]], parameters: list[str], units: dict[str, str], sources: dict[str, str]) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for record in records:
        key = (record["timestamp"], record["latitude"], record["longitude"])
        row = merged.setdefault(key, {
            "timestamp": record["timestamp"],
            "latitude": record["latitude"],
            "longitude": record["longitude"],
            "features": {},
            "missing_values": [],
            "units": dict(units),
            "source_dataset": dict(sources),
        })
        row["features"][record["parameter"]] = record["value"]
        row[record["parameter"]] = record["value"]
    for row in merged.values():
        row["missing_values"] = [parameter for parameter in parameters if row["features"].get(parameter) is None]
        for parameter in parameters:
            row["features"].setdefault(parameter, None)
            row.setdefault(parameter, None)
    return sorted(merged.values(), key=lambda row: (_parse_time(row["timestamp"]), row["latitude"], row["longitude"]))


def build_72h_feature_dataset(client: ERDDAPClient, datasets: list[dict[str, Any]], now: datetime | None = None, bbox: tuple[float, float, float, float] = VISAKHAPATNAM_BBOX) -> dict[str, Any]:
    """Retrieve and merge a chronological 72-hour feature dataset."""
    now = now or datetime.now(timezone.utc)
    candidates = discover_risk_candidates(datasets)
    selected: dict[str, dict[str, Any]] = {}
    unavailable = []
    for parameter, options in candidates.items():
        option = next((item for item in options if forecast_window(item["metadata"], now) is not None), None)
        if option is None:
            unavailable.append(parameter)
        else:
            selected[parameter] = option

    all_records = []
    units = {parameter: item["unit"] for parameter, item in selected.items()}
    sources = {parameter: item["dataset_id"] for parameter, item in selected.items()}
    forecast_timestamps = set()
    for parameter, option in list(selected.items()):
        try:
            start, end = forecast_window(option["metadata"], now)  # type: ignore[misc]
            response = client.get_region_data(option["dataset_id"], option["variable"], bbox, (start, end))
            from weather_forecast import normalize_response
            records = normalize_response(response, parameter, option["unit"], option["dataset_id"])
            all_records.extend(records)
            forecast_timestamps.update(record["timestamp"] for record in records)
        except (ERDDAPError, TypeError, ValueError):
            unavailable.append(parameter)
            selected.pop(parameter, None)
            units.pop(parameter, None)
            sources.pop(parameter, None)
    parameters = list(selected)
    rows = _merge_records(all_records, parameters, units, sources)
    return {
        "records": rows,
        "feature_units": units,
        "source_datasets": sources,
        "unavailable_parameters": sorted(set(unavailable)),
        "forecast_timestamps": sorted(forecast_timestamps, key=_parse_time),
        "missing_values": {row["timestamp"]: row["missing_values"] for row in rows if row["missing_values"]},
        "bbox": {"min_lat": bbox[0], "max_lat": bbox[1], "min_lon": bbox[2], "max_lon": bbox[3]},
        "forecast_hours": 72,
    }


def discover_and_build_live(client: ERDDAPClient, now: datetime | None = None) -> dict[str, Any]:
    """Load the ERDDAP catalog and build the Phase 5 dataset."""
    return build_72h_feature_dataset(client, load_datasets(client), now)
