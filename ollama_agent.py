"""Phase 11 Ollama tool-calling bridge for the SALTY data layer.

The model is an interpreter and response writer only. All numerical answers
must come from an ERDDAP tool result; absent data is reported as unavailable.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from erddap_client import ERDDAPClient, ERDDAPError
from historical import table_records
from prediction_models import build_predictions
from risk_features import build_72h_feature_dataset
from weather_forecast import VISAKHAPATNAM_BBOX, load_datasets


class OllamaError(RuntimeError):
    pass


class ERDDAPTools:
    """Allowlist of data tools exposed to Ollama."""

    def __init__(self, client: ERDDAPClient):
        self.client = client

    def search_datasets(self, query: str) -> dict[str, Any]:
        terms = str(query).lower().split()
        try:
            datasets = self.client.list_datasets()
        except ERDDAPError:
            return {"query": query, "datasets": "NOT AVAILABLE"}
        return {"query": query, "datasets": [dataset for dataset in datasets if all(term in f"{dataset['dataset_id']} {dataset['title']}".lower() for term in terms)]}

    def get_dataset_metadata(self, dataset_id: str) -> dict[str, Any]:
        try:
            return self.client.get_dataset_metadata(dataset_id)
        except ERDDAPError:
            return {"dataset_id": dataset_id, "status": "NOT AVAILABLE"}

    @staticmethod
    def _normalized(response: dict[str, Any]) -> dict[str, Any]:
        result = {"records": table_records(response)}
        if response.get("synthetic"):
            result["synthetic"] = True
            result["warning"] = response.get("warning", "SYNTHETIC TEST DATA")
        return result

    def get_point_data(self, dataset_id: str, variable: str, time: str, latitude: float, longitude: float) -> dict[str, Any]:
        return self._normalized(self.client.get_point_data(dataset_id, variable, time, latitude, longitude))

    def get_region_data(self, dataset_id: str, variable: str, bbox: list[float], time_range: list[str]) -> dict[str, Any]:
        return self._normalized(self.client.get_region_data(dataset_id, variable, bbox, tuple(time_range)))

    def get_time_series(self, dataset_id: str, variable: str, bbox: list[float], start: str, end: str) -> dict[str, Any]:
        return self._normalized(self.client.get_time_series(dataset_id, variable, bbox, start, end))

    def get_forecast(self, dataset_id: str, variables: list[str], bbox: list[float], start: str, end: str) -> dict[str, Any]:
        return self._normalized(self.client.get_forecast(dataset_id, variables, bbox, start, end))

    def get_marine_safety_forecast(self, bbox: list[float] | None = None) -> dict[str, Any]:
        """Assess sailing conditions from the real next-72-hour forecast data."""
        try:
            region = tuple(bbox or VISAKHAPATNAM_BBOX)
            features = build_72h_feature_dataset(self.client, load_datasets(self.client), bbox=region)
            return build_predictions(features)
        except (ERDDAPError, TypeError, ValueError) as exc:
            return {"status": "NOT AVAILABLE", "reason": str(exc), "marine_risk": [], "fishing_window": {"status": "NOT AVAILABLE"}}

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        functions: dict[str, Callable[..., dict[str, Any]]] = {
            "search_datasets": self.search_datasets,
            "get_dataset_metadata": self.get_dataset_metadata,
            "get_point_data": self.get_point_data,
            "get_region_data": self.get_region_data,
            "get_time_series": self.get_time_series,
            "get_forecast": self.get_forecast,
            "get_marine_safety_forecast": self.get_marine_safety_forecast,
        }
        if name not in functions:
            raise OllamaError(f"Tool is not allowed: {name}")
        return functions[name](**arguments)


TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "search_datasets", "description": "Search the ERDDAP dataset catalog.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "get_dataset_metadata", "description": "Get metadata and variables for one ERDDAP dataset.", "parameters": {"type": "object", "properties": {"dataset_id": {"type": "string"}}, "required": ["dataset_id"]}}},
    {"type": "function", "function": {"name": "get_point_data", "description": "Get one value at a time and lon/lat point.", "parameters": {"type": "object", "properties": {"dataset_id": {"type": "string"}, "variable": {"type": "string"}, "time": {"type": "string"}, "latitude": {"type": "number"}, "longitude": {"type": "number"}}, "required": ["dataset_id", "variable", "time", "latitude", "longitude"]}}},
    {"type": "function", "function": {"name": "get_region_data", "description": "Get a spatial region for a time range. bbox is min_lat,max_lat,min_lon,max_lon.", "parameters": {"type": "object", "properties": {"dataset_id": {"type": "string"}, "variable": {"type": "string"}, "bbox": {"type": "array", "items": {"type": "number"}}, "time_range": {"type": "array", "items": {"type": "string"}}}, "required": ["dataset_id", "variable", "bbox", "time_range"]}}},
    {"type": "function", "function": {"name": "get_time_series", "description": "Get a time series over a spatial region.", "parameters": {"type": "object", "properties": {"dataset_id": {"type": "string"}, "variable": {"type": "string"}, "bbox": {"type": "array", "items": {"type": "number"}}, "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["dataset_id", "variable", "bbox", "start", "end"]}}},
    {"type": "function", "function": {"name": "get_forecast", "description": "Get several variables over the same forecast window and region.", "parameters": {"type": "object", "properties": {"dataset_id": {"type": "string"}, "variables": {"type": "array", "items": {"type": "string"}}, "bbox": {"type": "array", "items": {"type": "number"}}, "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["dataset_id", "variables", "bbox", "start", "end"]}}},
    {"type": "function", "function": {"name": "get_marine_safety_forecast", "description": "Get a real 72-hour marine forecast and risk assessment for sailing, boating, fishing, departure planning, sea state, wind, waves, swell, currents, rainfall, and hazards. Use this whenever the user asks whether conditions are safe or suitable.", "parameters": {"type": "object", "properties": {"bbox": {"type": "array", "items": {"type": "number"}, "description": "Optional min_lat,max_lat,min_lon,max_lon; defaults to Visakhapatnam."}}, "required": []}}},
]


# Development-only context used when SALTY_AI_MODE=mock. It is deliberately
# marked as demo data and is never presented as a live observation or warning.
MOCK_MARINE_CONTEXT: dict[str, Any] = {
    "status": "DEMO DATA",
    "location": "Visakhapatnam",
    "forecast_window": "next 72 hours (development demo)",
    "wind": {"speed_knots": 14, "speed_mps": 7.2, "direction": "ENE", "direction_degrees": 67.5},
    "significant_wave": {"height_m": 1.6, "period_seconds": 8.0},
    "swell": {"height_m": 0.9, "period_seconds": 11.0},
    "surface_current": {"speed_mps": 0.45, "direction": "NE"},
    "sea_surface_temperature_c": 28.4,
    "rainfall_mm": 2.0,
    "marine_risk": {"level": "low to moderate", "score": 28},
    "fishing_window": {"status": "favorable", "local_time": "06:00-11:00"},
}


class OllamaAgent:
    def __init__(self, tools: ERDDAPTools, model: str = "llama3.1", base_url: str = "http://127.0.0.1:11434", timeout: float = 120.0, mode: str | None = None):
        self.tools = tools
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.mode = (mode or os.getenv("SALTY_AI_MODE", "live")).lower()

    def _chat(self, messages: list[dict[str, Any]], include_tools: bool = True) -> dict[str, Any]:
        payload_data: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        if include_tools:
            payload_data["tools"] = TOOL_DEFINITIONS
        payload = json.dumps(payload_data).encode()
        request = Request(f"{self.base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except (OSError, URLError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollama is unavailable at {self.base_url}: {exc}") from exc

    @staticmethod
    def _is_unsupported_refusal(response: str) -> bool:
        text = response.lower()
        refusal_patterns = (
            r"no .*\b(function|tool)\b.*\b(weather|sail|sailing|safety)\b",
            r"\b(function|tool)s?\b.*\bnot available\b.*\b(weather|sail|sailing|safety)\b",
            r"\btools?\b.*\bnot\b.*\b(weather|sail|sailing|safety)\b",
            r"\btools?\b.*\b(?:do not|don't|do not include)\b.*\b(functions?|weather|sail|sailing|safety)\b",
            r"\bno functions?\b.*\b(weather|sail|sailing|safety)\b",
            r"\bnot (?:about|for) weather or sailing safety\b",
            r"cannot answer.*\b(weather|sail|sailing|safety)\b",
            r"outside (?:my|the).*capabilit",
            r"not (?:able|equipped) to (?:answer|determine).*\b(weather|sail|sailing|safety)\b",
            r"\b(?:cannot|can't|unable)\b.*\bdetermine\b.*\b(?:fishing|sailing|weather|safety)\b",
        )
        return any(re.search(pattern, text) for pattern in refusal_patterns)

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
        response = f"The live forecast indicates {level} marine risk for the lowest-risk period at {timestamp} (risk score {score}/100)."
        if missing:
            response += f" Some parameters were unavailable: {', '.join(map(str, missing))}."
        response += " Check the latest forecast before departure and follow local maritime warnings."
        return response

    @staticmethod
    def _mock_safety_response() -> str:
        return (
            "Based on the development demo forecast for Visakhapatnam, conditions look generally suitable "
            "for normal fishing or sailing: wind is 14 kt from ENE, significant waves are 1.6 m, swell is "
            "0.9 m, and marine risk is low to moderate (28/100). A favorable demo fishing window is "
            "06:00-11:00 local time. This is mock data, not a live safety clearance; check current INCOIS "
            "forecasts and local warnings before departure."
        )

    @staticmethod
    def _mock_general_response() -> str:
        return (
            "The Visakhapatnam development demo forecast shows 14 kt ENE wind, 1.6 m significant waves, "
            "0.9 m swell, 0.45 m/s NE surface current, and 28.4°C sea-surface temperature. "
            "This is mock data for testing, not a live marine forecast."
        )

    def _answer_with_mock_context(self, user_query: str) -> dict[str, Any]:
        """Answer from a fixed demo forecast without asking Ollama to call tools."""
        context = json.dumps(MOCK_MARINE_CONTEXT, indent=2)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are SALTY's helpful marine assistant. Answer the user's question directly using only "
                    "the DEMO marine forecast below. You can discuss weather, wind, waves, swell, currents, "
                    "fishing, sailing, and safety. Never say that tools or functions are unavailable, because "
                    "you already have the forecast context. Clearly call it development demo/mock data when "
                    "discussing current or real-world safety. Do not invent values that are not in the context.\n\n"
                    f"DEMO MARINE FORECAST:\n{context}"
                ),
            },
            {"role": "user", "content": user_query},
        ]
        response = self._chat(messages, include_tools=False).get("message", {}).get("content", "").strip()
        safety_question = bool(re.search(r"\b(safe|safety|sail|sailing|boat|fishing|fish|sea condition|departure|hazard|danger|risk|worsen)\b", user_query.lower()))
        if not response or self._is_unsupported_refusal(response):
            response = self._mock_safety_response() if safety_question else self._mock_general_response()
        return {
            "user_query": user_query,
            "tool_calls": [],
            "returned_data": [{"tool": "mock_marine_forecast", "data": MOCK_MARINE_CONTEXT}],
            "response": response,
            "synthetic": True,
        }

    def answer(self, user_query: str, trace: bool = True) -> dict[str, Any]:
        if self.mode == "mock":
            return self._answer_with_mock_context(user_query)
        messages: list[dict[str, Any]] = [{"role": "system", "content": "You are SALTY's helpful marine assistant. You can answer questions about sailing, boating, fishing, weather, sea conditions, forecasts, and safety. Use the supplied tools whenever factual or current data is needed. For safety, departure, or suitability questions, call get_marine_safety_forecast first and use its result in your answer. Never invent live values, coordinates, warnings, or source availability. If the data source returns no usable data, say that the live assessment is unavailable and offer general guidance; do not claim that safety or weather questions are outside your capabilities."}, {"role": "user", "content": user_query}]
        calls = []
        tool_results = []
        safety_question = re.search(r"\b(safe|safety|sail|sailing|boat|fishing|fish|sea condition|departure|hazard|danger|risk|worsen)\b", user_query.lower())
        if safety_question:
            # Small local models sometimes respond that safety is outside the
            # tool set even when a suitable tool is available. Force the
            # grounded safety call so the final answer is based on real data.
            name = "get_marine_safety_forecast"
            arguments: dict[str, Any] = {}
            data = self.tools.execute(name, arguments)
            calls.append({"tool": name, "arguments": arguments})
            tool_results.append({"tool": name, "data": data})
            messages.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]})
            messages.append({"role": "tool", "tool_name": name, "content": json.dumps(data, default=str)})
            # Safety answers must remain grounded in the forecast result. Some
            # small local models ignore the tool result and emit a refusal, so
            # do not give the model an opportunity to replace this assessment.
            return {
                "user_query": user_query,
                "tool_calls": calls,
                "returned_data": tool_results,
                "response": self._grounded_safety_response(data),
            }
        for _ in range(4):
            message = self._chat(messages).get("message", {})
            messages.append(message)
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                response = message.get("content", "NOT AVAILABLE")
                if safety_question and self._is_unsupported_refusal(response):
                    response = self._grounded_safety_response(tool_results[-1]["data"])
                return {"user_query": user_query, "tool_calls": calls, "returned_data": tool_results, "response": response}
            for call in tool_calls:
                function = call.get("function", {})
                name, arguments = function.get("name"), function.get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                data = self.tools.execute(name, arguments)
                calls.append({"tool": name, "arguments": arguments})
                tool_results.append({"tool": name, "data": data})
                messages.append({"role": "tool", "tool_name": name, "content": json.dumps(data, default=str)})
        raise OllamaError("Ollama exceeded the maximum tool-call rounds")
