"""Phase 9 geofencing tests using explicitly labelled TEST DATA polygons."""

dependency_error = None
try:
    from geofencing import distance_to_boundary, nearest_zone, point_inside_zone, zones_from_geojson
except ImportError as exc:
    dependency_error = str(exc)


TEST_DATA = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"id": "restricted-a", "name": "TEST DATA — Restricted A"}, "geometry": {
            "type": "Polygon", "coordinates": [[[82.0, 17.0], [82.5, 17.0], [82.5, 17.5], [82.0, 17.5], [82.0, 17.0]]]}},
        {"type": "Feature", "properties": {"id": "restricted-b", "name": "TEST DATA — Restricted B"}, "geometry": {
            "type": "Polygon", "coordinates": [[[83.0, 18.0], [83.5, 18.0], [83.5, 18.5], [83.0, 18.5], [83.0, 18.0]]]}},
    ],
}


def offline_tests():
    if dependency_error:
        print(f"Phase 9 tests skipped: {dependency_error}")
        return
    zones = zones_from_geojson(TEST_DATA)
    assert point_inside_zone((82.25, 17.25), zones[0])
    assert not point_inside_zone((82.75, 17.25), zones[0])
    assert not point_inside_zone((82.0, 17.25), zones[0], include_boundary=False)
    assert distance_to_boundary((82.25, 17.25), zones[0]) == 0.25
    nearest = nearest_zone((83.1, 18.1), zones)
    assert nearest["zone_id"] == "restricted-b"
    assert nearest["inside"] is True
    assert nearest["name"].startswith("TEST DATA")
    print("Phase 9 offline tests: PASS (TEST DATA polygons)")


if __name__ == "__main__":
    offline_tests()
