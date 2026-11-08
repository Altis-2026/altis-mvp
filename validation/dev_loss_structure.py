#!/usr/bin/env python3
"""
dev_loss_structure.py — choose the model STRUCTURE using only training storms.

Leave-one-storm-out inside the training set: fit on three of
(Matthew, Louisiana 2016, Harvey, Irma), score on the fourth, for each
candidate structure ('bracket' = value brackets, 'power' = power-law value
normalisation). Rule fixed in advance: pick the structure with the lowest mean
|aggregate ratio - 1| over the 8 (storm x depth basis) cells, full-information
scenario. The held-out storms (Florence, Imelda, Ida, Helene) are not read.
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "pipeline")); sys.path.insert(0, str(ROOT / "validation"))
from fit_loss_model import load, build_model, TRAIN  # noqa: E402
from loss_model_check import usable, predict_new  # noqa: E402


def main():
    df_all = load(TRAIN)
    out = {}
    for structure in ("bracket", "power"):
        cells = []
        for held in TRAIN:
            fit_df = df_all[df_all.event != held]
            model = build_model(fit_df, [e for e in TRAIN if e != held], structure)
            test_df = df_all[df_all.event == held]
            for basis, col in (("above_first_floor", "interiorWaterDepth"), ("above_ground", "exteriorWaterDepth")):
                x = usable(test_df, col)
                pred, _, _ = predict_new(x, col, basis, "full", model)
                act = x.actual.to_numpy()
                cells.append({"held_out": held, "basis": basis, "n": int(len(x)),
                              "agg_ratio": round(float(pred.sum() / act.sum()), 3),
                              "nmae": round(float(np.abs(pred - act).sum() / act.sum()), 3)})
                print(f"{structure:8s} held-out {held:14s} {basis:18s} n={len(x):6d} agg {cells[-1]['agg_ratio']:.2f} nMAE {cells[-1]['nmae']:.2f}", flush=True)
        out[structure] = {"cells": cells,
                          "mean_abs_agg_error": round(float(np.mean([abs(c["agg_ratio"] - 1) for c in cells])), 3),
                          "mean_nmae": round(float(np.mean([c["nmae"] for c in cells])), 3)}
        print(structure, "mean |agg-1|", out[structure]["mean_abs_agg_error"], "mean nMAE", out[structure]["mean_nmae"], flush=True)
    best = min(out, key=lambda k: out[k]["mean_abs_agg_error"])
    out["chosen"] = best
    out["rule"] = "lowest mean |aggregate ratio - 1| over leave-one-storm-out training cells"
    print("CHOSEN:", best)
    (ROOT / "outputs" / "dev_loss_structure.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
