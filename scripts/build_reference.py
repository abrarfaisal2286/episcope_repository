"""One-off script: build data/reference/{districts,facilities}.csv and simplified GeoJSON.

Usage: python scripts/build_reference.py path/to/district.json
Source of district.json: OCHA COD-AB Bangladesh ADM2 as redistributed at
https://github.com/mostakimjihad/bd_district_geojson (check licence before reuse).
"""
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from shapely.geometry import shape, mapping
from generator.reference import build_districts, build_facilities
from common.config import REFERENCE, ROOT

src = sys.argv[1]
REFERENCE.mkdir(parents=True, exist_ok=True)
d = build_districts(src, REFERENCE / "districts.csv")
build_facilities(d, REFERENCE / "facilities.csv")

gj = json.load(open(src))
feats = []
for f in gj["features"]:
    g = shape(f["geometry"]).simplify(0.01, preserve_topology=True)
    p = f["properties"]
    feats.append({"type": "Feature",
                  "properties": {"district_id": p["ADM2_PCODE"], "district": p["ADM2_EN"],
                                 "division": p["ADM1_EN"]},
                  "geometry": json.loads(json.dumps(mapping(g), default=list))})
def rnd(o):
    if isinstance(o, float): return round(o, 3)
    if isinstance(o, (list, tuple)): return [rnd(x) for x in o]
    return o
for f in feats:
    f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
out = ROOT / "dashboard" / "data"
out.mkdir(parents=True, exist_ok=True)
json.dump({"type": "FeatureCollection", "features": feats}, open(out / "districts.geojson", "w"),
          separators=(",", ":"))
print("districts:", len(d), "facilities:", len(build_facilities(d, REFERENCE / "facilities.csv")))
