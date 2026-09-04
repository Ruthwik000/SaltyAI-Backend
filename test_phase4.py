"""Phase 4 weather discovery and normalization tests/runner."""

from datetime import datetime, timezone
import sys

from erddap_client import ERDDAPClient
from weather_forecast import (
    VISAKHAPATNAM_BBOX,
    discover_weather_candidates,
    load_datasets,
    normalize_response,
    retrieve_period,
)


class FakeClient(ERDDAPClient):
    def __init__(self):
        super().__init__(base_url="https://example.test/erddap")

    def get_region_data(self, dataset_id, variable, bbox, time_range):
        assert bbox == VISAKHAPATNAM_BBOX
        return {"table": {"columnNames": ["time", "latitude", "longitude", variable], "rows": [[time_range[0], 17.5, 83.5, 31.2]]}}


def offline_tests():
    metadata = {"time_coverage": {"time_coverage_start": "2026-01-01T00:00:00Z", "time_coverage_end": "2026-12-31T00:00:00Z"}}
    datasets = [{"dataset_id": "weather_demo", "title": "Weather forecast", "metadata": {**metadata, "variables": [
        {"name": "air_temp", "units": "degC", "attributes": {"long_name": "Air temperature"}},
        {"name": "sst", "units": "degC", "attributes": {"long_name": "Sea surface temperature"}},
    ]}}]
    candidates = discover_weather_candidates(datasets)
    assert candidates["temperature"][0]["variable"] == "air_temp"
    assert not any(item["variable"] == "sst" for item in candidates["temperature"])
    fake = FakeClient()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    records = retrieve_period(fake, candidates["temperature"][0], "temperature", VISAKHAPATNAM_BBOX, "current", now)
    assert records == [{"timestamp": "2026-12-31T00:00:00Z", "latitude": 17.5, "longitude": 83.5, "parameter": "temperature", "value": 31.2, "unit": "degC", "dataset": "weather_demo"}]
    assert normalize_response({"table": {"columnNames": ["bad"], "rows": [[1]]}}, "x", "u", "d") == []
    print("Phase 4 offline tests: PASS")


def live_discovery():
    client = ERDDAPClient(verify_ssl=False, timeout=45)
    candidates = discover_weather_candidates(load_datasets(client))
    now = datetime.now(timezone.utc)
    print("DISCOVERED METADATA CANDIDATES")
    for parameter, options in candidates.items():
        if options:
            for option in options:
                print(f"{parameter}: dataset={option['dataset_id']} variable={option['variable']} unit={option['unit']}")
        else:
            print(f"{parameter}: NOT AVAILABLE")
    for period, label in (("current", "CURRENT DATA"), ("future", "FUTURE FORECAST DATA")):
        print(f"\n{label}")
        for parameter, options in candidates.items():
            records = []
            selected = next((option for option in options if _period_available(option, period, now)), None)
            if selected:
                try:
                    records = retrieve_period(client, selected, parameter, VISAKHAPATNAM_BBOX, period, now)
                except Exception:
                    records = []
            if records:
                print(f"{parameter}: {records[:3]}")
            else:
                print(f"{parameter}: NOT AVAILABLE")


def _period_available(candidate, period, now):
    coverage = candidate["metadata"].get("time_coverage", {})
    if not coverage.get("time_coverage_start") or not coverage.get("time_coverage_end"):
        return False
    from weather_forecast import _coverage_window
    return _coverage_window(candidate["metadata"], period, now) is not None


if __name__ == "__main__":
    offline_tests()
    if "--live" in sys.argv:
        live_discovery()
    else:
        print("Live discovery skipped (use: python3 test_phase4.py --live)")
