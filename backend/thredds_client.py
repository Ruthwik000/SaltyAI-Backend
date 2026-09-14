"""INCOIS Ocean State Forecast client (THREDDS NetCDF Subset Service).

Why NCSS and not WMS
--------------------
The frontend map draws OSF layers over WMS, so WMS was the obvious first
choice for reading values too. It does not work on this server: GetFeatureInfo
returns only the clicked coordinate and never a value —

    Clicked:
        Longitude: 83.2185
        Latitude:  17.6868

— in both text/plain and XML, with and without a TIME parameter, in WMS 1.1.1
and 1.3.0. An earlier version of this file had a "if nothing is labelled, take
the last number in the body" fallback, which duly read 17.6868 as a wave height
and had the risk model report 100/100 danger. There is no such fallback now.

The NetCDF Subset Service on the same server does return values, several
variables in one request, as CSV:

    time,station,latitude,longitude,HS,PHS01,T02,UWND,VWND
    2026-09-08T12:00:00Z,GridPointRequestedAt[17.500N_83.600E],17.500,83.600,
    1.3387818336486816,0.6708751320838928,4.849918365478516,4.194007873535156,
    4.662006855010986

Verified against https://www.incois.gov.in/thredds on 2026-09-08. The service
base path is /thredds/ncss/grid/, taken from the server's own catalogue
(/thredds/catalog/osf/ww3/catalog.xml), not guessed.

Live filenames are discovered from the official OSF page each run, mirroring
the frontend's /api/incois/osf-config route, because INCOIS renames the file
per forecast cycle and the handoff rules forbid hardcoding one.
"""

from __future__ import annotations

import csv
import io
import math
import re
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OSF_PAGE = "https://www.incois.gov.in/oceanservices/osfforecast.jsp"
NCSS_BASE = "https://www.incois.gov.in/thredds/ncss/grid/osf"
WMS_BASE = "https://www.incois.gov.in/thredds/wms/osf"


class ThreddsError(RuntimeError):
    """Raised when the INCOIS forecast cannot be read."""


# The OSF files, the page variable that names each one, and the variables each
# holds. Variable names come from the datasets' own OPeNDAP .dds listings.
# The files declare no units, so the units below are supplied from the OSF
# documentation and are stated wherever a value is shown.
DATASETS: dict[str, dict[str, Any]] = {
    "ww3": {
        "directory": "ww3",
        "page_var": "rsmc_combined_ww3",
        "variables": ["HS", "PHS01", "T02", "UWND", "VWND"],
    },
    "currents": {
        "directory": "currents",
        "page_var": "currentsFile2",
        "variables": ["CURRENT"],
    },
    "sst": {
        "directory": "winds",
        "page_var": "sstnio",
        "variables": ["SST"],
    },
}

# Reported field -> (source dataset, CSV column, unit, plain label)
FIELDS: dict[str, tuple[str, str, str, str]] = {
    "waveHeight": ("ww3", "HS", "m", "significant wave height"),
    "swellHeight": ("ww3", "PHS01", "m", "swell height"),
    "wavePeriod": ("ww3", "T02", "s", "wave period"),
    "currentSpeed": ("currents", "CURRENT", "m/s", "surface current"),
    "seaSurfaceTemperature": ("sst", "SST", "degC", "sea surface temperature"),
}

_CACHE_TTL = 1800.0
_files_cache: dict[str, str] = {}
_files_cache_at = 0.0
_CTX = ssl._create_unverified_context()


def _fetch(url: str, timeout: float = 30.0) -> str:
    request = Request(url, headers={"User-Agent": "SALTY/1.0", "Accept": "*/*"})
    try:
        with urlopen(request, timeout=timeout, context=_CTX) as response:
            return response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise ThreddsError(f"INCOIS returned HTTP {exc.code} for {url}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ThreddsError(f"Could not reach INCOIS at {url}: {exc}") from exc


def discover_files(force: bool = False) -> dict[str, str]:
    """Today's OSF filenames, read from the official page's inline JS."""
    global _files_cache, _files_cache_at
    if _files_cache and not force and time.time() - _files_cache_at < _CACHE_TTL:
        return _files_cache
    html = _fetch(OSF_PAGE)
    files = {}
    for name in ("sstnio", "currentsFile2", "rsmc_combined_ww3", "mldnio"):
        match = re.search(r"var\s+" + name + r"\s*=\s*[\"']([^\"']+)", html, re.IGNORECASE)
        if match:
            files[name] = match.group(1)
    if not files:
        raise ThreddsError("The OSF page did not expose the expected dataset variables")
    _files_cache, _files_cache_at = files, time.time()
    return files


def dataset_path(key: str, files: dict[str, str]) -> str:
    definition = DATASETS[key]
    discovered = files.get(definition["page_var"])
    if not discovered:
        # No guessing at a filename. INCOIS renames the file each cycle.
        raise ThreddsError(f"The OSF page did not name the {key} dataset")
    return f"{definition['directory']}/{discovered}"


def ncss_url(key: str, files: dict[str, str], lat: float, lon: float,
             time_start: str | None = None, time_end: str | None = None) -> str:
    definition = DATASETS[key]
    params: dict[str, str] = {
        "var": ",".join(definition["variables"]),
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "accept": "csv",
    }
    if time_start and time_end:
        params["time_start"], params["time_end"] = time_start, time_end
    elif time_start:
        params["time"] = time_start
    # No time parameter at all means "the current step". Verified against the
    # server: omitting it returns the step matching ncWMS nearestTimeIso, while
    # the documented time=present is rejected by this THREDDS build with a bare
    # HTTP 400 and an HTML error page.
    return f"{NCSS_BASE}/{dataset_path(key, files)}?{urlencode(params)}"


def _clean(name: str) -> str:
    """`HS[unit="m"]` -> `HS`."""
    return re.sub(r"\[.*?\]", "", name).strip()


def parse_ncss_csv(body: str) -> list[dict[str, Any]]:
    """CSV rows -> dicts. NaN becomes None; it means land or outside the grid."""
    reader = csv.reader(io.StringIO(body.strip()))
    try:
        header = [_clean(column) for column in next(reader)]
    except StopIteration:
        return []
    rows = []
    for raw in reader:
        if not raw:
            continue
        row: dict[str, Any] = {}
        for column, value in zip(header, raw):
            text = value.strip()
            if column in {"time", "station"}:
                row[column] = text
                continue
            try:
                number = float(text)
            except ValueError:
                row[column] = None
                continue
            row[column] = None if math.isnan(number) else number
        rows.append(row)
    return rows


def _wind(row: dict[str, Any]) -> dict[str, Any] | None:
    """Wind speed and meteorological direction from the U/V components."""
    u, v = row.get("UWND"), row.get("VWND")
    if u is None or v is None:
        return None
    return {
        "value": round(math.hypot(u, v), 2),
        "unit": "m/s",
        "label": "wind speed",
        "directionDeg": round((270.0 - math.degrees(math.atan2(v, u))) % 360.0, 1),
    }


# Steps outward when the requested point lands on a land cell. The grid is
# ~0.1 degrees, so these reach roughly 11, 22 and 44 km offshore.
_OFFSETS = [(0.0, 0.0)] + [
    (dy * step, dx * step)
    for step in (0.1, 0.2, 0.4)
    for dy, dx in ((0, 1), (-1, 1), (-1, 0), (0, -1), (1, 0), (1, 1), (-1, -1), (1, -1))
]


def point_conditions(lat: float, lon: float) -> dict[str, Any]:
    """Live conditions at a position, from the INCOIS Ocean State Forecast.

    A coastal request often lands on a land cell, where the model has no value
    and NCSS returns NaN. Rather than report nothing, this steps seaward in
    fixed increments and reports the coordinate that actually produced the
    reading, so the answer says where it came from.
    """
    files = discover_files()
    parameters: dict[str, Any] = {}
    unavailable: list[str] = []
    used_lat, used_lon, timestamp = lat, lon, None

    for key in DATASETS:
        row: dict[str, Any] | None = None
        for delta_lat, delta_lon in _OFFSETS:
            rows = parse_ncss_csv(_fetch(ncss_url(key, files, lat + delta_lat, lon + delta_lon)))
            if not rows:
                continue
            candidate = rows[0]
            wanted = DATASETS[key]["variables"]
            if any(candidate.get(name) is not None for name in wanted):
                row = candidate
                if key == "ww3":
                    used_lat = candidate.get("latitude", lat + delta_lat)
                    used_lon = candidate.get("longitude", lon + delta_lon)
                break
        if row is None:
            unavailable.extend(
                field for field, (source, *_rest) in FIELDS.items() if source == key
            )
            if key == "ww3":
                unavailable.append("windSpeed")
            continue

        timestamp = timestamp or row.get("time")
        for field, (source, column, unit, label) in FIELDS.items():
            if source != key:
                continue
            value = row.get(column)
            if value is None:
                unavailable.append(field)
                continue
            parameters[field] = {
                "value": round(value, 3),
                "unit": unit,
                "label": label,
                "timestamp": row.get("time"),
                "dataset": dataset_path(key, files),
            }
        if key == "ww3":
            wind = _wind(row)
            if wind:
                wind["timestamp"] = row.get("time")
                wind["dataset"] = dataset_path(key, files)
                parameters["windSpeed"] = wind
            else:
                unavailable.append("windSpeed")

    return {
        "source": "INCOIS Ocean State Forecast (THREDDS NetCDF Subset Service)",
        "requested": {"latitude": lat, "longitude": lon},
        "readAt": {"latitude": used_lat, "longitude": used_lon},
        "timestamp": timestamp,
        "parameters": parameters,
        "unavailable_parameters": sorted(set(unavailable)),
    }


def series_conditions(lat: float, lon: float, start_iso: str, end_iso: str) -> dict[str, Any]:
    """Forecast steps between two times — what "tomorrow morning" needs.

    NCSS returns 3-hourly steps for a time_start/time_end range. Only the wave
    file is queried: it carries waves, swell, period and the wind components,
    which is everything a go/no-go decision turns on.
    """
    files = discover_files()
    rows: list[dict[str, Any]] = []
    used_lat, used_lon = lat, lon
    for delta_lat, delta_lon in _OFFSETS:
        parsed = parse_ncss_csv(
            _fetch(ncss_url("ww3", files, lat + delta_lat, lon + delta_lon, start_iso, end_iso))
        )
        usable = [row for row in parsed if row.get("HS") is not None]
        if usable:
            rows = usable
            used_lat = usable[0].get("latitude", lat + delta_lat)
            used_lon = usable[0].get("longitude", lon + delta_lon)
            break

    steps = []
    for row in rows:
        step = {
            "time": row.get("time"),
            "waveHeight": None if row.get("HS") is None else round(row["HS"], 2),
            "swellHeight": None if row.get("PHS01") is None else round(row["PHS01"], 2),
            "wavePeriod": None if row.get("T02") is None else round(row["T02"], 2),
        }
        wind = _wind(row)
        step["windSpeed"] = wind["value"] if wind else None
        step["windDirectionDeg"] = wind["directionDeg"] if wind else None
        steps.append(step)

    return {
        "source": "INCOIS Ocean State Forecast (THREDDS NetCDF Subset Service)",
        "units": {"waveHeight": "m", "swellHeight": "m", "wavePeriod": "s", "windSpeed": "m/s"},
        "requested": {"latitude": lat, "longitude": lon},
        "readAt": {"latitude": used_lat, "longitude": used_lon},
        "window": {"start": start_iso, "end": end_iso},
        "dataset": dataset_path("ww3", files) if rows else None,
        "steps": steps,
    }