"""Phase 5 tests and optional live 72-hour Visakhapatnam forecast check."""

from datetime import datetime, timezone
import sys

from erddap_client import ERDDAPClient
from risk_features import VISAKHAPATNAM_BBOX, build_72h_feature_dataset


class FakeClient(ERDDAPClient):
    def get_region_data(self, dataset_id, variable, bbox, time_range):
        assert bbox == VISAKHAPATNAM_BBOX
        return {"table": {"columnNames": ["time", "latitude", "longitude", variable], "rows": [
            [time_range[0], 17.5, 83.5, 20.0 if variable == "wind" else None],
            [time_range[1], 17.5, 83.5, 21.0 if variable == "wind" else 1.5],
        ]}}


def offline_tests():
    metadata = {"time_coverage": {"time_coverage_start": "2026-06-02T00:00:00Z", "time_coverage_end": "2026-06-10T00:00:00Z"}, "variables": [
        {"name": "wind", "units": "m/s", "attributes": {"long_name": "wind speed"}},
        {"name": "wave_h", "units": "m", "attributes": {"long_name": "significant wave height"}},
    ]}
    datasets = [{"dataset_id": "forecast_demo", "title": "Marine forecast", "metadata": metadata}]
    result = build_72h_feature_dataset(FakeClient(), datasets, datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert result["forecast_hours"] == 72
    assert result["forecast_timestamps"] == ["2026-06-02T00:00:00Z", "2026-06-05T00:00:00Z"]
    assert result["records"][0]["features"]["wind"] == 20.0
    assert result["records"][0]["features"]["wave height"] is None
    assert "wave height" in result["records"][0]["missing_values"]
    assert "rainfall" in result["unavailable_parameters"]
    print("Phase 5 offline tests: PASS")


def live_test():
    result = __import__("risk_features").discover_and_build_live(ERDDAPClient(verify_ssl=False, timeout=45))
    print("\n72-HOUR VISAKHAPATNAM MARINE-RISK FEATURE DATASET")
    print("Forecast timestamps:", result["forecast_timestamps"])
    print("Units:", result["feature_units"])
    print("Source datasets:", result["source_datasets"])
    print("Unavailable parameters:", result["unavailable_parameters"])
    print("Missing values:", result["missing_values"])
    print("Records:", result["records"][:3])


if __name__ == "__main__":
    offline_tests()
    if "--live" in sys.argv:
        live_test()
    else:
        print("Live 72-hour query skipped (use: python3 test_phase5.py --live)")
