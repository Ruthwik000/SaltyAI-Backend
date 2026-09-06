"""Small local HTTP adapter for the SALTY Python data layer.

Run with ``python3 api_server.py``. The default prototype mode uses the
existing ERDDAP client's explicitly labelled synthetic fallback when the
remote service is unavailable. Set ``SALTY_LIVE=1`` to use the live catalog.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from erddap_client import ERDDAPClient, ERDDAPConnectionError
from fishing_zone import build_fishing_zone_data
from ollama_agent import ERDDAPTools, OllamaAgent, OllamaError
from prediction_models import build_predictions
from risk_features import build_72h_feature_dataset
from severe_weather import build_severe_weather_data


BBOX = (17.0, 18.5, 82.5, 84.5)
SYNTHETIC_METADATA = {
    "time_coverage": {"time_coverage_start": "2020-01-01T00:00:00Z", "time_coverage_end": "2030-01-01T00:00:00Z"},
    "dimensions": ["time", "latitude", "longitude"],
    "variables": [
        {"name": "SST", "units": "degC", "attributes": {"standard_name": "sea_surface_temperature"}},
        {"name": "CHL", "units": "mg/m3", "attributes": {"long_name": "chlorophyll a"}},
        {"name": "wind_speed", "units": "m/s", "attributes": {"long_name": "wind speed"}},
        {"name": "wave_height", "units": "m", "attributes": {"long_name": "significant wave height"}},
        {"name": "swell_height", "units": "m", "attributes": {"long_name": "swell height"}},
        {"name": "current_speed", "units": "m/s", "attributes": {"long_name": "ocean current speed"}},
        {"name": "rainfall", "units": "mm", "attributes": {"long_name": "rainfall"}},
    ],
}


class PrototypeClient(ERDDAPClient):
    """Client that exercises the normal query fallback without remote I/O."""

    def get_dataset_metadata(self, dataset_id):
        return {**SYNTHETIC_METADATA, "dataset_id": dataset_id, "title": "SYNTHETIC TEST DATA — prototype marine feed"}

    def list_datasets(self):
        return [{"dataset_id": "prototype_marine", "title": "SYNTHETIC TEST DATA — prototype marine feed"}]

    def _query(self, dataset_id, expression):
        raise ERDDAPConnectionError("prototype backend has no remote feed")


def _client() -> ERDDAPClient:
    # Always try the real INCOIS ERDDAP first. SALTY_LIVE=1 disables the
    # per-request synthetic fallback entirely (fail loudly instead of ever
    # fabricating a value); SALTY_PROTOTYPE=1 opts back into the old
    # always-fake client for offline development.
    if os.getenv("SALTY_PROTOTYPE") == "1":
        return PrototypeClient(synthetic_fallback=True)
    return ERDDAPClient(timeout=20, verify_ssl=False, synthetic_fallback=os.getenv("SALTY_LIVE") != "1")


def _prediction_client() -> ERDDAPClient:
    """Prediction inputs must be real; never use the prototype fallback."""
    return ERDDAPClient(timeout=30, synthetic_fallback=False)


def _datasets(client: ERDDAPClient):
    if isinstance(client, PrototypeClient):
        return [{"dataset_id": "prototype_marine", "title": "SYNTHETIC TEST DATA — prototype marine feed", "metadata": SYNTHETIC_METADATA}]
    from weather_forecast import load_datasets
    try:
        return load_datasets(client)
    except Exception:
        # Keep the local API usable when INCOIS is temporarily unreachable.
        # Query records remain explicitly tagged as synthetic by ERDDAPClient.
        return _datasets(PrototypeClient(synthetic_fallback=True))


def _research_series(payload: dict) -> dict:
    """Return a labelled local series for the chart fallback contract."""
    dataset_id = str(payload.get("datasetId", "unknown"))
    variable = str(payload.get("variable", "unknown"))
    region = payload.get("region") or BBOX
    start = str(payload.get("start", "2023-01-01"))[:7]
    end = str(payload.get("end", "2026-01-01"))[:7]
    year, month = (int(part) for part in start.split("-"))
    end_year, end_month = (int(part) for part in end.split("-"))
    points = []
    while (year, month) <= (end_year, end_month) and len(points) < 480:
        value = 28.2 + 1.8 * math.sin((month - 1) / 12 * math.tau) + (year - int(start[:4])) * 0.02
        points.append({"t": f"{year:04d}-{month:02d}-01", "value": round(value, 3)})
        month += 1
        if month == 13:
            month, year = 1, year + 1
    values = [point["value"] for point in points]
    mean = sum(values) / len(values) if values else 0
    baseline = [{"t": point["t"], "value": round(mean, 3)} for point in points]
    return {
        "synthetic": True,
        "datasetId": dataset_id,
        "variable": variable,
        "unit": "°C" if "sst" in variable.lower() or "temp" in variable.lower() else "",
        "region": region,
        "start": points[0]["t"] if points else start,
        "end": points[-1]["t"] if points else end,
        "points": points,
        "baseline": baseline,
        "climatology": [{"month": str(index + 1), "mean": round(mean, 3)} for index in range(12)],
        "histogram": [],
        "stats": {"count": len(values), "mean": round(mean, 3), "min": round(min(values), 3) if values else 0, "max": round(max(values), 3) if values else 0, "stdDev": 0, "trendPerDecade": 0, "anomalyMean": 0},
    }



# ---------------------------------------------------------------------------
# Research & Data catalog — real INCOIS ERDDAP metadata + animations.
#
# Every value served here comes straight from https://erddap.incois.gov.in.
# No synthetic fallback: if INCOIS is unreachable the endpoint fails instead
# of inventing marine values. All 16 datasets from reference.ipynb are listed;
# 15 are griddap (time/lat/lon [+ a fixed depth axis]) and one — the Argo
# float profiles — is a tabledap point table, handled separately below.
# ---------------------------------------------------------------------------

import re

RESEARCH_DATASETS = {
    "incois_argo_sst_weekly": {"variable": "ASST", "unit": "°C"},
    "AMSRE_MONTHLY_GLOBAL": {"variable": "SST", "unit": "°C"},
    "ascat_daily_datasets": {"variable": "wind_speed", "unit": "m/s"},
    "ascat_mnt_datasets": {"variable": "wind_speed", "unit": "m/s"},
    "NOAA_AVHRR_AMSR_datasets": {"variable": "sst", "unit": "°C"},
    "incois_argo_10day_McCreary": {"variable": "T_ANALYZED", "unit": "°C"},
    "incois_argo_10d_VAM": {"variable": "TEMP", "unit": "°C"},
    "incois_argo_mnt_McCreary": {"variable": "T_ANALYZED", "unit": "°C"},
    "incois_argo_mnt_VAM": {"variable": "TEMP", "unit": "°C"},
    "incois_oceansat2_datasets": {"variable": "CHL", "unit": "mg/m³"},
    "incois_quickscat_daily_datasets": {"variable": "WIND_SPEED", "unit": "m/s"},
    "incois_quickscat_mnt_datasets": {"variable": "WIND_SPEED", "unit": "m/s"},
    "incois_tmi_3day_datasets": {"variable": "SST", "unit": "°C"},
    "incois_valueadded_products_datasets": {"variable": "MLD", "unit": "m"},
    "IRS_chlorophyll_datasets": {"variable": "CHLOROPHYLL", "unit": "mg/m³"},
    "Indian_ARGO_Floats": {"variable": "TEMP", "unit": "°C", "kind": "table"},
}

_catalog_cache: list[dict] = []
_catalog_cache_at = 0.0
_CATALOG_TTL = 3600

LAT_NAMES = {"latitude", "lat", "y"}
LON_NAMES = {"longitude", "lon", "long", "x"}
TIME_NAMES = {"time", "t"}


def _real_client() -> ERDDAPClient:
    """A client that only ever returns live INCOIS data, never synthetic rows."""
    return ERDDAPClient(timeout=45, verify_ssl=False, synthetic_fallback=False)


_metadata_cache: dict[str, tuple[float, dict]] = {}
_dim_size_cache: dict[str, tuple[float, dict]] = {}
_METADATA_TTL = 3600


def _cached_metadata(client: ERDDAPClient, dataset_id: str) -> dict:
    """Metadata rarely changes; caching it keeps frame stepping snappy."""
    cached = _metadata_cache.get(dataset_id)
    if cached and time.time() - cached[0] < _METADATA_TTL:
        return cached[1]
    metadata = client.get_dataset_metadata(dataset_id)
    _metadata_cache[dataset_id] = (time.time(), metadata)
    return metadata


def _cached_dimension_sizes(client: ERDDAPClient, dataset_id: str) -> dict[str, int]:
    cached = _dim_size_cache.get(dataset_id)
    if cached and time.time() - cached[0] < _METADATA_TTL:
        return cached[1]
    sizes = _dimension_sizes(client, dataset_id)
    _dim_size_cache[dataset_id] = (time.time(), sizes)
    return sizes


def _actual_range(metadata: dict, name: str | None) -> tuple[float, float] | None:
    if not name:
        return None
    for variable in metadata.get("variables", []):
        if variable["name"] == name:
            raw = variable.get("attributes", {}).get("actual_range")
            if raw:
                parts = [part.strip() for part in str(raw).split(",")]
                if len(parts) == 2:
                    try:
                        return float(parts[0]), float(parts[1])
                    except ValueError:
                        return None
    return None


def _dimension_sizes(client: ERDDAPClient, dataset_id: str) -> dict[str, int]:
    """Real grid sizes read from ERDDAP's own dimension rows (`nValues=...`)."""
    sizes: dict[str, int] = {}
    for row in client._metadata_rows(dataset_id):
        if str(row.get("Row Type") or "").lower() != "dimension":
            continue
        name = row.get("Variable Name")
        match = re.search(r"nValues=(\d+)", str(row.get("Value") or ""))
        if name and match:
            sizes[name] = int(match.group(1))
    return sizes


def _classify_dims(dimensions: list[str]) -> tuple[str | None, str | None, str | None, list[str]]:
    time_dim = next((d for d in dimensions if d.lower() in TIME_NAMES), None)
    lat_dim = next((d for d in dimensions if d.lower() in LAT_NAMES), None)
    lon_dim = next((d for d in dimensions if d.lower() in LON_NAMES), None)
    extra = [d for d in dimensions if d not in (time_dim, lat_dim, lon_dim)]
    return time_dim, lat_dim, lon_dim, extra


def _dataset_summary(client: ERDDAPClient, dataset_id: str, cfg: dict) -> dict:
    metadata = _cached_metadata(client, dataset_id)
    attributes = metadata.get("attributes", {})
    dimensions = metadata.get("dimensions", [])
    variables = [
        {
            "name": variable["name"],
            "units": variable.get("units") or (cfg.get("unit") if variable["name"] == cfg.get("variable") else None),
            "longName": variable.get("attributes", {}).get("long_name"),
        }
        for variable in metadata.get("variables", [])
        if variable["name"] not in dimensions
    ]
    geo = None
    if cfg.get("kind") != "table":
        _, lat_dim, lon_dim, _ = _classify_dims(dimensions)
        lat_range = _actual_range(metadata, lat_dim)
        lon_range = _actual_range(metadata, lon_dim)
        if lat_range and lon_range:
            geo = {"latMin": lat_range[0], "latMax": lat_range[1], "lonMin": lon_range[0], "lonMax": lon_range[1]}
    else:
        lat_range = _actual_range(metadata, "latitude")
        lon_range = _actual_range(metadata, "longitude")
        if lat_range and lon_range:
            geo = {"latMin": lat_range[0], "latMax": lat_range[1], "lonMin": lon_range[0], "lonMax": lon_range[1]}
    return {
        "id": dataset_id,
        "title": metadata.get("title") or dataset_id,
        "summary": attributes.get("summary", ""),
        "institution": attributes.get("institution", "INCOIS"),
        "variable": cfg["variable"],
        "unit": cfg.get("unit", ""),
        "kind": cfg.get("kind", "grid"),
        "variables": variables,
        "dimensions": dimensions,
        "timeCoverage": metadata.get("time_coverage", {}),
        "geospatial": geo,
    }


def _get_catalog() -> list[dict]:
    global _catalog_cache, _catalog_cache_at
    if _catalog_cache and time.time() - _catalog_cache_at < _CATALOG_TTL:
        return _catalog_cache
    client = _real_client()
    datasets = []
    for dataset_id, cfg in RESEARCH_DATASETS.items():
        try:
            datasets.append(_dataset_summary(client, dataset_id, cfg))
        except Exception as exc:
            datasets.append({"id": dataset_id, "error": str(exc)})
    _catalog_cache = datasets
    _catalog_cache_at = time.time()
    return datasets


def _fill_value(metadata: dict, variable: str) -> float | None:
    for item in metadata.get("variables", []):
        if item["name"] == variable:
            raw = item.get("attributes", {}).get("_FillValue")
            if raw is not None:
                try:
                    return float(raw)
                except ValueError:
                    return None
    return None


def _research_timeseries(dataset_id: str, cfg: dict) -> dict:
    client = _real_client()
    variable = cfg["variable"]

    if cfg.get("kind") == "table":
        # Argo float profiles: aggregate near-surface readings (real
        # measurements, PRES < 10 dbar) over the dataset's own last two years.
        metadata = _cached_metadata(client, dataset_id)
        coverage = metadata.get("time_coverage", {})
        end = coverage.get("time_coverage_end")
        if not end:
            raise ERDDAPConnectionError(f"{dataset_id} has no published time coverage")
        end_dt = ERDDAPClient._parse_time(end)
        start_dt = end_dt.replace(year=end_dt.year - 2) if end_dt.year > 2 else end_dt
        query = (
            f"time,{variable}"
            f"&time>={quote(start_dt.isoformat().replace('+00:00', 'Z'), safe=':-')}"
            f"&time<={quote(end, safe=':-')}"
            f"&PRES<10"
        )
        data = client._get_json(f"tabledap/{quote(dataset_id, safe='')}.json?{query.replace('>', '%3E').replace('<', '%3C')}")
        columns = data.get("table", {}).get("columnNames", [])
        rows = data.get("table", {}).get("rows", [])
        time_at = columns.index("time")
        value_at = columns.index(variable)
        buckets: dict[str, list[float]] = {}
        for row in rows:
            value = row[value_at]
            if value is None:
                continue
            month = str(row[time_at])[:7]
            buckets.setdefault(month, []).append(float(value))
        points = [
            {"t": f"{month}-01", "value": round(sum(values) / len(values), 3)}
            for month, values in sorted(buckets.items())
        ]
        return {"datasetId": dataset_id, "variable": variable, "unit": cfg.get("unit", ""), "points": points}

    metadata = _cached_metadata(client, dataset_id)
    dimensions = metadata.get("dimensions", [])
    time_dim, lat_dim, lon_dim, extra_dims = _classify_dims(dimensions)
    if not (time_dim and lat_dim and lon_dim):
        raise ERDDAPConnectionError(f"{dataset_id} does not expose a time/lat/lon grid")
    lat_range = _actual_range(metadata, lat_dim) or (-90.0, 90.0)
    lon_range = _actual_range(metadata, lon_dim) or (-180.0, 180.0)
    lat = (lat_range[0] + lat_range[1]) / 2
    lon = (lon_range[0] + lon_range[1]) / 2
    coverage = metadata.get("time_coverage", {})
    start = coverage.get("time_coverage_start")
    end = coverage.get("time_coverage_end")
    if not start or not end:
        raise ERDDAPConnectionError(f"{dataset_id} has no published time coverage")

    constraints = {time_dim: f"[({start}):({end})]", lat_dim: f"[({lat})]", lon_dim: f"[({lon})]"}
    for dim in extra_dims:
        dim_range = _actual_range(metadata, dim) or (0.0, 0.0)
        constraints[dim] = f"[({dim_range[0]})]"
    expression = variable + "".join(constraints[dim] for dim in dimensions)

    data = client._query(dataset_id, expression)
    columns = data.get("table", {}).get("columnNames", [])
    rows = data.get("table", {}).get("rows", [])
    time_at = columns.index("time")
    value_at = columns.index(variable)
    fill = _fill_value(metadata, variable)
    points = []
    for row in rows:
        value = row[value_at]
        if value is None:
            continue
        value = float(value)
        if fill is not None and abs(value - fill) < 1e-6:
            continue
        points.append({"t": row[time_at], "value": value})
    return {
        "datasetId": dataset_id,
        "variable": variable,
        "unit": cfg.get("unit", ""),
        "lat": lat,
        "lon": lon,
        "points": points,
    }


def _research_frames(dataset_id: str, cfg: dict, count: int) -> dict:
    client = _real_client()
    if cfg.get("kind") == "table":
        metadata = _cached_metadata(client, dataset_id)
        end = ERDDAPClient._parse_time(metadata.get("time_coverage", {}).get("time_coverage_end"))
        # 15-day windows walking back from the dataset's own latest fix.
        windows = []
        cursor = end
        for _ in range(count):
            start = cursor - timedelta(days=15)
            windows.append(f"{start.isoformat().replace('+00:00', 'Z')}|{cursor.isoformat().replace('+00:00', 'Z')}")
            cursor = start
        windows.reverse()
        return {"datasetId": dataset_id, "variable": cfg["variable"], "times": windows}

    data = client._query(dataset_id, "time")
    times = [row[0] for row in data.get("table", {}).get("rows", [])]
    chosen = times[-count:] if len(times) > count else times
    return {"datasetId": dataset_id, "variable": cfg["variable"], "times": chosen}


def _research_frame_png(dataset_id: str, cfg: dict, time_value: str) -> bytes:
    client = _real_client()

    if cfg.get("kind") == "table":
        start, end = time_value.split("|")
        variable = cfg["variable"]
        query = (
            f"longitude,latitude,{variable}"
            f"&time>={quote(start, safe=':-')}&time<={quote(end, safe=':-')}"
            f"&PRES<10&.draw=markers&.marker=5%7C5&.colorBar=%7C%7C%7C%7C%7C"
        )
        return client._get_bytes(f"tabledap/{quote(dataset_id, safe='')}.png?{query.replace('>', '%3E').replace('<', '%3C')}")

    metadata = _cached_metadata(client, dataset_id)
    dimensions = metadata.get("dimensions", [])
    time_dim, lat_dim, lon_dim, extra_dims = _classify_dims(dimensions)
    lat_range = _actual_range(metadata, lat_dim) or (-90.0, 90.0)
    lon_range = _actual_range(metadata, lon_dim) or (-180.0, 180.0)
    sizes = _cached_dimension_sizes(client, dataset_id)
    lat_stride = max(1, round(sizes.get(lat_dim, 150) / 150))
    lon_stride = max(1, round(sizes.get(lon_dim, 150) / 150))
    variable = cfg["variable"]

    constraints = {
        time_dim: f"[({time_value})]",
        lat_dim: f"[({lat_range[0]}):{lat_stride}:({lat_range[1]})]",
        lon_dim: f"[({lon_range[0]}):{lon_stride}:({lon_range[1]})]",
    }
    for dim in extra_dims:
        dim_range = _actual_range(metadata, dim) or (0.0, 0.0)
        constraints[dim] = f"[({dim_range[0]})]"
    expression = variable + "".join(constraints[dim] for dim in dimensions)
    encoded = quote(expression, safe=",():")
    return client._get_bytes(f"griddap/{quote(dataset_id, safe='')}.png?{encoded}")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        client = _client()
        try:
            if parsed.path == "/api/health":
                mode = "prototype" if os.getenv("SALTY_PROTOTYPE") == "1" else ("live-strict" if os.getenv("SALTY_LIVE") == "1" else "live")
                return self._send(200, {"ok": True, "service": "salty-data-layer", "mode": mode})
            if parsed.path == "/api/predictions":
                prediction_client = _prediction_client()
                features = build_72h_feature_dataset(prediction_client, _datasets(prediction_client), bbox=BBOX)
                return self._send(200, build_predictions(features))
            if parsed.path == "/api/severe-weather":
                return self._send(200, build_severe_weather_data(client, _datasets(client), BBOX))
            if parsed.path == "/api/marine/point":
                response = client.get_point_data("prototype_marine", "SST", "2020-01-01T00:00:00Z", 17.6868, 83.2185)
                return self._send(200, {"synthetic": response.get("synthetic", False), "records": response.get("table", {}).get("rows", [])})
            if parsed.path == "/api/fishing-zones":
                return self._send(200, build_fishing_zone_data(client, _datasets(client), BBOX))
            if parsed.path == "/api/research/catalog":
                return self._send(200, {"datasets": _get_catalog()})
            if parsed.path == "/api/research/timeseries":
                dataset_id = (query.get("id") or [None])[0]
                cfg = RESEARCH_DATASETS.get(dataset_id or "")
                if not cfg:
                    return self._send(404, {"error": "unknown dataset"})
                return self._send(200, _research_timeseries(dataset_id, cfg))
            if parsed.path == "/api/research/frames":
                dataset_id = (query.get("id") or [None])[0]
                cfg = RESEARCH_DATASETS.get(dataset_id or "")
                if not cfg:
                    return self._send(404, {"error": "unknown dataset"})
                count = int((query.get("count") or ["12"])[0])
                return self._send(200, _research_frames(dataset_id, cfg, count))
            if parsed.path == "/api/research/frame.png":
                dataset_id = (query.get("id") or [None])[0]
                time_value = (query.get("time") or [None])[0]
                cfg = RESEARCH_DATASETS.get(dataset_id or "")
                if not cfg or not time_value:
                    return self._send(404, {"error": "unknown dataset or time"})
                png = _research_frame_png(dataset_id, cfg, time_value)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(png)
                return
            return self._send(404, {"error": "NOT FOUND"})
        except Exception as exc:
            return self._send(500, {"error": str(exc), "status": "NOT AVAILABLE"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/research/series":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                return self._send(200, _research_series(payload))
            except (json.JSONDecodeError, ValueError) as exc:
                return self._send(400, {"error": str(exc)})
        if parsed.path not in ("/api/llm/chat", "/api/ai/query"):
            return self._send(404, {"error": "NOT FOUND"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            query = str(payload.get("query", payload.get("message", ""))).strip()
            if not query:
                return self._send(400, {"error": "query is required"})
            if len(query) > 4000:
                return self._send(413, {"error": "query is too long"})
            location = payload.get("location")
            language = str(payload.get("language", "English")).strip() or "English"
            location_context = ""
            if isinstance(location, dict):
                name = str(location.get("name", "")).strip()
                lat = location.get("lat")
                lon = location.get("lon")
                if name and isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                    location_context = f" The selected operating location is {name} ({lat}, {lon})."
            language_context = (
                f" Reply in {language}. Preserve technical values, units, dataset names, and safety warnings accurately."
            )

            result = OllamaAgent(
                ERDDAPTools(_client()),
                # gemma3:4b has no tool-calling support in this Ollama build;
                # qwen3:0.6b does, and the tool contract is what keeps every
                # numeric answer grounded in a real ERDDAP call.
                model=os.getenv("SALTY_OLLAMA_MODEL", "qwen3:0.6b"),
                base_url=os.getenv("SALTY_OLLAMA_URL", "http://127.0.0.1:11434"),
                mode=os.getenv("SALTY_AI_MODE", "live"),
            ).answer(query, mode=str(payload.get("mode", "normal")), context=location_context + language_context)
            if parsed.path == "/api/ai/query":
                return self._send(200, {
                    "response": result.get("response", "NOT AVAILABLE"),
                    "language": payload.get("language", "te-IN"),
                    "priority": "emergency" if any(term in query.lower() for term in ("sos", "救", "emergency", "drowning", "help")) else "normal",
                    "tool_calls": result.get("tool_calls", []),
                })
            return self._send(200, result)
        except (OllamaError, json.JSONDecodeError, ValueError) as exc:
            return self._send(503, {"error": str(exc), "status": "LLM NOT AVAILABLE"})
        except Exception as exc:
            return self._send(500, {"error": str(exc), "status": "NOT AVAILABLE"})

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    port = int(os.getenv("SALTY_API_PORT", "8010"))
    print(f"SALTY data API listening on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
