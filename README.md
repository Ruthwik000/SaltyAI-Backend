# SALTY data layer — Phases 1–3

`erddap_client.py` is a dependency-free Python client for the INCOIS ERDDAP metadata API.

Run the live smoke test from this directory:

```bash
python3 test_erddap.py
```

The client uses ERDDAP's official `/info/index.json` and `/info/{dataset_id}/index.json` endpoints and returns dictionaries/lists suitable for later SALTY layers.

Phase 2 retrieval methods use griddap JSON responses. Bounding boxes are `(min_lat, max_lat, min_lon, max_lon)` (or a mapping with those four keys). Every query re-reads metadata and validates variables, units, dimension order, and time coverage before requesting data.

Run Phase 3 discovery and sampling for Visakhapatnam with:

```bash
python3 test_phase3.py
```

Phase 3 discovers parameter candidates from dataset metadata and reports unavailable parameters without substituting a related variable.

Phase 5 uses `risk_features.py` to discover marine-risk features and construct a chronological 72-hour feature dataset. Each row contains direct feature values plus `missing_values`, `units`, and `source_dataset` provenance. Run its offline tests with `python3 test_phase5.py`; append `--live` for the INCOIS catalog check.

Phase 6 uses `fishing_zone.py` for metadata-driven coastal environmental data. It supports SST, chlorophyll, currents, ocean colour, and PFZ-related discovery, with point, region, and time-series samples. PFZ coordinates are never generated. Run `python3 test_phase6.py` or append `--live` for INCOIS retrieval.

Phase 7 uses `historical.py` for reproducible researcher workflows: date ranges, bounding boxes, points, time series, same- or cross-dataset parameter comparisons, and CSV, JSON, and NetCDF exports. Run `python3 test_phase7.py` or append `--live` to exercise historical SST and chlorophyll retrieval against INCOIS.

The Marine Map is an official INCOIS OSF integration in the Next.js frontend. It is not backed by a Python map-data route. The Python backend now exposes prediction models through `/api/predictions`.

Phase 9 uses `geofencing.py`, independently of ERDDAP, for GeoJSON/Shapely restricted-zone checks: `point_inside_zone`, `distance_to_boundary`, and `nearest_zone`. GeoJSON points use `(longitude, latitude)` order. Sample polygons in `test_phase9.py` are explicitly `TEST DATA`; no random fallback is used for safety-critical geofence decisions. Install dependencies with `pip install -r requirements.txt`, then run `python3 test_phase9.py`.

Phase 10 uses `severe_weather.py` for metadata-driven cyclone, wind, wave, swell, rainfall, lightning, thunderstorm, and other severe-weather inputs. Missing parameters are returned as `NOT AVAILABLE`; warnings and disaster ML are not fabricated.

Phase 11 uses `ollama_agent.py` to expose the ERDDAP client as allowlisted Ollama tools: dataset search/metadata, point, region, time-series, and forecast queries. The agent is instructed to answer only from tool-returned normalized records. Start Ollama locally with a tool-capable model, then run `python3 test_phase10_11.py` for the deterministic end-to-end flow or adapt `OllamaAgent` for live `/api/chat` calls.

Prediction models use only retrieved ERDDAP forecast records. `prediction_models.py` provides marine-risk scoring and lower-risk fishing-window selection. Missing or unavailable source data produces `NOT AVAILABLE`; no synthetic values are used by `/api/predictions`.

All ERDDAP data-query methods have prototype fallbacks enabled by default (`synthetic_fallback=True`). Fetch failures return meaningful parameter-range values tagged `synthetic: true` and `SYNTHETIC TEST DATA`; set `synthetic_fallback=False` for strict production behavior. Catalog and metadata failures return `NOT AVAILABLE`.
