#!/usr/bin/env python3
"""
conformal_check.py — protocol: docs/PREREG_CONFORMAL.md.

Calibrate a normalised conformal widening of the claim-level p10-p90 band on the
four held-out storms of the loss check, then measure its real coverage on three
reserved storms (Ian, Sally, Michael). Run the scoring ONCE.

  --dry-run   leave-one-storm-out on the four calibration storms only; the
              reserved storms are not read; nothing is written.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "pipeline")); sys.path.insert(0, str(ROOT / "validation"))
import loss_model as lm  # noqa: E402
from fit_loss_model import load  # noqa: E402
from loss_model_check import usable, predict_new, TRAIN  # noqa: E402

CAL = ("florence", "imelda", "ida", "helene")
RESERVED = ("ian", "sally", "michael")
LEVELS = (0.80, 0.90)
W_FLOOR = 1000.0
CELLS = [(sc, basis, col) for sc in ("full", "min")
         for basis, col in (("above_first_floor", "interiorWaterDepth"), ("above_ground", "exteriorWaterDepth"))]
DRY = "--dry-run" in sys.argv


def band_data(ev, sc, basis, col, model):
    x = usable(load([ev]), col)
    if len(x) < 300:
        return None
    _, p10, p90 = predict_new(x, col, basis, sc, model)
    y = x.actual.to_numpy(float)
    lim = x.totalBuildingInsuranceCoverage.to_numpy(float)
    dbin = np.array([lm.depth_bin(d) for d in x[col].to_numpy()])
    return {"y": y, "p10": p10, "p90": p90, "lim": lim, "bin": dbin}


def scores(d):
    w = np.maximum(d["p90"] - d["p10"], W_FLOOR)
    return np.maximum(d["p10"] - d["y"], d["y"] - d["p90"]) / w


def qhat(s, level):
    n = len(s)
    k = min(n, math.ceil((n + 1) * level))
    return float(np.sort(s)[k - 1])


def widen(d, q):
    w = np.maximum(d["p90"] - d["p10"], W_FLOOR)
    return np.maximum(0.0, d["p10"] - q * w), np.minimum(d["lim"], d["p90"] + q * w)


def coverage(d, q=None):
    lo, hi = (d["p10"], d["p90"]) if q is None else widen(d, q)
    return float(((d["y"] >= lo) & (d["y"] <= hi)).mean())


def pooled(ds):
    return {k: np.concatenate([d[k] for d in ds]) for k in ds[0]}


def main():
    model = lm.load_model()
    assert model and model["trained_on"] == list(TRAIN)
    out = {"protocol": "docs/PREREG_CONFORMAL.md", "cells": {}}
    cal = {}
    for sc, basis, col in CELLS:
        key = f"{sc}/{basis}"
        cal[key] = {ev: band_data(ev, sc, basis, col, model) for ev in CAL}
        cal[key] = {k: v for k, v in cal[key].items() if v is not None}
        print(f"calibration {key}: " + ", ".join(f"{k}={len(v['y'])}" for k, v in cal[key].items()), flush=True)

    # leave-one-storm-out on the calibration storms (reported, not gating)
    loso = {}
    for key, per in cal.items():
        loso[key] = {}
        for held in per:
            rest = pooled([d for k, d in per.items() if k != held])
            loso[key][held] = {"raw_p10_p90": round(coverage(per[held]), 3)}
            for L in LEVELS:
                loso[key][held][f"conformal_{L}"] = round(coverage(per[held], qhat(scores(rest), L)), 3)
    out["leave_one_storm_out"] = loso
    print(json.dumps(loso, indent=1))
    if DRY:
        print("DRY RUN OK: reserved storms not read; nothing written.")
        return

    q_store = {}
    for key, per in cal.items():
        s = scores(pooled(list(per.values())))
        q_store[key] = {str(L): round(qhat(s, L), 4) for L in LEVELS}
    out["q"] = q_store

    res, p1, p2n, p3, p4 = {}, [], [], [], []
    for sc, basis, col in CELLS:
        key = f"{sc}/{basis}"
        per = {}
        for ev in RESERVED:
            d = band_data(ev, sc, basis, col, model)
            if d is not None:
                per[ev] = d
        allp = pooled(list(per.values()))
        cell = {"n": {ev: int(len(d["y"])) for ev, d in per.items()}, "per_storm": {}}
        for ev, d in per.items():
            cell["per_storm"][ev] = {"raw": round(coverage(d), 3),
                                     "conformal_0.8": round(coverage(d, q_store[key]["0.8"]), 3),
                                     "conformal_0.9": round(coverage(d, q_store[key]["0.9"]), 3)}
        raw = coverage(allp)
        c80 = coverage(allp, q_store[key]["0.8"]); c90 = coverage(allp, q_store[key]["0.9"])
        cell["pooled"] = {"raw": round(raw, 3), "conformal_0.8": round(c80, 3), "conformal_0.9": round(c90, 3)}
        lo, hi = widen(allp, q_store[key]["0.8"])
        cell["median_width_share_of_limit_0.8"] = round(float(np.median((hi - lo) / allp["lim"])), 3)
        by_bin = {}
        for b in np.unique(allp["bin"]):
            m = allp["bin"] == b
            if m.sum() >= 200:
                sub = {k: v[m] for k, v in allp.items()}
                by_bin[int(b)] = {"n": int(m.sum()), "conformal_0.8": round(coverage(sub, q_store[key]["0.8"]), 3)}
        cell["by_depth_bin_0.8"] = by_bin
        res[key] = cell
        p1.append(0.75 <= c80 <= 0.85)
        p2n += [v["conformal_0.8"] >= 0.70 for v in cell["per_storm"].values()]
        p3.append(abs(c80 - 0.8) < abs(raw - 0.8) or abs(raw - 0.8) <= 0.02)
        p4.append(0.85 <= c90 <= 0.95)
        print(key, json.dumps(cell["pooled"]), json.dumps(cell["per_storm"]), flush=True)
    out["cells"] = res
    out["decision"] = {"P1_pooled_in_[.75,.85]_all_cells": f"{sum(p1)}/{len(p1)}", "P1_pass": all(p1),
                       "P2_storm_cells_ge_0.70": f"{sum(p2n)}/{len(p2n)} (need >= 9/12)", "P2_pass": sum(p2n) >= 9 and len(p2n) == 12,
                       "P3_closer_than_raw": f"{sum(p3)}/{len(p3)} (need >= 3/4)", "P3_pass": sum(p3) >= 3,
                       "P4_0.9_pooled_in_[.85,.95]": f"{sum(p4)}/{len(p4)} (need >= 3/4)", "P4_pass": sum(p4) >= 3}
    out["decision"]["overall_pass"] = bool(all(out["decision"][k] for k in ("P1_pass", "P2_pass", "P3_pass", "P4_pass")))
    print(json.dumps(out["decision"], indent=1))
    (ROOT / "outputs" / "conformal_check.json").write_text(json.dumps(out, indent=1))
    (ROOT / "outputs" / "conformal_q.json").write_text(json.dumps(
        {"source": "docs/PREREG_CONFORMAL.md", "calibrated_on": list(CAL), "w_floor_usd": W_FLOOR, "q": q_store}, indent=1))


if __name__ == "__main__":
    main()
