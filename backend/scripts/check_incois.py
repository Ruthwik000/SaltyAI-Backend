"""Verify the live INCOIS forecast read, end to end.

Run from backend/:  python -m scripts.check_incois [lat] [lon]

Prints the discovered filenames, the URL used, and every value returned.
Nothing is cached between runs, so this is the quickest way to tell whether
a "no data" answer in the chat is INCOIS being down or a bug in SALTY.
"""

import json
import sys

import thredds_client as t

lat = float(sys.argv[1]) if len(sys.argv) > 1 else 17.6868
lon = float(sys.argv[2]) if len(sys.argv) > 2 else 83.2185

print(f"Reading INCOIS Ocean State Forecast at {lat}, {lon}\n")

files = t.discover_files(force=True)
print("Live dataset files named by the OSF page:")
for name, value in files.items():
    print(f"   {name:22s} {value}")

print("\nRequest URLs:")
for key in t.DATASETS:
    print(f"   {key:9s} {t.ncss_url(key, files, lat, lon)}")

print()
result = t.point_conditions(lat, lon)
print(json.dumps(result, indent=2))

values = {k: v for k, v in result["parameters"].items() if v.get("value") is not None}
print(f"\n{len(values)} of {len(t.FIELDS) + 1} parameters returned a value.")
if result["readAt"] != result["requested"]:
    print(f"Requested point was a land cell; read from {result['readAt']} instead.")
if result["unavailable_parameters"]:
    print("Not available:", ", ".join(result["unavailable_parameters"]))
