"""Discover and sample marine parameters in the Visakhapatnam region."""

from __future__ import annotations

import re
from typing import Any

from erddap_client import ERDDAPClient, ERDDAPError


BBOX = (17.0, 18.5, 82.5, 84.5)  # min_lat, max_lat, min_lon, max_lon
PARAMETER_TERMS = {
    "SST": ("sea surface temperature", "sst", "sea_surface_temperature"),
    "chlorophyll": ("chlorophyll", "chlor_a", "chl"),
    "wind speed": ("wind speed", "wind_speed", "wspd"),
    "wind direction": ("wind direction", "wind_direction", "wdir"),
    "wave height": ("wave height", "wave_height", "significant wave height", "significant_wave_height"),
    "wave period": ("wave period", "wave_period", "significant wave period"),
    "swell": ("swell",),
    "ocean currents": ("current", "currents", "eastward_sea_water_velocity", "northward_sea_water_velocity"),
    "tides": ("tide", "tidal", "sea level", "water level"),
}


def _search_text(variable: dict[str, Any]) -> str:
    pieces = [variable["name"]]
    for key, value in variable.get("attributes", {}).items():
        pieces.extend((str(key), str(value)))
    return re.sub(r"[^a-z0-9 ]+", " ", " ".join(pieces).lower())


def _find_candidate(parameter: str, datasets: list[dict[str, Any]]) -> dict[str, Any] | None:
    terms = PARAMETER_TERMS[parameter]
    candidates = []
    for dataset in datasets:
        for variable in dataset["metadata"]["variables"]:
            text = _search_text(variable)
            matched = next((term for term in terms if term.replace("_", " ").lower() in text), None)
            if matched and variable.get("units"):
                candidates.append((len(matched), dataset, variable))
    if not candidates:
        return None
    _, dataset, variable = max(candidates, key=lambda item: item[0])
    return {"dataset_id": dataset["dataset_id"], "title": dataset["metadata"]["title"], "variable": variable}


def _sample_time(metadata: dict[str, Any]) -> tuple[str, str]:
    start = metadata["time_coverage"]["time_coverage_start"]
    return start, start


def main() -> None:
    # See the Phase 1 smoke test for why this live test opts out of TLS
    # verification; ERDDAPClient defaults to secure verification.
    client = ERDDAPClient(verify_ssl=False, timeout=45)
    datasets = []
    for listed in client.list_datasets():
        try:
            metadata = client.get_dataset_metadata(listed["dataset_id"])
            datasets.append({**listed, "metadata": metadata})
        except ERDDAPError as exc:
            print(f"Skipping {listed['dataset_id']}: {exc}")

    for parameter in PARAMETER_TERMS:
        candidate = _find_candidate(parameter, datasets)
        if candidate is None:
            print(f"{parameter}: NOT AVAILABLE")
            continue
        metadata = next(item["metadata"] for item in datasets if item["dataset_id"] == candidate["dataset_id"])
        start, end = _sample_time(metadata)
        variable = candidate["variable"]
        try:
            result = client.get_region_data(candidate["dataset_id"], variable["name"], BBOX, (start, end))
            rows = result.get("table", {}).get("rows", [])
            print(f"{parameter}: dataset={candidate['dataset_id']} variable={variable['name']} units={variable['units']} sample={rows[:3]}")
        except ERDDAPError as exc:
            print(f"{parameter}: NOT AVAILABLE ({exc})")


if __name__ == "__main__":
    main()
