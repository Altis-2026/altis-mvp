#!/usr/bin/env python3
"""
05_gauge_depth.py — River-gauge depth for every property in a pre-baked event.

Writes outputs/{event}_gauge_depth.csv with, per property:
  gauge_wse_ft        peak water-surface elevation (ft NAVD88) interpolated
                      along the nearest gauged main stem
  gauge_ground_ft     USGS 3DEP 1 m ground elevation (ft NAVD88)
  gauge_depth_ft      max(0, WSE - ground); blank when not applicable
  gauge_dist_m        distance to that river's channel
  gauge_river         river name, e.g. "Brazos River"

Validated in validation/gauge_depth_check.py against USGS high water marks.
Only applied within backend.gauges.RIVERINE_MAX_DIST_M of a gauged channel:
it models main-stem river flooding, not tributaries or rain ponding.

US only (NOAA/USGS gauges). Usage:
    python pipeline/05_gauge_depth.py --event brazos
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.gauges import gauge_depth_for_points, sample_ground_ft  # noqa: E402
from pipeline.config import EVENTS  # noqa: E402

OUT = ROOT / "outputs"
WINDOWS = {"brazos": ("2017-08-25", "2017-09-15"), "harvey": ("2017-08-25", "2017-09-15")}


def sample_3dep_ft(lat, lon, bbox, chunk=2000):
    from backend.live_pipeline import init_ee
    if not init_ee():
        raise SystemExit("Earth Engine not configured.")
    import ee
    dem = ee.ImageCollection("USGS/3DEP/1m").filterBounds(ee.Geometry.Rectangle(bbox)).mosaic().select(0)
    g = np.full(len(lat), np.nan)
    for s in range(0, len(lat), chunk):
        idx = range(s, min(s + chunk, len(lat)))
        fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([float(lon[i]), float(lat[i])]), {"i": i})
                                   for i in idx])
        res = dem.reduceRegions(fc, ee.Reducer.mean(), 1).getInfo()
        for f in res["features"]:
            v = f["properties"].get("mean")
            if v is not None:
                g[f["properties"]["i"]] = v * 3.28084
        print(f"  3DEP sampled {min(s + chunk, len(lat))}/{len(lat)}", flush=True)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, choices=sorted(WINDOWS))
    a = ap.parse_args()
    cfg = EVENTS[a.event]
    bbox = cfg["bbox"]
    props = pd.read_csv(OUT / f"{a.event}_properties.csv")[["property_id", "latitude", "longitude"]].dropna()
    lat, lon = props["latitude"].to_numpy(), props["longitude"].to_numpy()

    pre = gauge_depth_for_points(lat, lon, bbox, *WINDOWS[a.event])
    near = np.isfinite(pre["wse_ft"])
    print(f"  {near.sum()} of {len(lat)} properties within reach of a gauged main stem")
    ground = np.full(len(lat), np.nan)
    if near.any():
        ground[near], _ = sample_ground_ft(lat[near], lon[near], bbox)
    depth = np.where(near & np.isfinite(ground), np.maximum(0.0, pre["wse_ft"] - ground), np.nan)

    out = pd.DataFrame({
        "property_id": props["property_id"],
        "gauge_wse_ft": np.round(pre["wse_ft"], 2),
        "gauge_ground_ft": np.round(ground, 2),
        "gauge_depth_ft": np.round(depth, 2),
        "gauge_dist_m": np.round(np.where(np.isfinite(pre["dist_to_channel_m"]), pre["dist_to_channel_m"], np.nan), 0),
        "gauge_river": pre["river"],
    })
    out.to_csv(OUT / f"{a.event}_gauge_depth.csv", index=False)
    meta = {"event": a.event, "window": WINDOWS[a.event], "rivers_used": pre["rivers_used"],
            "n_properties": int(len(out)), "n_in_reach": int(near.sum()),
            "n_depth_gt_0": int(np.nansum(depth > 0)),
            "method": "gauge crest WSE along NLDI main stem minus 3DEP 1 m ground",
            "validation": f"outputs/gauge_depth_check_{a.event}.json"}
    (OUT / f"{a.event}_gauge_depth_meta.json").write_text(json.dumps(meta, indent=1, default=str))
    print(json.dumps({k: v for k, v in meta.items() if k != "rivers_used"}, indent=1))
    print("rivers:", list(pre["rivers_used"]))


if __name__ == "__main__":
    main()
