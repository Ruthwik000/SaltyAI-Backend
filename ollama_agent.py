"""Phase 11 Ollama tool-calling bridge for the SALTY data layer.

The model is an interpreter and response writer only. All numerical answers
must come from an ERDDAP tool result; absent data is reported as unavailable.
"""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from erddap_client import ERDDAPClient, ERDDAPError
from historical import table_records


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

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        functions: dict[str, Callable[..., dict[str, Any]]] = {
            "search_datasets": self.search_datasets,
            "get_dataset_metadata": self.get_dataset_metadata,
            "get_point_data": self.get_point_data,
            "get_region_data": self.get_region_data,
            "get_time_series": self.get_time_series,
            "get_forecast": self.get_forecast,
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
]


class OllamaAgent:
    def __init__(self, tools: ERDDAPTools, model: str = "llama3.1", base_url: str = "http://127.0.0.1:11434", timeout: float = 120.0):
        self.tools = tools
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = json.dumps({"model": self.model, "messages": messages, "tools": TOOL_DEFINITIONS, "stream": False}).encode()
        request = Request(f"{self.base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except (OSError, URLError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollama is unavailable at {self.base_url}: {exc}") from exc

    def answer(self, user_query: str, trace: bool = True) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": "Use the supplied tools for all data. Never invent values, coordinates, warnings, or availability. If a tool returns no data, say NOT AVAILABLE. Answer only from returned tool data."}, {"role": "user", "content": user_query}]
        calls = []
        tool_results = []
        for _ in range(4):
            message = self._chat(messages).get("message", {})
            messages.append(message)
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                return {"user_query": user_query, "tool_calls": calls, "returned_data": tool_results, "response": message.get("content", "NOT AVAILABLE")}
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
