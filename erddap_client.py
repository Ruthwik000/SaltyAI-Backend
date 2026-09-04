"""Small client for the INCOIS ERDDAP metadata API."""

from __future__ import annotations

import json
import random
import ssl
from datetime import datetime
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ERDDAPError(RuntimeError):
    """Base exception raised by :class:`ERDDAPClient`."""


class ERDDAPConnectionError(ERDDAPError):
    """Raised when ERDDAP cannot be reached or returns invalid JSON."""


class DatasetNotFoundError(ERDDAPError):
    """Raised when a requested dataset does not exist."""


class MetadataValidationError(ERDDAPError):
    """Raised when a query does not match the dataset metadata."""


@dataclass
class ERDDAPClient:
    """Client for ERDDAP's official JSON metadata endpoints."""

    base_url: str = "https://erddap.incois.gov.in/erddap"
    timeout: float = 30.0
    verify_ssl: bool = True
    synthetic_fallback: bool = True

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    def _get_json(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "SALTY/1.0"})
        try:
            ssl_context = ssl.create_default_context() if self.verify_ssl else ssl._create_unverified_context()
            with urlopen(request, timeout=self.timeout, context=ssl_context) as response:
                payload = response.read()
        except HTTPError as exc:
            if exc.code == 404:
                raise DatasetNotFoundError(f"ERDDAP resource was not found: {url}") from exc
            raise ERDDAPConnectionError(f"ERDDAP returned HTTP {exc.code} for {url}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ERDDAPConnectionError(f"Could not connect to ERDDAP at {url}: {exc}") from exc

        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ERDDAPConnectionError(f"ERDDAP returned invalid JSON for {url}") from exc
        if not isinstance(data, dict):
            raise ERDDAPConnectionError(f"ERDDAP returned an unexpected response for {url}")
        return data

    def _query(self, dataset_id: str, expression: str) -> dict[str, Any]:
        """Run a griddap query and normalize its JSON table response."""
        # Encode brackets/parentheses: Tomcat rejects them when left raw in
        # the request target, while ERDDAP correctly decodes the query.
        encoded_expression = quote(expression, safe=",")
        return self._get_json(f"griddap/{quote(dataset_id, safe='')}.json?{encoded_expression}")

    @staticmethod
    def _synthetic_value(variable: str) -> float:
        """Generate prototype-only values in broad, meaningful marine ranges."""
        name = variable.lower().replace("_", " ")
        if any(term in name for term in ("temperature", "sst", "sea temp")):
            return round(random.uniform(24.0, 31.0), 2)       # degrees C
        if any(term in name for term in ("chlor", "chl")):
            return round(random.uniform(0.05, 5.0), 3)       # mg/m3
        if any(term in name for term in ("wind", "gust")):
            return round(random.uniform(0.0, 25.0), 2)       # m/s
        if any(term in name for term in ("wave", "swell")):
            return round(random.uniform(0.1, 5.0), 2)       # metres
        if any(term in name for term in ("rain", "precip")):
            return round(random.uniform(0.0, 80.0), 2)       # mm
        if any(term in name for term in ("lightning", "thunder")):
            return round(random.uniform(0.0, 20.0), 2)      # events
        if any(term in name for term in ("current", "velocity")):
            return round(random.uniform(0.05, 1.5), 3)      # m/s
        return round(random.uniform(0.0, 100.0), 2)

    def _synthetic_query(self, dataset_id: str, variable: str, start: str, end: str | None = None, bbox: tuple[float, float, float, float] | None = None, point: tuple[float, float] | None = None, variables: list[str] | None = None) -> dict[str, Any]:
        """Return clearly labelled synthetic rows when the prototype is offline."""
        names = variables or [variable]
        if point:
            locations = [point]
        else:
            values = bbox or (0.0, 0.0, 0.0, 0.0)
            locations = [((values[0] + values[1]) / 2, (values[2] + values[3]) / 2)]
        times = [start] if end is None else [start, end]
        columns = ["time", "latitude", "longitude", *names]
        rows = []
        for index, timestamp in enumerate(times):
            latitude, longitude = locations[min(index, len(locations) - 1)]
            rows.append([timestamp, latitude, longitude, *[self._synthetic_value(name) for name in names]])
        return {"synthetic": True, "warning": "SYNTHETIC TEST DATA — ERDDAP fetch failed", "table": {"columnNames": columns, "rows": rows}}

    def _get_bytes(self, path: str) -> bytes:
        """Fetch a binary ERDDAP response, such as NetCDF."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        request = Request(url, headers={"User-Agent": "SALTY/1.0"})
        try:
            ssl_context = ssl.create_default_context() if self.verify_ssl else ssl._create_unverified_context()
            with urlopen(request, timeout=self.timeout, context=ssl_context) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                raise DatasetNotFoundError(f"ERDDAP resource was not found: {url}") from exc
            raise ERDDAPConnectionError(f"ERDDAP returned HTTP {exc.code} for {url}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ERDDAPConnectionError(f"Could not connect to ERDDAP at {url}: {exc}") from exc

    @staticmethod
    def _table(data: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
        table = data.get("table")
        if not isinstance(table, dict):
            raise ERDDAPError("ERDDAP response did not contain a metadata table")
        columns = table.get("columnNames", [])
        rows = table.get("rows", [])
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise ERDDAPError("ERDDAP metadata table has an invalid shape")
        return columns, rows

    def check_connection(self) -> dict[str, Any]:
        """Check the server and return a structured status dictionary."""
        data = self._get_json("info/index.json")
        columns, rows = self._table(data)
        return {"connected": True, "base_url": self.base_url, "dataset_count": len(rows), "columns": columns}

    def list_datasets(self) -> list[dict[str, Any]]:
        """Return all datasets, including their IDs and titles."""
        columns, rows = self._table(self._get_json("info/index.json"))
        records = [dict(zip(columns, row)) for row in rows]
        datasets = []
        for record in records:
            dataset_id = record.get("Dataset ID") or record.get("datasetID")
            if dataset_id:
                datasets.append({
                    "dataset_id": dataset_id,
                    "title": record.get("Title") or record.get("title") or "",
                    "griddap": record.get("griddap"),
                    "tabledap": record.get("tabledap"),
                    "raw": record,
                })
        return datasets

    def _metadata_rows(self, dataset_id: str) -> list[dict[str, Any]]:
        if not dataset_id or "/" in dataset_id or "\\" in dataset_id:
            raise DatasetNotFoundError(f"Invalid dataset ID: {dataset_id!r}")
        columns, rows = self._table(self._get_json(f"info/{quote(dataset_id, safe='')}/index.json"))
        return [dict(zip(columns, row)) for row in rows]

    def get_variables(self, dataset_id: str) -> list[dict[str, Any]]:
        """Return variables and their dimensions, units, and attributes."""
        return self._variables_from_rows(self._metadata_rows(dataset_id))

    @staticmethod
    def _variables_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        variables: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = row.get("Variable Name") or row.get("variableName")
            row_type = str(row.get("Row Type") or row.get("rowType") or "").lower()
            if not name or name in {"NC_GLOBAL", "global"} or row_type == "global":
                continue
            # ERDDAP's info table represents dimensions as rows whose Variable
            # Name is the dimension name; variable rows and variable attributes
            # use the same column for the variable name.
            if row_type not in {"dimension", "variable", "attribute"}:
                continue
            variable = variables.setdefault(name, {"name": name, "dimensions": [], "units": None, "attributes": {}})
            dimension = row.get("Dimension") or row.get("dimension")
            if dimension and dimension not in variable["dimensions"]:
                variable["dimensions"].append(dimension)
            attribute = row.get("Attribute Name") or row.get("attributeName")
            value = row.get("Value")
            if attribute:
                variable["attributes"][attribute] = value
                if attribute.lower() == "units":
                    variable["units"] = value
        return list(variables.values())

    def get_dataset_metadata(self, dataset_id: str) -> dict[str, Any]:
        """Return normalized dataset metadata from ERDDAP's info endpoint."""
        rows = self._metadata_rows(dataset_id)
        variables = self._variables_from_rows(rows)
        attributes: dict[str, Any] = {}
        for row in rows:
            variable_name = row.get("Variable Name") or row.get("variableName")
            name = row.get("Attribute Name") or row.get("attributeName")
            row_type = str(row.get("Row Type") or row.get("rowType") or "").lower()
            if name and (variable_name in {None, "", "NC_GLOBAL", "global"} or row_type == "global"):
                attributes[name] = row.get("Value")
            # Some ERDDAP installations expose time coverage as a dataset
            # attribute row without marking it as global.
            if name and name.lower().startswith("time_coverage"):
                attributes[name] = row.get("Value")
        title = attributes.get("title") or attributes.get("Title") or ""
        dimensions = []
        for row in rows:
            if str(row.get("Row Type") or row.get("rowType") or "").lower() == "dimension":
                name = row.get("Variable Name") or row.get("variableName")
                if name and name not in dimensions:
                    dimensions.append(name)
        for variable in variables:
            for dimension in variable["dimensions"]:
                if dimension not in dimensions:
                    dimensions.append(dimension)
        return {
            "dataset_id": dataset_id,
            "title": title,
            "variables": variables,
            "dimensions": dimensions,
            "units": {v["name"]: v["units"] for v in variables if v["units"] is not None},
            "time_coverage": {key: value for key, value in attributes.items() if key.lower().startswith("time_coverage")},
            "attributes": attributes,
        }

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise MetadataValidationError(f"Invalid ISO time: {value!r}") from exc

    @staticmethod
    def _bbox_values(bbox: Any) -> tuple[float, float, float, float]:
        if isinstance(bbox, dict):
            try:
                values = (bbox["min_lat"], bbox["max_lat"], bbox["min_lon"], bbox["max_lon"])
            except KeyError as exc:
                raise MetadataValidationError("bbox must contain min_lat, max_lat, min_lon, and max_lon") from exc
        else:
            try:
                values = tuple(bbox)
            except TypeError as exc:
                raise MetadataValidationError("bbox must be a 4-item sequence or mapping") from exc
            if len(values) != 4:
                raise MetadataValidationError("bbox must be (min_lat, max_lat, min_lon, max_lon)")
        try:
            result = tuple(float(value) for value in values)
        except (TypeError, ValueError) as exc:
            raise MetadataValidationError("bbox coordinates must be numeric") from exc
        if result[0] > result[1] or result[2] > result[3]:
            raise MetadataValidationError("bbox minimum coordinates must not exceed maximum coordinates")
        return result  # type: ignore[return-value]

    @staticmethod
    def _dimension_name(dimensions: list[str], aliases: set[str]) -> str | None:
        for dimension in dimensions:
            if dimension.lower() in aliases:
                return dimension
        return None

    def _validated_context(self, dataset_id: str, variable_names: list[str], start: str, end: str | None = None) -> tuple[dict[str, Any], list[str], dict[str, str]]:
        metadata = self.get_dataset_metadata(dataset_id)
        available = {variable["name"]: variable for variable in metadata["variables"]}
        missing = [name for name in variable_names if name not in available]
        if missing:
            raise MetadataValidationError(f"Unknown variable(s) for {dataset_id}: {', '.join(missing)}")
        for name in variable_names:
            if available[name].get("units") is None:
                raise MetadataValidationError(f"Variable {name!r} has no units in dataset metadata")

        dimensions = metadata.get("dimensions", [])
        if not isinstance(dimensions, list):
            raise MetadataValidationError("Dataset metadata has no usable dimension order")
        time_dim = self._dimension_name(dimensions, {"time", "t"})
        lat_dim = self._dimension_name(dimensions, {"latitude", "lat", "y"})
        lon_dim = self._dimension_name(dimensions, {"longitude", "lon", "long", "x"})
        if not all((time_dim, lat_dim, lon_dim)):
            raise MetadataValidationError(f"Dataset {dataset_id!r} does not expose time/latitude/longitude dimensions")
        coverage = metadata.get("time_coverage", {})
        coverage_start = coverage.get("time_coverage_start")
        coverage_end = coverage.get("time_coverage_end")
        requested_start = self._parse_time(start)
        requested_end = self._parse_time(end or start)
        if not coverage_start or not coverage_end:
            raise MetadataValidationError(f"Dataset {dataset_id!r} has no complete time coverage metadata")
        if requested_start < self._parse_time(coverage_start) or requested_end > self._parse_time(coverage_end):
            raise MetadataValidationError(f"Requested time range {start}..{end or start} is outside dataset coverage")
        if requested_start > requested_end:
            raise MetadataValidationError("start must not be later than end")
        return metadata, dimensions, {"time": time_dim, "lat": lat_dim, "lon": lon_dim}

    @staticmethod
    def _constraint(dimension: str, start: Any, end: Any | None = None) -> str:
        if end is None:
            return f"[({quote(str(start), safe=':-+.TZ')})]"
        return f"[({quote(str(start), safe=':-+.TZ')}):({quote(str(end), safe=':-+.TZ')})]"

    def _build_expression(self, variable: str, dimensions: list[str], names: dict[str, str], start: str, end: str | None, bbox: tuple[float, float, float, float], point: tuple[float, float] | None = None) -> str:
        lat_min, lat_max, lon_min, lon_max = bbox
        constraints = {names["time"]: self._constraint(names["time"], start, end)}
        # Build ERDDAP's positional constraints in the exact metadata order.
        constraints[names["lat"]] = self._constraint(names["lat"], point[0] if point else lat_min, None if point else lat_max)
        constraints[names["lon"]] = self._constraint(names["lon"], point[1] if point else lon_min, None if point else lon_max)
        return quote(variable, safe="_") + "".join(constraints[dimension] for dimension in dimensions if dimension in constraints)

    def get_point_data(self, dataset_id: str, variable: str, time: str, lat: float, lon: float) -> dict[str, Any]:
        """Retrieve one griddap value at a time/latitude/longitude point."""
        try:
            _, dimensions, names = self._validated_context(dataset_id, [variable], time)
            expression = self._build_expression(variable, dimensions, names, time, None, (lat, lat, lon, lon), (lat, lon))
            return self._query(dataset_id, expression)
        except (ERDDAPConnectionError, DatasetNotFoundError):
            if not self.synthetic_fallback:
                raise
            return self._synthetic_query(dataset_id, variable, time, point=(lat, lon))

    def get_region_data(self, dataset_id: str, variable: str, bbox: Any, time_range: tuple[str, str]) -> dict[str, Any]:
        """Retrieve a spatial region for one time range."""
        start, end = time_range
        values = self._bbox_values(bbox)
        try:
            _, dimensions, names = self._validated_context(dataset_id, [variable], start, end)
            return self._query(dataset_id, self._build_expression(variable, dimensions, names, start, end, values))
        except (ERDDAPConnectionError, DatasetNotFoundError):
            if not self.synthetic_fallback:
                raise
            return self._synthetic_query(dataset_id, variable, start, end, bbox=values)

    def get_time_series(self, dataset_id: str, variable: str, bbox: Any, start: str, end: str) -> dict[str, Any]:
        """Retrieve a variable's time series over a spatial region."""
        values = self._bbox_values(bbox)
        try:
            _, dimensions, names = self._validated_context(dataset_id, [variable], start, end)
            return self._query(dataset_id, self._build_expression(variable, dimensions, names, start, end, values))
        except (ERDDAPConnectionError, DatasetNotFoundError):
            if not self.synthetic_fallback:
                raise
            return self._synthetic_query(dataset_id, variable, start, end, bbox=values)

    def get_forecast(self, dataset_id: str, variables: list[str], bbox: Any, start: str, end: str) -> dict[str, Any]:
        """Retrieve several variables over the same spatial/time window."""
        if not variables:
            raise MetadataValidationError("variables must contain at least one variable name")
        values = self._bbox_values(bbox)
        try:
            _, dimensions, names = self._validated_context(dataset_id, variables, start, end)
            expressions = [self._build_expression(variable, dimensions, names, start, end, values) for variable in variables]
            return self._query(dataset_id, ",".join(expressions))
        except (ERDDAPConnectionError, DatasetNotFoundError):
            if not self.synthetic_fallback:
                raise
            return self._synthetic_query(dataset_id, variables[0], start, end, bbox=values, variables=variables)

    def get_netcdf(self, dataset_id: str, variable: str, bbox: Any, start: str, end: str) -> bytes:
        """Retrieve a validated griddap selection in ERDDAP NetCDF format."""
        values = self._bbox_values(bbox)
        _, dimensions, names = self._validated_context(dataset_id, [variable], start, end)
        expression = self._build_expression(variable, dimensions, names, start, end, values)
        encoded_expression = quote(expression, safe=",")
        return self._get_bytes(f"griddap/{quote(dataset_id, safe='')}.nc?{encoded_expression}")
