"""Small local HTTP adapter for the SALTY Python data layer.

Run with ``python3 api_server.py``. The default prototype mode uses the
existing ERDDAP client's explicitly labelled synthetic fallback when the
remote service is unavailable. Set ``SALTY_LIVE=1`` to use the live catalog.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

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
    if os.getenv("SALTY_LIVE") == "1":
        return ERDDAPClient(timeout=15, synthetic_fallback=False)
    return PrototypeClient(synthetic_fallback=True)


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
                return self._send(200, {"ok": True, "service": "salty-data-layer", "mode": "live" if os.getenv("SALTY_LIVE") == "1" else "prototype"})
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
            return self._send(404, {"error": "NOT FOUND"})
        except Exception as exc:
            return self._send(500, {"error": str(exc), "status": "NOT AVAILABLE"})

    def do_POST(self):
        parsed = urlparse(self.path)
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
            result = OllamaAgent(
                ERDDAPTools(_client()),
                model=os.getenv("SALTY_OLLAMA_MODEL", "gemma3:4b"),
                base_url=os.getenv("SALTY_OLLAMA_URL", "http://127.0.0.1:11434"),
                mode=os.getenv("SALTY_AI_MODE", "mock"),
            ).answer(query)
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
