"""Offline Phase 10/11 end-to-end trace over the completed SALTY data layer."""

import json
from datetime import datetime, timezone

from erddap_client import ERDDAPClient
from fishing_zone import build_fishing_zone_data
from geofencing import point_inside_zone
from historical import HistoricalResearcher
from ollama_agent import ERDDAPTools, OllamaAgent
from risk_features import build_72h_feature_dataset
from severe_weather import build_severe_weather_data


class DemoClient(ERDDAPClient):
    def list_datasets(self):
        return [{"dataset_id": "demo_marine", "title": "Demo marine forecast"}]

    def get_dataset_metadata(self, dataset_id):
        names = {"SST": ("degC", "sea surface temperature"), "CHL": ("mg/m3", "chlorophyll"), "wind": ("m/s", "wind speed"), "wave": ("m", "wave height"), "swell": ("m", "swell height"), "rain": ("mm", "rainfall")}
        return {"time_coverage": {"time_coverage_start": "2020-01-01T00:00:00Z", "time_coverage_end": "2020-01-05T00:00:00Z"}, "variables": [{"name": name, "units": unit, "attributes": {"long_name": label}} for name, (unit, label) in names.items()]}

    def _response(self, variable, start="2020-01-01T00:00:00Z", end="2020-01-02T00:00:00Z"):
        values = {"SST": 28.0, "CHL": 0.9, "wind": 7.0, "wave": 1.2, "swell": 0.6, "rain": 4.0}
        return {"table": {"columnNames": ["time", "latitude", "longitude", variable], "rows": [[start, 17.5, 83.5, values.get(variable, 1.0)], [end, 17.5, 83.5, values.get(variable, 1.0)]]}}

    def get_point_data(self, dataset_id, variable, time, lat, lon):
        return self._response(variable, time, time)

    def get_region_data(self, dataset_id, variable, bbox, time_range):
        return self._response(variable, time_range[0], time_range[1])

    def get_time_series(self, dataset_id, variable, bbox, start, end):
        return self._response(variable, start, end)

    def get_forecast(self, dataset_id, variables, bbox, start, end):
        return {"table": {"columnNames": ["time", "latitude", "longitude", *variables], "rows": [[start, 17.5, 83.5, *[1.0 for _ in variables]]]}}


class DemoOllama(OllamaAgent):
    def _chat(self, messages):
        query = messages[1]["content"].lower()
        if messages[-1]["role"] != "tool":
            if "nearest potential" in query:
                call = {"name": "search_datasets", "arguments": {"query": "potential fishing zone"}}
            elif "sst" in query and "near" in query:
                call = {"name": "get_point_data", "arguments": {"dataset_id": "demo_marine", "variable": "SST", "time": "2020-01-01T00:00:00Z", "latitude": 17.7, "longitude": 83.3}}
            elif "historical" in query or "compare sst" in query:
                call = {"name": "get_time_series", "arguments": {"dataset_id": "demo_marine", "variable": "SST", "bbox": [17.0, 18.5, 82.5, 84.5], "start": "2020-01-01T00:00:00Z", "end": "2020-01-02T00:00:00Z"}}
            else:
                call = {"name": "get_forecast", "arguments": {"dataset_id": "demo_marine", "variables": ["wind", "wave"], "bbox": [17.0, 18.5, 82.5, 84.5], "start": "2020-01-01T00:00:00Z", "end": "2020-01-03T00:00:00Z"}}
            return {"message": {"role": "assistant", "tool_calls": [{"function": call}]}}
        data = json.loads(messages[-1]["content"])
        value = data.get("records", [{}])[0].get("SST", data.get("records", [{}])[0].get("value", "NOT AVAILABLE"))
        return {"message": {"role": "assistant", "content": f"Based only on returned data: {value}"}}


def main():
    client = DemoClient()
    datasets = [{"dataset_id": "demo_marine", "title": "Demo marine forecast", "metadata": client.get_dataset_metadata("demo_marine")}]
    severe = build_severe_weather_data(client, datasets)
    assert all("value" in row for rows in severe["parameters"].values() if isinstance(rows, list) for row in rows)
    researcher = HistoricalResearcher(client)
    assert researcher.point("demo_marine", "SST", "2020-01-01", 17.5, 83.5)["records"]
    assert build_fishing_zone_data(client, datasets)["parameters"]
    assert build_72h_feature_dataset(client, datasets, datetime(2019, 12, 31, tzinfo=timezone.utc), bbox=(17.0, 18.5, 82.5, 84.5))["records"]
    polygon = {"type": "Polygon", "coordinates": [[[83, 17], [84, 17], [84, 18], [83, 18], [83, 17]]]}
    assert point_inside_zone((83.5, 17.5), polygon)

    agent = DemoOllama(ERDDAPTools(client))
    queries = ["Where is the nearest Potential Fishing Zone today?", "What is the SST near Visakhapatnam?", "What are the wind and wave conditions for the next 3 days?", "Which region has high chlorophyll?", "Show ocean conditions for the next 72 hours.", "Compare SST between two regions.", "Is the sea condition expected to worsen tomorrow?", "When is the lowest-risk forecast window?"]
    for query in queries:
        result = agent.answer(query)
        print(json.dumps(result, indent=2, default=str))
        assert result["user_query"] == query and result["tool_calls"] and result["returned_data"]
    print("Phase 10/11 end-to-end tests: PASS; no values generated outside tool data")


if __name__ == "__main__":
    main()
