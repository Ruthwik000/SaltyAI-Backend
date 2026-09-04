"""Phase 9 geofencing, deliberately independent of ERDDAP.

GeoJSON coordinates use ``(longitude, latitude)`` order. Distances returned by
this module are in the source coordinate units (normally decimal degrees),
because Shapely performs planar geometry operations. Use a projected CRS for
accurate metre distances in production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

try:
    import geojson
    from shapely.geometry import Point, shape
    from shapely.geometry.base import BaseGeometry
except ImportError as exc:  # pragma: no cover - exercised in dependency checks
    raise ImportError("Phase 9 requires 'shapely' and 'geojson'; install with: pip install -r requirements.txt") from exc


class GeofenceError(ValueError):
    """Raised for malformed GeoJSON or invalid geofence inputs."""


def _geometry(zone: Any) -> BaseGeometry:
    if isinstance(zone, BaseGeometry):
        geometry = zone
    else:
        try:
            geometry = shape(zone.get("geometry", zone) if isinstance(zone, dict) else zone)
        except (AttributeError, TypeError, ValueError) as exc:
            raise GeofenceError("zone must be a Shapely geometry or GeoJSON geometry/feature") from exc
    if geometry.is_empty or not geometry.is_valid:
        raise GeofenceError("zone geometry must be non-empty and valid")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise GeofenceError("zone geometry must be a Polygon or MultiPolygon")
    return geometry


def _point(point: Any) -> Point:
    try:
        longitude, latitude = point
        longitude, latitude = float(longitude), float(latitude)
    except (TypeError, ValueError) as exc:
        raise GeofenceError("point must be a (longitude, latitude) pair") from exc
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise GeofenceError("point coordinates are outside valid longitude/latitude ranges")
    return Point(longitude, latitude)


def point_inside_zone(point: Any, zone: Any, include_boundary: bool = True) -> bool:
    """Return whether a lon/lat point is inside a restricted zone."""
    geometry = _geometry(zone)
    candidate = _point(point)
    return geometry.covers(candidate) if include_boundary else geometry.contains(candidate)


def distance_to_boundary(point: Any, zone: Any) -> float:
    """Return planar distance from a lon/lat point to the zone boundary."""
    return float(_point(point).distance(_geometry(zone).boundary))


def _zone_properties(zone: Any, index: int) -> dict[str, Any]:
    properties = zone.get("properties", {}) if isinstance(zone, dict) else {}
    properties = dict(properties or {})
    zone_id = properties.get("id", properties.get("zone_id", f"zone-{index + 1}"))
    return {"zone_id": zone_id, "name": properties.get("name", str(zone_id)), **properties}


def nearest_zone(point: Any, zones: Iterable[Any]) -> dict[str, Any] | None:
    """Return the nearest zone summary, or ``None`` for an empty collection."""
    candidate = _point(point)
    nearest = None
    for index, zone in enumerate(zones):
        geometry = _geometry(zone)
        distance = float(candidate.distance(geometry.boundary))
        if not geometry.covers(candidate):
            distance = float(candidate.distance(geometry))
        result = {
            **_zone_properties(zone, index),
            "inside": bool(geometry.covers(candidate)),
            "distance_to_boundary": distance,
        }
        if nearest is None or distance < nearest["distance_to_boundary"]:
            nearest = result
    return nearest


def load_geojson(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Load a GeoJSON object from a mapping, JSON string, or local file."""
    try:
        if isinstance(source, dict):
            document = source
        elif isinstance(source, Path) or (isinstance(source, str) and Path(source).exists()):
            document = json.loads(Path(source).read_text(encoding="utf-8"))
        else:
            document = json.loads(str(source))
        return geojson.GeoJSON.to_instance(document)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GeofenceError("source is not valid GeoJSON") from exc


def zones_from_geojson(source: str | Path | dict[str, Any]) -> list[dict[str, Any]]:
    """Extract Polygon features from a GeoJSON FeatureCollection or Feature."""
    document = load_geojson(source)
    if document.get("type") == "FeatureCollection":
        features = list(document.get("features", []))
    elif document.get("type") == "Feature":
        features = [document]
    else:
        features = [{"type": "Feature", "properties": {}, "geometry": document}]
    for feature in features:
        _geometry(feature)
    return features

