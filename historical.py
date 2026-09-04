"""Researcher-oriented historical ERDDAP retrieval and export helpers."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from erddap_client import ERDDAPClient, MetadataValidationError


def _iso(value: str, end_of_day: bool = False) -> str:
    """Accept date or ISO datetime input and return a UTC ISO timestamp."""
    text = str(value)
    if len(text) == 10:
        text += "T23:59:59Z" if end_of_day else "T00:00:00Z"
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _period(start: str, end: str) -> tuple[str, str]:
    start_iso, end_iso = _iso(start), _iso(end, end_of_day=True)
    if datetime.fromisoformat(start_iso.replace("Z", "+00:00")) > datetime.fromisoformat(end_iso.replace("Z", "+00:00")):
        raise MetadataValidationError("Historical start must not be later than end")
    return start_iso, end_iso


def table_records(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn any ERDDAP JSON table into chart/map-friendly row dictionaries."""
    table = response.get("table", {})
    columns, rows = table.get("columnNames", []), table.get("rows", [])
    return [dict(zip(columns, row)) for row in rows]


def merge_parameter_records(named_records: dict[str, Iterable[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge parameter records into rows keyed for charts and map layers."""
    merged: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    coordinate_names = {"time", "timestamp", "latitude", "lat", "longitude", "lon"}
    for parameter, records in named_records.items():
        for record in records:
            timestamp = record.get("timestamp", record.get("time"))
            latitude = record.get("latitude", record.get("lat"))
            longitude = record.get("longitude", record.get("lon"))
            key = (timestamp, latitude, longitude)
            row = merged.setdefault(key, {
                "timestamp": timestamp,
                "latitude": latitude,
                "longitude": longitude,
            })
            value_keys = [key for key in record if key.lower() not in coordinate_names]
            if value_keys:
                row[parameter] = record[value_keys[-1]]
    return sorted(merged.values(), key=lambda row: (str(row["timestamp"]), row["latitude"], row["longitude"]))


class HistoricalResearcher:
    """Convenience API for reproducible historical environmental research."""

    def __init__(self, client: ERDDAPClient):
        self.client = client

    def point(self, dataset_id: str, variable: str, timestamp: str, lat: float, lon: float) -> dict[str, Any]:
        timestamp = _iso(timestamp)
        response = self.client.get_point_data(dataset_id, variable, timestamp, lat, lon)
        return {"dataset": dataset_id, "variable": variable, "timestamp": timestamp, "latitude": lat, "longitude": lon, "records": table_records(response)}

    def region(self, dataset_id: str, variable: str, bbox: Any, start: str, end: str) -> dict[str, Any]:
        start, end = _period(start, end)
        response = self.client.get_region_data(dataset_id, variable, bbox, (start, end))
        return {"dataset": dataset_id, "variable": variable, "start": start, "end": end, "bbox": bbox, "records": table_records(response)}

    def time_series(self, dataset_id: str, variable: str, bbox: Any, start: str, end: str) -> dict[str, Any]:
        start, end = _period(start, end)
        response = self.client.get_time_series(dataset_id, variable, bbox, start, end)
        return {"dataset": dataset_id, "variable": variable, "start": start, "end": end, "bbox": bbox, "records": table_records(response)}

    def compare_parameters(self, dataset_id: str, variables: Iterable[str], bbox: Any, start: str, end: str) -> dict[str, Any]:
        start, end = _period(start, end)
        names = list(variables)
        if not names:
            raise MetadataValidationError("At least one comparison variable is required")
        response = self.client.get_forecast(dataset_id, names, bbox, start, end)
        return {"dataset": dataset_id, "variables": names, "start": start, "end": end, "bbox": bbox, "records": table_records(response)}

    def compare_datasets(self, parameters: dict[str, tuple[str, str]], bbox: Any, start: str, end: str) -> dict[str, Any]:
        """Compare parameters that may come from different historical datasets.

        ``parameters`` maps an output name to ``(dataset_id, variable)``. The
        returned rows are joined on timestamp, latitude, and longitude; a
        missing observation remains absent rather than being interpolated.
        """
        start, end = _period(start, end)
        if not parameters:
            raise MetadataValidationError("At least one parameter source is required")
        named_records = {}
        sources = {}
        for parameter, selection in parameters.items():
            if not isinstance(selection, (tuple, list)) or len(selection) != 2:
                raise MetadataValidationError(f"Source for {parameter!r} must be (dataset_id, variable)")
            dataset_id, variable = selection
            response = self.client.get_time_series(dataset_id, variable, bbox, start, end)
            named_records[parameter] = table_records(response)
            sources[parameter] = {"dataset": dataset_id, "variable": variable}
        return {
            "parameters": list(parameters),
            "sources": sources,
            "start": start,
            "end": end,
            "bbox": bbox,
            "records": merge_parameter_records(named_records),
        }

    def compare_zones(self, dataset_id: str, variable: str, zones: dict[str, Any], start: str, end: str) -> dict[str, Any]:
        start, end = _period(start, end)
        return {
            "dataset": dataset_id,
            "variable": variable,
            "start": start,
            "end": end,
            "zones": {name: self.region(dataset_id, variable, bbox, start, end) for name, bbox in zones.items()},
        }

    def netcdf(self, dataset_id: str, variable: str, bbox: Any, start: str, end: str, path: str | Path) -> Path:
        start, end = _period(start, end)
        target = Path(path)
        target.write_bytes(self.client.get_netcdf(dataset_id, variable, bbox, start, end))
        return target


def export_json(data: Any, path: str | Path) -> Path:
    target = Path(path)
    target.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    return target


def export_csv(data: Any, path: str | Path) -> Path:
    """Export records or a retrieval wrapper as a flat CSV for charts/maps."""
    target = Path(path)
    records = data.get("records", []) if isinstance(data, dict) else data
    records = list(records)
    fields = list(dict.fromkeys(key for record in records for key in record))
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    return target
