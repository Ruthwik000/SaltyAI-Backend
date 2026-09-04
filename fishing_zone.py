"""Phase 6 fishing-zone data discovery and retrieval.

This module provides environmental observations only. It never derives or
invents potential-fishing-zone coordinates.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any, Iterable

from erddap_client import ERDDAPClient, ERDDAPError
from weather_forecast import VISAKHAPATNAM_BBOX, _parse_time, load_datasets, normalize_response


FISHING_TERMS = {
    "SST": ("sea surface temperature", "sst", "sea_water_temperature"),
    "chlorophyll": ("chlorophyll", "chlor_a", "chl"),
    "currents": ("current", "currents", "sea_water_velocity", "eastward_sea_water_velocity", "northward_sea_water_velocity"),
    "ocean colour": ("ocean colour", "ocean color", "ocean_colour", "ocean_color", "reflectance", "remote sensing reflectance"),
    "PFZ-related": ("pfz", "potential fishing zone", "fishing zone", "fishery"),
}


def _metadata_text(variable: dict[str, Any]) -> str:
    parts = [variable.get("name", "")]
    parts.extend(f"{key} {value}" for key, value in variable.get("attributes", {}).items())
    return re.sub(r"[^a-z0-9 ]+", " ", " ".join(map(str, parts)).lower())


def discover_fishing_candidates(datasets: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Find candidates from variable names and ERDDAP variable attributes."""
    found = {parameter: [] for parameter in FISHING_TERMS}
    for dataset in datasets:
        for variable in dataset["metadata"].get("variables", []):
            if not variable.get("units"):
                continue
            text = _metadata_text(variable)
            for parameter, terms in FISHING_TERMS.items():
                matched = next((term for term in terms if term.replace("_", " ").lower() in text), None)
                if matched:
                    found[parameter].append({
                        "dataset_id": dataset["dataset_id"],
                        "dataset": dataset.get("title", ""),
                        "variable": variable["name"],
                        "unit": variable["units"],
                        "metadata": dataset["metadata"],
                        "match": matched,
                    })
    return found


def _sample_window(metadata: dict[str, Any]) -> tuple[str, str] | None:
    coverage = metadata.get("time_coverage", {})
    start, end = coverage.get("time_coverage_start"), coverage.get("time_coverage_end")
    if not start or not end:
        return None
    start_dt, end_dt = _parse_time(start), _parse_time(end)
    sample_end = min(start_dt + timedelta(days=1), end_dt)
    return start, sample_end.isoformat().replace("+00:00", "Z")


def _candidate_order(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return candidates with usable coverage first, without guessing a source."""
    return [candidate for candidate in candidates if _sample_window(candidate["metadata"])]


def retrieve_fishing_data(client: ERDDAPClient, candidate: dict[str, Any], region: tuple[float, float, float, float]) -> tuple[str, dict[str, Any]]:
    """Retrieve point, regional, and time-series data for one candidate."""
    window = _sample_window(candidate["metadata"])
    if window is None:
        raise ERDDAPError(f"No time coverage for {candidate['dataset_id']}")
    start, end = window
    point = ((region[0] + region[1]) / 2, (region[2] + region[3]) / 2)
    point_data = client.get_point_data(candidate["dataset_id"], candidate["variable"], start, *point)
    regional_data = client.get_region_data(candidate["dataset_id"], candidate["variable"], region, window)
    time_series_data = client.get_time_series(candidate["dataset_id"], candidate["variable"], region, start, end)
    return start, {
        "dataset": candidate["dataset_id"],
        "variable": candidate["variable"],
        "unit": candidate["unit"],
        "point": normalize_response(point_data, candidate["variable"], candidate["unit"], candidate["dataset_id"]),
        "region": normalize_response(regional_data, candidate["variable"], candidate["unit"], candidate["dataset_id"]),
        "time_series": normalize_response(time_series_data, candidate["variable"], candidate["unit"], candidate["dataset_id"]),
    }


def build_fishing_zone_data(client: ERDDAPClient, datasets: list[dict[str, Any]], region: tuple[float, float, float, float] = VISAKHAPATNAM_BBOX) -> dict[str, Any]:
    """Return environmental inputs for a coastal region, without PFZ output."""
    candidates = discover_fishing_candidates(datasets)
    parameters: dict[str, Any] = {}
    unavailable = []
    sources = []
    timestamp = None
    for parameter, options in candidates.items():
        usable_candidates = _candidate_order(options)
        if not usable_candidates:
            unavailable.append(parameter)
            continue
        retrieved = None
        selected_candidate = None
        for candidate in usable_candidates:
            try:
                candidate_timestamp, data = retrieve_fishing_data(client, candidate, region)
                retrieved = (candidate_timestamp, data)
                selected_candidate = candidate
                break
            except (ERDDAPError, TypeError, ValueError):
                # A catalog entry can have coverage but still be unsuitable
                # for griddap (for example, missing spatial dimensions). Try
                # the next metadata-backed candidate instead of fabricating a
                # substitute value.
                continue
        if retrieved is None or selected_candidate is None:
            unavailable.append(parameter)
            continue
        candidate_timestamp, data = retrieved
        parameters[parameter] = data
        # All parameter queries use the same one-day window. Preserve the
        # first successful query's timestamp as the response timestamp.
        timestamp = timestamp or candidate_timestamp
        sources.append({"dataset": selected_candidate["dataset_id"], "variable": selected_candidate["variable"], "unit": selected_candidate["unit"]})
    return {
        "region": {"name": "Visakhapatnam", "min_lat": region[0], "max_lat": region[1], "min_lon": region[2], "max_lon": region[3]},
        "parameters": parameters,
        "timestamp": timestamp,
        "sources": sources,
        "unavailable_parameters": sorted(set(unavailable)),
        "pfz_coordinates": None,
    }


def build_live_fishing_zone_data(client: ERDDAPClient, region: tuple[float, float, float, float] = VISAKHAPATNAM_BBOX) -> dict[str, Any]:
    return build_fishing_zone_data(client, load_datasets(client), region)
