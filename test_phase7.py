"""Phase 7 historical retrieval tests and optional live SST/CHL comparison."""

from datetime import datetime, timedelta
from pathlib import Path
import sys

from erddap_client import ERDDAPClient
from historical import HistoricalResearcher, export_csv, export_json


BBOX = (17.0, 18.5, 82.5, 84.5)
ZONES = {"north": (17.75, 18.5, 82.5, 84.5), "south": (17.0, 17.75, 82.5, 84.5)}


class FakeClient(ERDDAPClient):
    def get_point_data(self, dataset_id, variable, time, lat, lon):
        return {"table": {"columnNames": ["time", "latitude", "longitude", variable], "rows": [[time, lat, lon, 28.0]]}}

    def get_region_data(self, dataset_id, variable, bbox, time_range):
        return {"table": {"columnNames": ["time", "latitude", "longitude", variable], "rows": [[time_range[0], 17.5, 83.5, 28.0]]}}

    def get_time_series(self, dataset_id, variable, bbox, start, end):
        return {"table": {"columnNames": ["time", "latitude", "longitude", variable], "rows": [[start, 17.5, 83.5, 28.0], [end, 17.5, 83.5, 28.5]]}}

    def get_forecast(self, dataset_id, variables, bbox, start, end):
        return {"table": {"columnNames": ["time", "latitude", "longitude", *variables], "rows": [[start, 17.5, 83.5, 28.0, 1.2]]}}

    def get_netcdf(self, dataset_id, variable, bbox, start, end):
        return b"mock-netcdf"


def offline_tests():
    researcher = HistoricalResearcher(FakeClient())
    point = researcher.point("demo", "SST", "2020-01-01", 17.5, 83.5)
    region = researcher.region("demo", "SST", BBOX, "2020-01-01", "2020-01-02")
    series = researcher.time_series("demo", "SST", BBOX, "2020-01-01", "2020-01-02")
    comparison = researcher.compare_parameters("demo", ["SST", "CHL"], BBOX, "2020-01-01", "2020-01-02")
    cross_dataset = researcher.compare_datasets(
        {"SST": ("sst_historical", "SST"), "chlorophyll": ("chlorophyll_historical", "CHL")},
        BBOX, "2020-01-01", "2020-01-02",
    )
    zones = researcher.compare_zones("demo", "SST", ZONES, "2020-01-01", "2020-01-02")
    assert point["records"][0]["SST"] == 28.0
    assert len(region["records"]) == 1 and len(series["records"]) == 2
    assert comparison["records"][0]["CHL"] == 1.2
    assert cross_dataset["records"][0]["SST"] == 28.0
    assert cross_dataset["records"][0]["chlorophyll"] == 28.0
    assert set(zones["zones"]) == {"north", "south"}
    output = Path("/tmp/salty_phase7_test")
    export_csv(series, output.with_suffix(".csv"))
    export_json(zones, output.with_suffix(".json"))
    netcdf = researcher.netcdf("demo", "SST", BBOX, "2020-01-01", "2020-01-02", output.with_suffix(".nc"))
    assert output.with_suffix(".csv").exists() and output.with_suffix(".json").exists() and netcdf.read_bytes() == b"mock-netcdf"
    print("Phase 7 offline tests: PASS")


def live_tests():
    client = ERDDAPClient(verify_ssl=False, timeout=45)
    researcher = HistoricalResearcher(client)
    sst_meta = client.get_dataset_metadata("AMSRE_MONTHLY_GLOBAL")
    chl_meta = client.get_dataset_metadata("incois_oceansat2_datasets")
    sst_start = sst_meta["time_coverage"]["time_coverage_start"]
    chl_start = chl_meta["time_coverage"]["time_coverage_start"]
    sst_end = (datetime.fromisoformat(sst_start.replace("Z", "+00:00")) + timedelta(days=3)).isoformat().replace("+00:00", "Z")
    chl_end = (datetime.fromisoformat(chl_start.replace("Z", "+00:00")) + timedelta(days=3)).isoformat().replace("+00:00", "Z")
    point = researcher.point("AMSRE_MONTHLY_GLOBAL", "SST", sst_start, 17.5, 83.5)
    sst = researcher.compare_zones("AMSRE_MONTHLY_GLOBAL", "SST", ZONES, sst_start, sst_end)
    chl = researcher.time_series("incois_oceansat2_datasets", "CHL", BBOX, chl_start, chl_end)
    csv_path = export_csv(sst["zones"]["north"], "/tmp/salty_sst_north.csv")
    json_path = export_json(chl, "/tmp/salty_chlorophyll.json")
    netcdf_path = researcher.netcdf("AMSRE_MONTHLY_GLOBAL", "SST", BBOX, sst_start, sst_end, "/tmp/salty_sst.nc")
    print("\nHistorical SST point records:", len(point["records"]))
    print("Historical SST zone comparison:", {name: len(data["records"]) for name, data in sst["zones"].items()})
    print("Historical chlorophyll records:", len(chl["records"]))
    print("Exports:", csv_path, json_path, netcdf_path)
    print("Phase 7 live tests: PASS")


if __name__ == "__main__":
    offline_tests()
    if "--live" in sys.argv:
        live_tests()
    else:
        print("Live retrieval skipped (use: python3 test_phase7.py --live)")
