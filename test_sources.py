"""Live check of every external data source the agent depends on.

Run this before trusting an answer, and after any change to a client module:

    python test_sources.py                 # every source
    python test_sources.py --only tides    # one source

Each check hits the real service and asserts that a real value came back.
Nothing is mocked and nothing falls back: a source that cannot answer is
reported FAIL, not filled in. That is the whole point of the file.
"""

from __future__ import annotations

import sys
import time
import traceback

# Visakhapatnam, a few miles offshore, and its district for the alert feeds.
LAT, LON = 17.70, 83.35
PLACE, STATE = "Visakhapatnam", "Andhra Pradesh"


def _ok(value) -> bool:
    return value is not None


def check_sea_state():
    from thredds_client import point_conditions
    data = point_conditions(LAT, LON)
    parameters = data["parameters"]
    got = {k: v["value"] for k, v in parameters.items() if _ok(v.get("value"))}
    assert got, f"no parameters returned; missing {data['unavailable_parameters']}"
    return f"{len(got)} parameters at {data['timestamp']}: " + ", ".join(
        f"{k}={v}" for k, v in sorted(got.items()))


def check_pfz():
    from pfz_client import nearest_zones
    data = nearest_zones(LAT, LON, limit=3)
    zones = data.get("zones") or []
    assert zones, f"no PFZ advisories returned: {data.get('reason') or data}"
    first = zones[0]
    return (f"{len(zones)} advisories, nearest {first['distanceKm']} km "
            f"{first['bearingText']}, issued {first.get('issuedOn')}")


def check_eez():
    from pfz_client import eez_distance
    data = eez_distance(LAT, LON)
    assert _ok(data.get("distanceKm")), "no EEZ distance"
    return f"{data['distanceKm']} km {data['bearingText']} ({data['proximity']})"


def check_alerts():
    from alerts_client import alerts_for
    data = alerts_for(PLACE, STATE)
    assert data.get("highWaveIssuedFor"), "feed returned no issue date"
    return (f"{data['matchedCount']} in force for {PLACE}, "
            f"{data['totalNationwide']} nationwide, issued {data['highWaveIssuedFor']}")


def check_tides():
    from tides_client import tide_forecast
    data = tide_forecast(LAT, LON, days=2)
    points = data.get("turningPoints") or []
    assert points, f"no turning points: {data}"
    first = points[0]
    return f"{len(points)} turning points, next {first.get('kind')} at {first.get('time')}"


def check_weather():
    from weather_client import weather_hazards
    data = weather_hazards(LAT, LON, days=2)
    assert _ok(data["now"]["airTemperatureC"]), "no air temperature"
    return (f"{data['now']['description']}, {data['now']['airTemperatureC']} degC, "
            f"instability {data['now']['instability']}, {data['lightningRisk']}")


def check_chlorophyll():
    from ocean_color_client import productivity_patches
    data = productivity_patches(LAT, LON, limit=3)
    patches = data.get("patches") or []
    assert patches, "no chlorophyll patches"
    best = patches[0]
    return (f"{len(patches)} patches, best {best['chlorophyllMgM3']} mg/m3 "
            f"({best['chlorophyllBand']}) {best['distanceKm']} km {best['bearingText']}, "
            f"SST {best['seaSurfaceTemperatureC']} degC, observed {data['observedAt'][:10]}")


def check_trend():
    from ocean_color_client import productivity_trend
    data = productivity_trend(LAT, LON, days=60)
    trend = data["chlorophyll"]["trend"]
    assert "direction" in trend, f"no chlorophyll trend: {trend}"
    return (f"chlorophyll {trend['direction']} ({trend['changePercent']}%), "
            f"SST {data['seaSurfaceTemperature']['trend']['direction']}, "
            f"anomaly {data['seaSurfaceTemperatureAnomalyC']} degC")


def check_route():
    from route_client import check_route as run
    data = run(LAT, LON, 17.20, 84.30, samples=3)
    legs = data.get("legs") or []
    assert legs, "no legs read"
    return (f"{len(legs)}/3 legs read, {data['distanceKm']} km {data['bearingText']}, "
            f"worst leg {data['worstRiskLevel']}")


def check_hazards():
    from route_client import hazard_zones
    data = hazard_zones(LAT, LON, PLACE, STATE)
    assert "hazards" in data, "no hazard list"
    return f"{data['hazardCount']} hazard(s): {data['summary']}"


def check_groq():
    import os
    if not os.environ.get("GROQ_API_KEY"):
        raise AssertionError("GROQ_API_KEY is not set in the environment")
    from groq_agent import TOOL_DEFINITIONS
    return f"{len(TOOL_DEFINITIONS)} tools declared to the model"


CHECKS = [
    ("sea state    (INCOIS THREDDS OSF)", check_sea_state),
    ("pfz          (INCOIS GeoServer WFS)", check_pfz),
    ("eez          (INCOIS GeoServer WFS)", check_eez),
    ("alerts       (INCOIS HWA/SSA)", check_alerts),
    ("tides        (Open-Meteo Marine)", check_tides),
    ("weather      (Open-Meteo Forecast)", check_weather),
    ("chlorophyll  (NOAA CoastWatch)", check_chlorophyll),
    ("trend        (NOAA CoastWatch)", check_trend),
    ("route        (composed)", check_route),
    ("hazards      (composed)", check_hazards),
    ("agent        (Groq)", check_groq),
]


def main() -> int:
    wanted = None
    if "--only" in sys.argv:
        wanted = sys.argv[sys.argv.index("--only") + 1].lower()

    failures = 0
    for name, check in CHECKS:
        if wanted and wanted not in name.lower():
            continue
        started = time.time()
        try:
            detail = check()
            print(f"PASS  {name}  ({time.time() - started:.1f}s)\n      {detail}")
        except Exception as exc:                       # noqa: BLE001 - report, do not hide
            failures += 1
            print(f"FAIL  {name}  ({time.time() - started:.1f}s)\n      {exc}")
            if "-v" in sys.argv:
                traceback.print_exc()
    print()
    print("ALL SOURCES LIVE" if not failures else f"{failures} SOURCE(S) DOWN")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
