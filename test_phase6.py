"""Phase 6 tests and optional live coastal-region retrieval."""

from datetime import datetime, timezone
import sys

from erddap_client import ERDDAPClient
from fishing_zone import VISAKHAPATNAM_BBOX, build_fishing_zone_data


class FakeClient(ERDDAPClient):
    def get_point_data(self, dataset_id, variable, time, lat, lon):
        return {"table": {"columnNames": ["time", "latitude", "longitude", variable], "rows": [[time, lat, lon, 28.1]]}}

    def get_region_data(self, dataset_id, variable, bbox, time_range):
        return {"table": {"columnNames": ["time", "latitude", "longitude", variable], "rows": [[time_range[0], 17.5, 83.5, 28.1]]}}

    def get_time_series(self, dataset_id, variable, bbox, start, end):
        return {"table": {"columnNames": ["time", "latitude", "longitude", variable], "rows": [[start, 17.5, 83.5, 28.1], [end, 17.5, 83.5, 28.3]]}}


def offline_tests():
    metadata = {"time_coverage": {"time_coverage_start": "2020-01-01T00:00:00Z", "time_coverage_end": "2020-01-03T00:00:00Z"}, "variables": [
        {"name": "sea_temp", "units": "degC", "attributes": {"standard_name": "sea_surface_temperature"}},
        {"name": "CHL", "units": "mg/m3", "attributes": {"long_name": "chlorophyll a"}},
    ]}
    datasets = [{"dataset_id": "coastal_demo", "title": "Demo", "metadata": metadata}]
    result = build_fishing_zone_data(FakeClient(), datasets)
    assert set(result["parameters"]) == {"SST", "chlorophyll"}
    assert result["parameters"]["SST"]["point"][0]["value"] == 28.1
    assert len(result["parameters"]["SST"]["time_series"]) == 2
    assert result["parameters"]["SST"]["unit"] == "degC"
    assert result["sources"] == [
        {"dataset": "coastal_demo", "variable": "sea_temp", "unit": "degC"},
        {"dataset": "coastal_demo", "variable": "CHL", "unit": "mg/m3"},
    ]
    assert result["pfz_coordinates"] is None
    assert "currents" in result["unavailable_parameters"]
    print("Phase 6 offline tests: PASS")


def live_test():
    from fishing_zone import build_live_fishing_zone_data
    result = build_live_fishing_zone_data(ERDDAPClient(verify_ssl=False, timeout=45))
    print("\nFISHING-ZONE ENVIRONMENTAL DATA — VISAKHAPATNAM")
    print("Region:", result["region"])
    print("Timestamp:", result["timestamp"])
    print("Sources:", result["sources"])
    print("Unavailable:", result["unavailable_parameters"])
    print("PFZ coordinates: not generated")
    for parameter, data in result["parameters"].items():
        print(f"{parameter}: point={data['point'][:2]} region={data['region'][:2]} time_series={data['time_series'][:2]}")


if __name__ == "__main__":
    offline_tests()
    if "--live" in sys.argv:
        live_test()
    else:
        print("Live retrieval skipped (use: python3 test_phase6.py --live)")
