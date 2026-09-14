"""Offline Phase 2 tests for all generic retrieval functions."""

from erddap_client import ERDDAPClient


class FakeClient(ERDDAPClient):
    def __init__(self):
        super().__init__(base_url="https://example.test/erddap")
        self.queries = []

    def get_dataset_metadata(self, dataset_id):
        return {
            "dataset_id": dataset_id,
            "dimensions": ["time", "latitude", "longitude"],
            "time_coverage": {
                "time_coverage_start": "2020-01-01T00:00:00Z",
                "time_coverage_end": "2020-12-31T23:59:59Z",
            },
            "variables": [
                {"name": "sst", "units": "degree_C", "dimensions": [], "attributes": {}},
                {"name": "salinity", "units": "1e-3", "dimensions": [], "attributes": {}},
            ],
        }

    def _query(self, dataset_id, expression):
        self.queries.append(expression)
        return {"dataset_id": dataset_id, "expression": expression, "values": [[25.4]]}


def main():
    client = FakeClient()
    point = client.get_point_data("demo", "sst", "2020-06-01T00:00:00Z", 15, 75)
    region = client.get_region_data("demo", "sst", (10, 20, 70, 80), ("2020-06-01T00:00:00Z", "2020-06-02T00:00:00Z"))
    series = client.get_time_series("demo", "sst", {"min_lat": 10, "max_lat": 20, "min_lon": 70, "max_lon": 80}, "2020-06-01T00:00:00Z", "2020-06-03T00:00:00Z")
    forecast = client.get_forecast("demo", ["sst", "salinity"], (10, 20, 70, 80), "2020-06-01T00:00:00Z", "2020-06-03T00:00:00Z")
    assert point["values"] == [[25.4]]
    assert region["values"] == [[25.4]]
    assert series["values"] == [[25.4]]
    assert forecast["values"] == [[25.4]]
    assert len(client.queries) == 4
    assert all(query.startswith("sst[(2020-06") for query in client.queries[:3])
    assert forecast["expression"].startswith("sst[") and ",salinity[" in forecast["expression"]
    print("Point:   ", point["values"])
    print("Region:  ", region["values"])
    print("Series:  ", series["values"])
    print("Forecast:", forecast["values"])
    print("Phase 2 tests: PASS")


if __name__ == "__main__":
    main()
