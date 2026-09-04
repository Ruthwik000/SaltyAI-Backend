"""Prototype fetch-fallback checks for ERDDAP data endpoints."""

from erddap_client import ERDDAPClient, ERDDAPConnectionError


class FailingClient(ERDDAPClient):
    def get_dataset_metadata(self, dataset_id):
        return {"time_coverage": {"time_coverage_start": "2020-01-01T00:00:00Z", "time_coverage_end": "2020-01-05T00:00:00Z"}, "dimensions": ["time", "latitude", "longitude"], "variables": [{"name": "SST", "units": "degC"}, {"name": "wind", "units": "m/s"}]}

    def _query(self, dataset_id, expression):
        raise ERDDAPConnectionError("offline test")


def main():
    client = FailingClient()
    point = client.get_point_data("demo", "SST", "2020-01-01T00:00:00Z", 17.5, 83.5)
    region = client.get_region_data("demo", "wind", (17, 18, 82, 84), ("2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z"))
    series = client.get_time_series("demo", "SST", (17, 18, 82, 84), "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")
    forecast = client.get_forecast("demo", ["SST", "wind"], (17, 18, 82, 84), "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")
    assert all(response["synthetic"] for response in (point, region, series, forecast))
    assert 24 <= point["table"]["rows"][0][-1] <= 31
    assert 0 <= region["table"]["rows"][0][-1] <= 25
    print("Synthetic fetch fallback tests: PASS")


if __name__ == "__main__":
    main()
