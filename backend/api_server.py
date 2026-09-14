"""Small local HTTP adapter for the SALTY Python data layer.

Run with ``python backend/api_server.py`` from the repository root.

There is no demo, prototype or synthetic mode. Every endpoint either returns
data that came off a real service or reports NOT AVAILABLE. Credentials and
settings come from a .env file beside this one.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

import field_api
from alerts_client import AlertsError, alerts_for
from erddap_client import ERDDAPClient, ERDDAPConnectionError
from marine_agent import ERDDAPTools, MarineAgent, AgentError
from pfz_client import PfzError
from ports import resolve as resolve_port
from route_client import RouteError
from thredds_client import ThreddsError
from prediction_models import build_predictions
from risk_features import build_72h_feature_dataset


BBOX = (17.0, 18.5, 82.5, 84.5)


def _client() -> ERDDAPClient:
    # ERDDAPClient can no longer fabricate: the synthetic-row generator was
    # removed outright rather than left behind a flag someone could flip.
    return ERDDAPClient(timeout=20, verify_ssl=False)


def _prediction_client() -> ERDDAPClient:
    """Prediction inputs must be real."""
    return ERDDAPClient(timeout=30)


def _datasets(client: ERDDAPClient):
    from weather_forecast import load_datasets

    return load_datasets(client)


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
    return ERDDAPClient(timeout=45, verify_ssl=False)


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
    lat_stride = max(1, round(sizes.get(lat_dim or "", 150) / 150))
    lon_stride = max(1, round(sizes.get(lon_dim or "", 150) / 150))
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
    def _send(self, status: int, payload: dict | list):
        body = json.dumps(payload, default=str).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # The browser hung up before we answered. Routine: the UI aborts
            # in-flight requests on navigation and on its own 6s timeout.
            # Without this the do_GET/do_POST error handler tries to send a
            # 500 down the same dead socket, raises a second time, and dumps
            # a full traceback per abandoned request.
            self.close_connection = True

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
                return self._send(200, {"ok": True, "service": "salty-data-layer", "mode": "live"})
            if parsed.path == "/api/alerts":
                place = (query.get("place") or [None])[0]
                state = (query.get("state") or [None])[0]
                try:
                    return self._send(200, alerts_for(place, state, limit=10))
                except AlertsError as exc:
                    return self._send(502, {"error": str(exc), "status": "NOT AVAILABLE"})

            if parsed.path == "/api/research/catalog":
                return self._send(200, {"datasets": _get_catalog()})
            if parsed.path == "/api/research/timeseries":
                dataset_id = (query.get("id") or [None])[0]
                cfg = RESEARCH_DATASETS.get(dataset_id or "")
                if not dataset_id or not cfg:
                    return self._send(404, {"error": "unknown dataset"})
                return self._send(200, _research_timeseries(dataset_id, cfg))
            if parsed.path == "/api/research/frames":
                dataset_id = (query.get("id") or [None])[0]
                cfg = RESEARCH_DATASETS.get(dataset_id or "")
                if not dataset_id or not cfg:
                    return self._send(404, {"error": "unknown dataset"})
                count = int((query.get("count") or ["12"])[0])
                return self._send(200, _research_frames(dataset_id, cfg, count))
            if parsed.path == "/api/research/frame.png":
                dataset_id = (query.get("id") or [None])[0]
                time_value = (query.get("time") or [None])[0]
                cfg = RESEARCH_DATASETS.get(dataset_id or "")
                if not cfg or not time_value or not dataset_id:
                    return self._send(404, {"error": "unknown dataset or time"})
                png = _research_frame_png(dataset_id, cfg, time_value)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                try:
                    self.wfile.write(png)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    # Frame stepping cancels in-flight images constantly.
                    self.close_connection = True
                return
            if parsed.path.startswith(("/api/fisherman/", "/api/operations/")):
                return self._field_get(parsed.path, query)
            return self._send(404, {"error": "NOT FOUND"})
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"error": str(exc), "status": "NOT AVAILABLE"})

    @staticmethod
    def _coords(query: dict) -> tuple[float, float]:
        return float((query.get("lat") or [""])[0]), float((query.get("lon") or [""])[0])

    def _field_get(self, path: str, query: dict):
        """Live data for the console's fisherman and operator screens."""
        try:
            if path == "/api/fisherman/zones":
                return self._send(200, field_api.pfz_zones(*self._coords(query)))
            if path.startswith("/api/fisherman/zones/"):
                detail = field_api.zone_detail(path.rsplit("/", 1)[-1])
                if detail is None:
                    return self._send(404, {"error": "unknown zone; list zones first"})
                return self._send(200, detail)
            if path == "/api/fisherman/conditions":
                return self._send(200, field_api.conditions(*self._coords(query)))
            if path == "/api/fisherman/forecast":
                return self._send(200, field_api.forecast(*self._coords(query)))
            if path == "/api/fisherman/alerts":
                return self._send(200, field_api.ocean_alerts(*self._coords(query)))
            if path == "/api/operations/fleet":
                return self._send(200, field_api.fleet(*self._coords(query)))
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        except (ThreddsError, PfzError, AlertsError, RouteError) as exc:
            return self._send(502, {"error": str(exc), "status": "NOT AVAILABLE"})
        return self._send(404, {"error": "NOT FOUND"})

    def _field_post(self, path: str):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            if path == "/api/fisherman/risk/assess":
                return self._send(200, field_api.trip_risk(payload))
            if path == "/api/fisherman/trip/start":
                return self._send(200, field_api.start_trip(payload))
            if path.startswith("/api/fisherman/trip/") and path.endswith(("/ping", "/end")):
                trip_id, action = path.split("/")[-2:]
                done = field_api.ping_trip(trip_id, payload) if action == "ping" else field_api.end_trip(trip_id)
                return self._send(200 if done else 404, {"ok": done})
            if path == "/api/operations/sar/predict":
                return self._send(200, field_api.sar_predict(payload))
        except (KeyError, TypeError, ValueError) as exc:
            return self._send(400, {"error": f"bad request: {exc}"})
        except (ThreddsError, PfzError, AlertsError, RouteError) as exc:
            return self._send(502, {"error": str(exc), "status": "NOT AVAILABLE"})
        return self._send(404, {"error": "NOT FOUND"})

    # The phone agent speaks BCP-47 ("te-IN"); the reasoning agent is told to
    # reply in a language by NAME. Passing the code straight through produced
    # the instruction "Reply in te-IN", which the model read as English.
    _SPOKEN_LANGUAGE = {
        "te": "Telugu", "hi": "Hindi", "ta": "Tamil", "ml": "Malayalam",
        "kn": "Kannada", "bn": "Bengali", "mr": "Marathi", "gu": "Gujarati",
        "or": "Odia", "pa": "Punjabi", "en": "English",
    }

    @classmethod
    def _language_name(cls, value: str) -> str:
        """"te-IN" -> "Telugu". A name already given is passed through."""
        text = str(value or "").strip()
        if not text:
            return "English"
        base = text.replace("_", "-").split("-")[0].lower()
        return cls._SPOKEN_LANGUAGE.get(base, text)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith(("/api/fisherman/", "/api/operations/")):
            try:
                return self._field_post(parsed.path)
            except Exception as exc:
                traceback.print_exc()
                return self._send(500, {"error": str(exc), "status": "NOT AVAILABLE"})
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
            # Prior turns from the client, so follow-ups have context. The web
            # console sends "history"; the phone agent sends
            # "conversation_history". Reading only the first meant every call
            # started from nothing and "what about tomorrow" meant nothing.
            history = payload.get("history")
            if not isinstance(history, list):
                history = payload.get("conversation_history")
            if not isinstance(history, list):
                history = []
            history = [turn for turn in history if isinstance(turn, dict)][-16:]
            location = payload.get("location")
            requested_language = str(payload.get("language", "English")).strip()
            language = self._language_name(requested_language)
            location_context = ""
            bbox = None
            call_lat = call_lon = None
            call_place = call_state = None
            if isinstance(location, dict):
                name = str(location.get("name", "")).strip()
                # The console sends lat/lon; the phone agent's Location model
                # spells them out. Accept both, or a caller's position is
                # dropped and every tool answers about Visakhapatnam. A key
                # that is present but null must fall through too.
                lat = location.get("lat")
                if lat is None:
                    lat = location.get("latitude")
                lon = location.get("lon")
                if lon is None:
                    lon = location.get("longitude")
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                    where = name or f"{lat:.3f}, {lon:.3f}"
                    location_context = f" The selected operating location is {where} ({lat}, {lon})."
                    # The safety tool otherwise defaults to Visakhapatnam, which
                    # would answer a Kochi skipper about the wrong coast. Give it
                    # the caller's own water, shaped like VISAKHAPATNAM_BBOX.
                    bbox = [lat - 0.75, lat + 0.75, lon - 1.0, lon + 1.0]
                    call_lat, call_lon = float(lat), float(lon)
                    # Only a REAL district name goes to the advisory feed; it
                    # matches on district, and a placeholder would match nothing
                    # while looking like a lookup that found nothing.
                    call_place = name or None
                    call_state = str(location.get("state") or "").strip() or None
            role = str(payload.get("role") or "").strip().lower()
            role_context = {
                "fisherman": " The user is a fisherman; keep it practical.",
                "researcher": " The user is a marine researcher; include datasets, units and sources.",
                "operator": " The user is a coastal operator; SAR, fleet and warning tools are relevant.",
            }.get(role, "")
            language_context = (
                f" Reply in {language}. Preserve technical values, units, dataset names, and safety warnings accurately."
            )

            # A phone call is always the fisherman voice, tightened for speech:
            # the answer goes straight into a text-to-speech engine and out of a
            # handset, where a markdown table is unreadable noise.
            mode = str(payload.get("mode", "normal"))
            if parsed.path == "/api/ai/query":
                mode = "voice"

            result = MarineAgent(
                ERDDAPTools(_client(), latitude=call_lat, longitude=call_lon,
                            place=call_place, state=call_state),
            ).answer(
                query,
                mode=mode,
                # language_context was previously built and dropped, which left
                # every reply in English regardless of what was asked.
                context=location_context + role_context + language_context,
                bbox=bbox,
                language=language,
                history=history,
            )
            for item in result.get("returned_data", []):
                mark = "data" if item.get("usable") else "NO DATA"
                print(f"  tool {item.get('tool')}: {mark}", file=sys.stderr, flush=True)
            if parsed.path == "/api/ai/query":
                return self._send(200, {
                    "response": result.get("response", "NOT AVAILABLE"),
                    # Echo the code the caller sent, not the language name:
                    # the phone agent hands it straight to the speech engine.
                    "language": requested_language or "te-IN",
                    "priority": "emergency" if any(term in query.lower() for term in ("sos", "救", "emergency", "drowning", "help")) else "normal",
                    "tool_calls": result.get("tool_calls", []),
                })
            return self._send(200, result)
        except (AgentError, json.JSONDecodeError, ValueError) as exc:
            print(f"  chat failed: {exc}", file=sys.stderr, flush=True)
            return self._send(503, {"error": str(exc), "status": "LLM NOT AVAILABLE"})
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"error": str(exc), "status": "NOT AVAILABLE"})

    def log_message(self, format, *args):
        """One line per request.

        The default handler logs are noisy, but silencing them entirely left
        the console blank while requests failed, with nothing to look at.
        """
        sys.stderr.write(f"{datetime.now():%H:%M:%S}  {self.command} {self.path[:110]}\n")
        sys.stderr.flush()


if __name__ == "__main__":
    port = int(os.getenv("SALTY_API_PORT", "8010"))
    print(f"SALTY data API listening on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
