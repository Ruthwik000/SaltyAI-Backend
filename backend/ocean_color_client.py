"""Satellite chlorophyll and sea surface temperature, from NOAA CoastWatch ERDDAP.

Why not INCOIS
--------------
INCOIS runs its own ERDDAP, and it was the obvious place to look. It is an
archive, and its ocean-colour holdings have stopped:

    IRS_chlorophyll_datasets        IRS P4 OCM-Chlorophyll   2003-01-05 .. 2006-03-21
    incois_oceansat2_datasets       Oceansat-2 OCM           2011-02-02 .. 2020-05-01
    incois_argo_sst_weekly          ARGO SST weekly          2009-01-07 .. 2010-12-29
    NOAA_AVHRR_AMSR_datasets        Daily OI SST             2002-06-01 .. 2011-10-04

Checked against erddap.incois.gov.in/erddap/tabledap/allDatasets.json on
2026-09-08: the newest chlorophyll pixel INCOIS serves is twenty years old.
Nothing there can answer "where is the water productive today".

NOAA CoastWatch can, is free and keyless, and speaks ERDDAP, so the same
query grammar applies. Verified live on 2026-09-08 off Visakhapatnam:

    noaacwNPPN20VIIRSDINEOFDaily          chlor_a       2026-09-05  0.24-0.98 mg/m3
    noaacrwsstDaily                       analysed_sst  2026-09-06  29.4 degC
    noaacrwsstanomalybaselineDaily        anomaly       2026-09-01  -0.26 degC

The chlorophyll product is the DINEOF gap-filled one on purpose. The raw
swath is full of cloud holes, and over the Bay of Bengal in monsoon a raw
product answers "no data" on most days a fisherman would ask.

Three traps, all found by querying the server rather than by assuming:
  * chlorophyll carries a fourth axis, altitude, and the query fails without it;
  * chlorophyll and the anomaly grid run latitude NORTH to SOUTH, so their
    range has to be written high:low, while the SST grid runs south to north;
  * the three grids are 9 km, 5 km and 5 km, so a chlorophyll cell never
    coincides with an SST cell and has to be matched to the nearest one.

This is NOT the INCOIS PFZ advisory. PFZ is the official product and stays
the thing to quote; this is the raw satellite signal behind that kind of
advisory, useful on the days when no PFZ bulletin has been issued.
"""

from __future__ import annotations

import json
import math
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pfz_client import _bearing_deg, _distance_nm, compass

BASE = "https://coastwatch.noaa.gov/erddap/griddap"
SOURCE = "NOAA CoastWatch satellite (VIIRS chlorophyll, CoralTemp SST)"

CHLOROPHYLL = {
    "dataset": "noaacwNPPN20VIIRSDINEOFDaily",
    "variable": "chlor_a",
    "altitude": True,
    "lat_descending": True,
    "cell_deg": 0.0834,
    "unit": "mg/m3",
    "label": "NOAA S-NPP/NOAA-20 VIIRS gap-filled chlorophyll-a, 9 km daily",
}
SST = {
    "dataset": "noaacrwsstDaily",
    "variable": "analysed_sst",
    "altitude": False,
    "lat_descending": False,
    "cell_deg": 0.05,
    "unit": "degC",
    "label": "NOAA Coral Reef Watch CoralTemp sea surface temperature, 5 km daily",
}
SST_ANOMALY = {
    "dataset": "noaacrwsstanomalybaselineDaily",
    "variable": "sea_surface_temperature_anomaly",
    "altitude": False,
    "lat_descending": True,
    "cell_deg": 0.05,
    "unit": "degC",
    "label": "NOAA Coral Reef Watch SST anomaly against the 1985-2012 baseline",
}

# Chlorophyll is the standard proxy for how much food is in the water. These
# bands are the ones ocean-colour literature uses for case-1 tropical shelf
# water; they are descriptive, not a catch prediction.
CHLOROPHYLL_BANDS = (
    (3.0, "very high"),
    (1.0, "high"),
    (0.3, "moderate"),
    (0.1, "low"),
    (0.0, "very low"),
)

_CACHE_TTL = 3 * 3600.0
_cache: dict[str, tuple[float, Any]] = {}
_CTX = ssl._create_unverified_context()


class OceanColorError(RuntimeError):
    """Raised when the satellite grid cannot be read."""


def band(value: float | None) -> str | None:
    if value is None:
        return None
    for threshold, label in CHLOROPHYLL_BANDS:
        if value >= threshold:
            return label
    return "very low"


def _span(low: float, high: float, descending: bool) -> str:
    first, second = (high, low) if descending else (low, high)
    return f"({first:.4f}):({second:.4f})"


def _grid(spec: dict[str, Any], lat_low: float, lat_high: float,
          lon_low: float, lon_high: float, when: str = "(last)",
          timeout: float = 45.0) -> list[dict[str, Any]]:
    """One griddap slice, returned as a list of {time, latitude, longitude, value}."""
    query = f"{spec['variable']}[{when}]"
    if spec["altitude"]:
        query += "[(0.0)]"
    query += f"[{_span(lat_low, lat_high, spec['lat_descending'])}]"
    query += f"[{_span(lon_low, lon_high, False)}]"
    url = f"{BASE}/{spec['dataset']}.json?" + query.replace("[", "%5B").replace("]", "%5D")

    cached = _cache.get(url)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    request = Request(url, headers={"User-Agent": "SALTY/1.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout, context=_CTX) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        raise OceanColorError(
            f"{spec['dataset']} returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise OceanColorError(
            f"Could not reach NOAA CoastWatch for {spec['dataset']}: {exc}") from exc

    table = payload.get("table") or {}
    columns = table.get("columnNames") or []
    try:
        i_time = columns.index("time")
        i_lat = columns.index("latitude")
        i_lon = columns.index("longitude")
        i_value = columns.index(spec["variable"])
    except ValueError as exc:
        raise OceanColorError(f"{spec['dataset']} returned an unexpected shape") from exc

    rows = [
        {
            "time": row[i_time],
            "latitude": float(row[i_lat]),
            "longitude": float(row[i_lon]),
            "value": None if row[i_value] is None else float(row[i_value]),
        }
        for row in (table.get("rows") or [])
    ]
    _cache[url] = (time.time(), rows)
    return rows


def _nearest(rows: list[dict[str, Any]], lat: float, lon: float) -> dict[str, Any] | None:
    """Closest cell that actually has a value. Grids differ in resolution."""
    best = None
    best_gap = None
    for row in rows:
        if row["value"] is None:
            continue
        gap = (row["latitude"] - lat) ** 2 + (row["longitude"] - lon) ** 2
        if best_gap is None or gap < best_gap:
            best, best_gap = row, gap
    return best


def productivity_patches(lat: float, lon: float, box_deg: float = 0.6,
                         limit: int = 5) -> dict[str, Any]:
    """Where the water near the user is carrying the most food, with its SST."""
    chlorophyll = _grid(CHLOROPHYLL, lat - box_deg, lat + box_deg,
                        lon - box_deg, lon + box_deg)
    temperature = _grid(SST, lat - box_deg, lat + box_deg,
                        lon - box_deg, lon + box_deg)

    cells = [row for row in chlorophyll if row["value"] is not None]
    if not cells:
        raise OceanColorError(
            "the satellite returned no chlorophyll around this position")

    cells.sort(key=lambda row: row["value"], reverse=True)

    # Adjacent pixels of one bloom are not five places to go. Keep the
    # strongest cell of each patch and skip anything within ~0.15 degrees.
    picked: list[dict[str, Any]] = []
    for row in cells:
        if any(abs(row["latitude"] - kept["latitude"]) < 0.15
               and abs(row["longitude"] - kept["longitude"]) < 0.15
               for kept in picked):
            continue
        picked.append(row)
        if len(picked) >= max(1, int(limit)):
            break

    patches = []
    for row in picked:
        near_sst = _nearest(temperature, row["latitude"], row["longitude"])
        distance_nm = _distance_nm(lat, lon, row["latitude"], row["longitude"])
        bearing = _bearing_deg(lat, lon, row["latitude"], row["longitude"])
        patches.append({
            "latitude": round(row["latitude"], 4),
            "longitude": round(row["longitude"], 4),
            "chlorophyllMgM3": round(row["value"], 3),
            "chlorophyllBand": band(row["value"]),
            "seaSurfaceTemperatureC": (
                round(near_sst["value"], 2) if near_sst else None),
            "distanceNM": round(distance_nm, 1),
            "distanceKm": round(distance_nm * 1.852, 1),
            "bearingDegrees": round(bearing),
            "bearingText": compass(bearing),
            "observedAt": row["time"],
        })

    values = [row["value"] for row in cells]
    sst_values = [row["value"] for row in temperature if row["value"] is not None]
    return {
        "source": SOURCE,
        "note": ("Raw satellite chlorophyll and sea surface temperature from NOAA "
                 "CoastWatch, not an INCOIS product and not a PFZ advisory. INCOIS "
                 "stopped serving chlorophyll in 2020. Chlorophyll shows how much "
                 "food the water is carrying; it is not a count of fish."),
        "datasets": [CHLOROPHYLL["label"], SST["label"]],
        "position": {"latitude": lat, "longitude": lon},
        "searchRadiusKm": round(box_deg * 111.0),
        "observedAt": picked[0]["time"] if picked else None,
        "chlorophyllMaxMgM3": round(max(values), 3),
        "chlorophyllMedianMgM3": round(sorted(values)[len(values) // 2], 3),
        "seaSurfaceTemperatureRangeC": (
            [round(min(sst_values), 2), round(max(sst_values), 2)]
            if sst_values else None),
        "patches": patches,
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One value per day.

    A small lat/lon box on a 9 km grid is two by two cells, so a 60-day
    request comes back as roughly 240 rows, not 60. Flattening those into a
    series gave four dates' worth of "recent" history and duplicate dates in
    the output. Average the cells for each day instead, and drop days where
    every cell was cloud.
    """
    buckets: dict[str, list[float]] = {}
    for row in rows:
        if row["value"] is None:
            continue
        buckets.setdefault(row["time"][:10], []).append(row["value"])
    return [
        {"date": date, "value": round(sum(values) / len(values), 3)}
        for date, values in sorted(buckets.items())
    ]


def _trend(series: list[dict[str, Any]],
           absolute_threshold: float | None = None) -> dict[str, Any]:
    """Compare the mean of the first third with the mean of the last third.

    A least-squares slope on a cloud-affected daily series is dominated by the
    noisiest days. Thirds are blunt, but they say the same thing and they are
    explainable to the person reading the answer.
    """
    values = [entry["value"] for entry in series if entry["value"] is not None]
    if len(values) < 6:
        return {"status": "NOT AVAILABLE", "reason": "too few clear days"}
    third = max(2, len(values) // 3)
    earlier = _mean(values[:third])
    later = _mean(values[-third:])
    if earlier in (None, 0) or later is None:
        return {"status": "NOT AVAILABLE", "reason": "too few clear days"}
    change = (later - earlier) / abs(earlier) * 100.0
    # Percent is the right test for chlorophyll, which moves over an order of
    # magnitude. It is the wrong test for sea temperature: half a degree is a
    # real change to a shoal and only about 1.5% of 29 degC, so temperature is
    # judged on the absolute shift instead.
    if absolute_threshold is not None:
        shift = later - earlier
        if shift > absolute_threshold:
            direction = "rising"
        elif shift < -absolute_threshold:
            direction = "falling"
        else:
            direction = "about the same"
    elif change > 15:
        direction = "rising"
    elif change < -15:
        direction = "falling"
    else:
        direction = "about the same"
    return {
        "earlierMean": round(earlier, 3),
        "recentMean": round(later, 3),
        "changePercent": round(change, 1),
        "absoluteChange": round(later - earlier, 3),
        "direction": direction,
        "daysCompared": third,
    }


def productivity_trend(lat: float, lon: float, days: int = 60) -> dict[str, Any]:
    """How chlorophyll and SST at this spot have moved over recent weeks."""
    days = max(14, min(int(days), 180))
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    when = f"({start}):(last)"

    chlorophyll = _grid(CHLOROPHYLL, lat - 0.05, lat + 0.05,
                        lon - 0.05, lon + 0.05, when=when, timeout=60.0)
    temperature = _grid(SST, lat - 0.03, lat + 0.03,
                        lon - 0.03, lon + 0.03, when=when, timeout=60.0)
    try:
        anomaly_rows = _grid(SST_ANOMALY, lat - 0.03, lat + 0.03,
                             lon - 0.03, lon + 0.03)
        anomaly = _nearest(anomaly_rows, lat, lon)
    except OceanColorError:
        anomaly = None

    if not any(row["value"] is not None for row in chlorophyll):
        raise OceanColorError(
            "the satellite returned no chlorophyll history at this position")

    chlorophyll_series = _daily(chlorophyll)
    temperature_series = _daily(temperature)
    chlorophyll_trend = _trend(chlorophyll_series)
    temperature_trend = _trend(temperature_series, absolute_threshold=0.3)

    return {
        "source": SOURCE,
        "note": ("Satellite history from NOAA CoastWatch, not INCOIS. Chlorophyll "
                 "is how much food the water carries, which is what a shoal follows. "
                 "A fall in chlorophyll or a warm SST anomaly is a plausible reason "
                 "for a poor season; it is not proof, and it says nothing about "
                 "fishing effort or where boats have been working."),
        "datasets": [CHLOROPHYLL["label"], SST["label"], SST_ANOMALY["label"]],
        "position": {"latitude": lat, "longitude": lon},
        "windowDays": days,
        "chlorophyll": {
            "unit": "mg/m3",
            "trend": chlorophyll_trend,
            "series": chlorophyll_series[-30:],
        },
        "seaSurfaceTemperature": {
            "unit": "degC",
            "trend": temperature_trend,
            "series": temperature_series[-30:],
        },
        "seaSurfaceTemperatureAnomalyC": (
            round(anomaly["value"], 2) if anomaly else None),
        "anomalyObservedAt": anomaly["time"][:10] if anomaly else None,
    }
