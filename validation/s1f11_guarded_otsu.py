#!/usr/bin/env python3
"""
Score the PIPELINE's own single-image threshold step on Sen1Floods11:
50 m (radius 5 px) focal-mean speckle filter on VV, Otsu on 0.5 dB bins,
then the open-water range guard from pipeline/config.py SAR: thresholds
outside [water_db_min, water_db_max] fall back to otsu_fallback_db.
Compared with plain per-chip Otsu (s1f11_benchmark.py). This isolates the
threshold step; the pipeline's slope, permanent-water, HAND and baseline
layers are not reproducible on these chips (no pre-event image).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "pipeline")); sys.path.insert(0, str(ROOT / "validation"))
from config import SAR  # noqa: E402
from s1f11_benchmark import load_chip, read_split, scores, summarize  # noqa: E402


def otsu_bins(v, width=0.5):
    v = v[np.isfinite(v)]
    if v.size < 100:
        return None
    edges = np.arange(np.floor(v.min()), np.ceil(v.max()) + width, width)
    hist, edges = np.histogram(v, bins=edges)
    c = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist); w1 = w0[-1] - w0
    m0 = np.cumsum(hist * c) / np.maximum(w0, 1)
    m1 = (np.sum(hist * c) - np.cumsum(hist * c)) / np.maximum(w1, 1)
    return float(c[np.argmax(w0 * w1 * (m0 - m1) ** 2)])


def speckle(vv, radius_px=5):
    valid = np.isfinite(vv).astype(float)
    k = 2 * radius_px + 1
    n = uniform_filter(valid, k, mode="constant")
    s = uniform_filter(np.where(valid > 0, vv, 0.0), k, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(valid > 0, s / n, np.nan)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True)
    a = ap.parse_args(); data = Path(a.data); rng = np.random.default_rng(0)
    lo, hi, fb = SAR["water_db_min"], SAR["water_db_max"], SAR["otsu_fallback_db"]
    res = {}
    for split in ("test", "bolivia"):
        per, fallbacks = [], 0
        rows = read_split(data, f"flood_{split}_data")
        for row in rows:
            x, y = load_chip(data, *row)
            vv = speckle(x[0])
            t = otsu_bins(vv)
            if t is None or not (lo <= t <= hi):
                t = fb; fallbacks += 1
            pred = (vv < t) & np.isfinite(x[0])
            per.append(scores(pred, y))
        res[split] = {**summarize(per, rng), "n_fallback": fallbacks}
        s = res[split]
        print(f"{split}: pipeline guarded Otsu IoU {s['iou']} {s['iou_ci95']} P {s['precision']} R {s['recall']} "
              f"(fallback on {fallbacks}/{len(rows)} chips)")
    bench = ROOT / "outputs" / "s1f11_benchmark.json"
    if bench.exists():
        b = json.loads(bench.read_text())
        b["pipeline_guarded_otsu"] = {"guard_db": [lo, hi], "fallback_db": fb, "speckle_radius_px": 5,
                                      "results": res,
                                      "note": "threshold step only; slope/HAND/permanent-water/baseline layers not reproducible on these chips"}
        bench.write_text(json.dumps(b, indent=1))


if __name__ == "__main__":
    main()
