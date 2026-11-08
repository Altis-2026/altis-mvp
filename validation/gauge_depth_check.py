#!/usr/bin/env python3
"""
gauge_depth_check.py — Does a river-gauge water surface give better depth than
radar at surveyed flood sites?

METHOD (no fitted parameters)
  1. Peak stage at every NWS gauge on the main river during the event, from
     the NOAA NWPS crest record (USGS NWIS instantaneous values as fallback).
  2. Stage + gauge datum (NAVD88, from NWPS or the USGS site record) = peak
     water-surface elevation (WSE) at each gauge.
  3. The real main-stem channel from the USGS NLDI river network. WSE is
     interpolated linearly in along-channel distance between gauges and held
     flat beyond the end gauges.
  4. Depth at a point = WSE - ground, ground from USGS 3DEP 1 m lidar.

GROUND TRUTH
  USGS STN high water marks for the event (surveyed height above ground),
  same mark selection as hwm_check.py (zero-height marks dropped as unfilled).

READ BEFORE QUOTING
  - Applies only to the riverine floodplain of a gauged main stem. Marks on
    tributaries or from local ponding are reported separately (by distance
    to the channel) because the method does not model them.
  - Same precision caveat as hwm_check.py: every mark flooded, so this
    measures recall and depth error, never false positives.
  - Small samples. Distinct survey sites are the unit; CIs are Wilson 95%.

Usage:  python validation/gauge_depth_check.py --event brazos
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "validation"))

from backend.gauges import gauge_depth_for_points  # noqa: E402

EVENTS = {
    "brazos": {
        "bbox": [-95.9, 29.45, -95.6, 29.72],
        "window": ["2017-08-25", "2017-09-15"],
        "sar_reference": "outputs/hwm_check_brazos.json",
    },
    "harvey": {
        "bbox": [-95.88, 29.66, -95.46, 30.00],
        "window": ["2017-08-25", "2017-09-15"],
        "sar_reference": "outputs/hwm_check_harvey.json",
    },
}
OUT = ROOT / "outputs"


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0, c - h), 4), round(min(1, c + h), 4)]


def ground_ft_3dep(lat, lon, bbox):
    from backend.live_pipeline import init_ee
    if not init_ee():
        raise SystemExit("Earth Engine not configured; 3DEP ground needed.")
    import ee
    dem = ee.ImageCollection("USGS/3DEP/1m").filterBounds(ee.Geometry.Rectangle(bbox)).mosaic().select(0)
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([float(a), float(b)]), {"i": i})
                               for i, (a, b) in enumerate(zip(lon, lat))])
    res = dem.reduceRegions(fc, ee.Reducer.mean(), 1).getInfo()
    g = np.full(len(lat), np.nan)
    for f in res["features"]:
        v = f["properties"].get("mean")
        if v is not None:
            g[f["properties"]["i"]] = v * 3.28084
    return g


def summarize(dep, surv, sites, sel, label):
    sel = sel & np.isfinite(dep)
    if sel.sum() == 0:
        return {"label": label, "n_marks": 0}
    e = dep[sel] - surv[sel]
    st = sites[sel]
    det_sites = {s for s, d in zip(st, dep[sel]) if d > 0.1}
    ns = len(set(st))
    r = float(np.corrcoef(dep[sel], surv[sel])[0, 1]) if sel.sum() > 2 and np.std(dep[sel]) > 0 else None
    return {"label": label, "n_marks": int(sel.sum()), "n_sites": ns,
            "site_recall": round(len(det_sites) / ns, 4), "site_recall_detected": len(det_sites),
            "site_recall_ci95": wilson(len(det_sites), ns),
            "bias_ft": round(float(e.mean()), 3), "mae_ft": round(float(np.abs(e).mean()), 3),
            "pearson_r": round(r, 3) if r is not None else None,
            "surveyed_mean_ft": round(float(surv[sel].mean()), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="brazos", choices=EVENTS)
    a = ap.parse_args()
    cfg = EVENTS[a.event]
    import hwm_check as h

    bb = cfg["bbox"]
    marks = [m for m in h.fetch_hwms()
             if bb[0] <= m["longitude_dd"] <= bb[2] and bb[1] <= m["latitude_dd"] <= bb[3]
             and m.get("height_above_gnd") not in (None, 0, 0.0)]
    lat = np.array([m["latitude_dd"] for m in marks])
    lon = np.array([m["longitude_dd"] for m in marks])
    surv = np.array([m["height_above_gnd"] for m in marks], float)
    sites = np.array([m["site_id"] for m in marks])
    # Same function the product uses, with the distance cap lifted so the
    # report can show how accuracy falls off away from gauged channels.
    res = gauge_depth_for_points(lat, lon, bb, *cfg["window"], max_dist_m=1e9)
    wse, dist = res["wse_ft"], res["dist_to_channel_m"]
    ground = ground_ft_3dep(lat, lon, bb)
    depth = np.where(np.isfinite(wse), np.maximum(0.0, wse - ground), np.nan)
    river = [{"name": k, "gauges": v} for k, v in res["rivers_used"].items()]

    summaries = [summarize(depth, surv, sites, dist <= lim, f"within {lim / 1000:g} km of channel")
                 for lim in (1000, 3000, 5000)]
    summaries.append(summarize(depth, surv, sites, dist <= 3000,
                               "product rule (<= 3 km of a gauged channel)"))
    summaries.append(summarize(depth, surv, sites, np.ones(len(marks), bool), "all marks"))

    sar = None
    ref = ROOT / cfg["sar_reference"]
    if ref.exists():
        s = json.loads(ref.read_text())
        head = next((x for x in s.get("summaries", []) if x.get("label") == "radius 50 m"), None)
        if head:
            sar = {k: head.get(k) for k in ("n_sites", "site_recall", "site_recall_ci95", "bias_ft", "mae_ft")}

    out = {
        "event": a.event,
        "method": "gauge crest WSE (NWPS/NWIS, NAVD88) interpolated along each gauged NLDI "
                  "main-stem channel (nearest channel per point), minus USGS 3DEP 1 m ground",
        "ground_truth": "USGS STN high water marks, height_above_gnd (zero-height marks dropped)",
        "rivers_used": river,
        "summaries": summaries,
        "sar_detector_same_marks_50m": sar,
        "caveats": [
            "Only valid on the floodplain of the gauged main stem; tributary and ponding marks degrade it.",
            "No negative class: recall and depth error only, no false-positive rate.",
            "Small sample: distinct survey sites are the unit; see CIs.",
            "Distance cut-offs are reported side by side; none was tuned to this result.",
        ],
        "points": [{"lat": float(la), "lon": float(lo), "site": int(s), "surveyed_ft": float(sv),
                    "wse_ft": float(w) if np.isfinite(w) else None, "ground_ft": float(g) if np.isfinite(g) else None,
                    "depth_ft": float(dp) if np.isfinite(dp) else None,
                    "dist_to_channel_m": float(dd) if np.isfinite(dd) else None}
                   for la, lo, s, sv, w, g, dp, dd in zip(lat, lon, sites, surv, wse, ground, depth, dist)],
    }
    path = OUT / f"gauge_depth_check_{a.event}.json"
    path.write_text(json.dumps(out, indent=1))
    for s in summaries:
        print(s)
    print("SAR detector (hwm_check, 50 m):", sar)
    print("wrote", path)


if __name__ == "__main__":
    main()
