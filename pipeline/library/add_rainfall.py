#!/usr/bin/env python3
"""
Storm rainfall total for every event in the flood library: NASA GPM IMERG V07
precipitation summed over the 7 days from event start, area-mean over a 50 km
radius around the event centre (claims centroid for FEMA events, the Dartmouth
centroid for Global Flood Database events). Free, global, from 1998.

Writes outputs/library/rainfall.json keyed by library id ("fema-4332",
"gfd-1586") -> mm. Resumable; protocol fixed in docs/PREREG_ANALOGS_V2.md.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LIB = ROOT / "outputs" / "library"
WINDOW_DAYS, RADIUS_M, SCALE_M = 7, 50000, 10000


def targets():
    out = {}
    for e in json.loads((LIB / "fema_events.json").read_text()):
        c = (e.get("nfip") or {}).get("claims_centroid")
        if c:
            out[f"fema-{e['disaster_number']}"] = (e["start"], c[0], c[1])
    for e in json.loads((LIB / "gfd_events.json").read_text()):
        x, y = e.get("dfo_centroid_x"), e.get("dfo_centroid_y")
        if x is not None and y is not None and e.get("start"):
            out[f"gfd-{e['id']}"] = (e["start"], x, y)
    return out


def main():
    from backend.live_pipeline import init_ee
    if not init_ee():
        raise SystemExit("Earth Engine not configured.")
    import ee
    path = LIB / "rainfall.json"
    done = json.loads(path.read_text()) if path.exists() else {}
    todo = [(k, v) for k, v in targets().items() if k not in done]
    print(f"{len(todo)} events to do ({len(done)} done)", flush=True)
    col = ee.ImageCollection("NASA/GPM_L3/IMERG_V07").select("precipitation")

    def one(f):
        start = ee.Date(f.get("start"))
        img = col.filterDate(start, start.advance(WINDOW_DAYS, "day")).sum().multiply(0.5)   # mm/h x 0.5 h
        v = img.reduceRegion(ee.Reducer.mean(), f.geometry().buffer(RADIUS_M), SCALE_M, maxPixels=1e9)
        return f.set("rain_mm", v.get("precipitation"))

    step = 25
    for s in range(0, len(todo), step):
        chunk = todo[s:s + step]
        fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([lon, lat]), {"key": k, "start": st})
                                   for k, (st, lon, lat) in chunk])
        for attempt in range(8):
            try:
                res = fc.map(one).getInfo()
                for f in res["features"]:
                    p = f["properties"]
                    done[p["key"]] = round(p["rain_mm"], 1) if p.get("rain_mm") is not None else None
                break
            except Exception as e:  # noqa: BLE001
                print(f"  batch {s} attempt {attempt}: {str(e)[:100]}", flush=True)
                time.sleep(10 * (attempt + 1))
        else:
            raise SystemExit(f"batch {s} failed; not writing partial results")
        path.write_text(json.dumps(done))
        print(f"  {len(done)} done", flush=True)


if __name__ == "__main__":
    main()
