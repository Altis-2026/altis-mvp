#!/usr/bin/env python3
"""
Contents-coverage accuracy on the held-out storms. The protocol
(docs/PREREG_LOSS_MODEL.md) promised to REPORT contents but the main scoring
script only scored buildings; this closes that gap. Same model, same method,
no changes made after the building result. Not gating.

Contents are settled at actual cash value, so terms = ACV valuation with the
claim's contents deductible, contents value and contents limit.
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "pipeline")); sys.path.insert(0, str(ROOT / "validation"))
import loss_model as lm  # noqa: E402
from policy_terms import PolicyTerms, DEDUCTIBLE_CODES  # noqa: E402
from fit_loss_model import load, TRAIN, MAX_DEPTH_IN  # noqa: E402
from loss_model_check import TEST, EVENT_START, cpi_factor, storm_calibration  # noqa: E402


def main():
    model = lm.load_model()
    train = load(TRAIN)
    tp = train.amountPaidOnContentsClaim.clip(lower=0)
    train_mean = float(tp[train.totalContentsInsuranceCoverage > 0].mean())
    out = {}
    for ev in TEST:
        df = load([ev])
        for c in ("contentsPropertyValue", "contentsDamageAmount", "totalContentsInsuranceCoverage", "amountPaidOnContentsClaim"):
            df[c] = np.nan_to_num(df[c].astype(float), nan=np.nan)
        df["cded"] = df.contentsDeductibleCode.astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True).map(DEDUCTIBLE_CODES)
        for basis, col in (("above_first_floor", "interiorWaterDepth"), ("above_ground", "exteriorWaterDepth")):
            x = df[(df.contentsPropertyValue > 1000) & (df.totalContentsInsuranceCoverage > 0) & df.cded.notna()
                   & df.amountPaidOnContentsClaim.notna() & df.contentsDamageAmount.notna()
                   & (df[col] > 0) & (df[col] <= MAX_DEPTH_IN)].copy()
            if len(x) < 500:
                out[f"{ev}/{basis}"] = {"n": len(x), "skipped": "fewer than 500 usable claims"}
                continue
            act = x.amountPaidOnContentsClaim.clip(lower=0).to_numpy()
            pred = []
            for r in x.itertuples():
                t = PolicyTerms(limit=float(r.totalContentsInsuranceCoverage), deductible=float(r.cded), valuation="ACV",
                                acv_value=float(r.contentsPropertyValue), replacement_cost=float(r.contentsPropertyValue))
                seg = lm.segment_for(r.occupancyType, r.basementEnclosureCrawlspaceType in (1, 2))
                pred.append(lm.expected_loss(getattr(r, col) / 12.0, 0.0, t, coverage="contents", depth_basis=basis,
                                             segment=seg, model=model)["mid"])
            pred = np.array(pred, float)
            avg = np.full(len(x), train_mean * cpi_factor("2017-09", EVENT_START[ev]))
            cell = {"n_claims": int(len(x)), "agg_ratio": round(float(pred.sum() / act.sum()), 3),
                    "nmae_model": round(float(np.abs(pred - act).sum() / act.sum()), 3),
                    "agg_ratio_avg_reserve": round(float(avg.sum() / act.sum()), 3),
                    "nmae_avg_reserve": round(float(np.abs(avg - act).sum() / act.sum()), 3),
                    "after_200_settled_median_err": storm_calibration(pred, act, ks=(200,)).get("200", {}).get("median_abs_agg_error")}
            out[f"{ev}/{basis}"] = cell
            print(ev, basis, cell, flush=True)
    cells = [c for c in out.values() if "agg_ratio" in c]
    out["summary"] = {"mean_abs_agg_error_model": round(float(np.mean([abs(c["agg_ratio"] - 1) for c in cells])), 3),
                      "mean_abs_agg_error_avg_reserve": round(float(np.mean([abs(c["agg_ratio_avg_reserve"] - 1) for c in cells])), 3),
                      "cells_nmae_better_than_avg": f"{sum(c['nmae_model'] < c['nmae_avg_reserve'] for c in cells)}/{len(cells)}"}
    print(out["summary"])
    (ROOT / "outputs" / "loss_model_contents_check.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
