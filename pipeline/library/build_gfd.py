#!/usr/bin/env python3
"""
Pull the Global Flood Database (Tellman et al., Nature 2021; 913 MODIS-mapped
floods 2000-2018, free in Earth Engine as GLOBAL_FLOOD_DB/MODIS_EVENTS/V1)
into outputs/library/gfd_events.json: dates, cause, severity, deaths,
displacement (Dartmouth Flood Observatory attributes), footprint bounds and
satellite-mapped flooded area (excluding permanent water).
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "outputs" / "library"


def main():
    from backend.live_pipeline import init_ee
    if not init_ee():
        raise SystemExit("Earth Engine not configured.")
    import ee
    col = ee.ImageCollection("GLOBAL_FLOOD_DB/MODIS_EVENTS/V1")
    n = col.size().getInfo()
    lst = col.toList(n)
    keep = ["id", "cc", "countries", "dfo_country", "dfo_centroid_x", "dfo_centroid_y", "dfo_dead",
            "dfo_displaced", "dfo_main_cause", "dfo_severity", "glide_index"]

    def describe(img):
        img = ee.Image(img)
        flooded = img.select("flooded").And(img.select("jrc_perm_water").Not())
        area = flooded.multiply(ee.Image.pixelArea()).reduceRegion(
            ee.Reducer.sum(), img.geometry(), scale=1000, maxPixels=1e10, bestEffort=True)
        return ee.Feature(None, img.toDictionary(keep)).set({
            "start": img.date().format("YYYY-MM-dd"),
            "end": ee.Date(img.get("system:time_end")).format("YYYY-MM-dd"),
            "bounds": img.geometry().bounds(1000).coordinates(),
            "flooded_km2": ee.Number(area.get("flooded")).divide(1e6),
        })

    OUT.mkdir(parents=True, exist_ok=True)
    rows, step = [], 8
    for s in range(0, n, step):
        for attempt in range(8):
            try:
                fc = ee.FeatureCollection(lst.slice(s, min(s + step, n)).map(describe))
                rows += [f["properties"] for f in fc.getInfo()["features"]]
                break
            except Exception as e:  # noqa: BLE001
                print(f"  batch {s} attempt {attempt}: {e}", flush=True)
                time.sleep(10 * (attempt + 1))
        else:
            raise SystemExit(f"batch {s} failed after retries; not writing a partial library")
        print(f"  {len(rows)}/{n}", flush=True)
    (OUT / "gfd_events.json").write_text(json.dumps(rows))
    print("wrote", OUT / "gfd_events.json", len(rows))


if __name__ == "__main__":
    main()
