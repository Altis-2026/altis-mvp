#!/usr/bin/env python3
"""
loss_model_check.py — score the loss calculation ONCE on held-out storms
(protocol: docs/PREREG_LOSS_MODEL.md). Run exactly once; do not tune after.

What it tests: given the claim's own recorded water depth, does the dollar
calculation (damage distribution + policy terms) reproduce what the NFIP
actually paid? It does NOT test flood detection or claim propensity.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "pipeline")); sys.path.insert(0, str(ROOT / "validation"))
import loss_model as lm  # noqa: E402
from policy_terms import PolicyTerms, deductible_from_code  # noqa: E402
from config import SEVERITY  # noqa: E402
from severity import _interpolate  # noqa: E402
from fit_loss_model import load, TRAIN, MAX_DEPTH_IN  # noqa: E402

TEST = ("florence", "imelda", "ida", "helene")
EVENT_START = {"florence": "2018-09", "imelda": "2019-09", "ida": "2021-08", "helene": "2024-09"}
N_BOOT = 1000


def cpi_factor(frm, to):
    t = {}
    for line in (ROOT / "outputs" / "library" / "cpi_u.csv").read_text().splitlines()[1:]:
        d, _, v = line.partition(",")
        t[d[:7]] = float(v)
    return t[to] / t[frm]


def usable(df, depth_col):
    x = df[(df.buildingPropertyValue > 10000) & (df.buildingReplacementCost > 10000)
           & (df.totalBuildingInsuranceCoverage > 0) & df.ded.notna()
           & df.replacementCostBasis.isin(["R", "A"]) & df.amountPaidOnBuildingClaim.notna()
           & df.buildingDamageAmount.notna() & (df[depth_col] > 0) & (df[depth_col] <= MAX_DEPTH_IN)].copy()
    x["actual"] = x.amountPaidOnBuildingClaim.clip(lower=0)
    return x


def predict_new(x, depth_col, basis, scenario, model):
    out, p10, p90 = [], [], []
    for r in x.itertuples():
        limit = float(r.totalBuildingInsuranceCoverage)
        if scenario == "full":
            t = PolicyTerms(limit=limit, deductible=float(r.ded), valuation="RCV" if r.replacementCostBasis == "R" else "ACV",
                            acv_value=float(r.buildingPropertyValue), replacement_cost=float(r.buildingReplacementCost))
        elif scenario == "lite":     # limit, deductible, valuation known; values estimated from the limit
            t = PolicyTerms(limit=limit, deductible=float(r.ded), valuation="RCV" if r.replacementCostBasis == "R" else "ACV")
        elif scenario == "min":      # only the limit is known
            t = PolicyTerms(limit=limit)
        else:                        # "noterms": curve x value with no deductible/valuation/limit effect
            t = PolicyTerms(limit=1e12, deductible=0.0, valuation="ACV", acv_value=float(r.buildingPropertyValue),
                            replacement_cost=float(r.buildingReplacementCost))
        seg = lm.segment_for(r.occupancyType, r.basementEnclosureCrawlspaceType in (1, 2))
        res = lm.expected_loss(getattr(r, depth_col) / 12.0, 0.0, t, coverage="building", depth_basis=basis, segment=seg, model=model)
        out.append(res["mid"]); p10.append(res["claim_p10"]); p90.append(res["claim_p90"])
    return np.array(out, float), np.array(p10, float), np.array(p90, float)


def metrics(pred, actual):
    return {"agg_ratio": float(pred.sum() / actual.sum()),
            "nmae": float(np.abs(pred - actual).sum() / actual.sum()),
            "spearman": float(spearmanr(pred, actual).statistic) if np.std(pred) > 0 else None}


def cluster_boot(x, preds: dict, rng):
    """Bootstrap by county: agg ratio and nMAE per method, and nMAE difference vs 'avg' and 'shipped'."""
    counties = x.countyCode.astype(str).to_numpy()
    uniq = np.unique(counties)
    idx_by = {c: np.where(counties == c)[0] for c in uniq}
    act = x.actual.to_numpy()
    agg = {k: [] for k in preds}; nm = {k: [] for k in preds}
    for _ in range(N_BOOT):
        pick = rng.choice(uniq, len(uniq))
        idx = np.concatenate([idx_by[c] for c in pick])
        a = act[idx]
        if a.sum() <= 0:
            continue
        for k, p in preds.items():
            agg[k].append(p[idx].sum() / a.sum()); nm[k].append(np.abs(p[idx] - a).sum() / a.sum())
    ci = lambda v: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]  # noqa: E731
    res = {k: {"agg_ci95": ci(agg[k]), "nmae_ci95": ci(nm[k])} for k in preds}
    for base in ("avg", "shipped"):
        d = np.array(nm["full"]) - np.array(nm[base])
        res["full"][f"nmae_diff_vs_{base}_ci95"] = ci(d)
    return res


def main():
    model = lm.load_model()
    assert model and model["trained_on"] == list(TRAIN), "model must be the training-storm fit"
    train = load(TRAIN)
    train_paid = train.amountPaidOnBuildingClaim.clip(lower=0)
    rng = np.random.default_rng(0)
    results = {}
    for ev in TEST:
        df = load([ev])
        total = int((ROOT / "outputs" / "nfip_loss_cache" / f"{ev}.total").read_text())
        for basis, depth_col in (("above_first_floor", "interiorWaterDepth"), ("above_ground", "exteriorWaterDepth")):
            x = usable(df, depth_col)
            if len(x) < 500:
                results[f"{ev}/{basis}"] = {"n": len(x), "skipped": "fewer than 500 usable claims"}
                continue
            depth_ft = x[depth_col].to_numpy() / 12.0
            preds = {}
            for sc in ("full", "lite", "min", "noterms"):
                preds[sc], p10, p90 = predict_new(x, depth_col, basis, sc, model)
                if sc == "full":
                    band = float(((x.actual.to_numpy() >= p10) & (x.actual.to_numpy() <= p90)).mean())
            preds["shipped"] = np.array([_interpolate(SEVERITY["depth_damage_curve"], d) / 100.0 for d in depth_ft]) \
                * x.totalBuildingInsuranceCoverage.to_numpy()
            preds["avg"] = np.full(len(x), train_paid.mean() * cpi_factor("2017-09", EVENT_START[ev]))
            act = x.actual.to_numpy()
            cell = {"n_claims": int(len(x)), "n_claims_in_api": total, "actual_mean_paid": round(float(act.mean())),
                    "share_zero_paid": round(float((act == 0).mean()), 3),
                    "methods": {k: {kk: (round(vv, 4) if vv is not None else None) for kk, vv in metrics(p, act).items()}
                                for k, p in preds.items()},
                    "full_p10_p90_coverage": round(band, 3)}
            cell["bootstrap"] = cluster_boot(x, preds, rng)
            results[f"{ev}/{basis}"] = cell
            m = cell["methods"]
            print(f"{ev:9s} {basis:18s} n={len(x):6d} | agg full {m['full']['agg_ratio']:.2f} lite {m['lite']['agg_ratio']:.2f} "
                  f"min {m['min']['agg_ratio']:.2f} noterms {m['noterms']['agg_ratio']:.2f} shipped {m['shipped']['agg_ratio']:.2f} "
                  f"avg {m['avg']['agg_ratio']:.2f} | nMAE full {m['full']['nmae']:.2f} avg {m['avg']['nmae']:.2f} "
                  f"shipped {m['shipped']['nmae']:.2f} | rho {m['full']['spearman']:.2f} | band {band:.2f}", flush=True)

    cells = [c for c in results.values() if "methods" in c]
    c1 = sum(abs(c["methods"]["full"]["agg_ratio"] - 1) <= 0.20 for c in cells)
    c2a = sum(c["methods"]["full"]["nmae"] < c["methods"]["avg"]["nmae"] for c in cells)
    c2b = sum(c["bootstrap"]["full"]["nmae_diff_vs_avg_ci95"][1] < 0 for c in cells)
    c3 = sum(abs(c["methods"]["full"]["agg_ratio"] - 1) < abs(c["methods"]["shipped"]["agg_ratio"] - 1) for c in cells)
    n = len(cells)
    decision = {"cells": n,
                "C1_aggregate_within_20pct": f"{c1}/{n} (need >= 75%)", "C1_pass": c1 >= 0.75 * n,
                "C2_nmae_below_average_reserve_all": f"{c2a}/{n} (need all)",
                "C2_nmae_diff_ci_below_zero": f"{c2b}/{n} (need >= 75%)", "C2_pass": (c2a == n and c2b >= 0.75 * n),
                "C3_aggregate_closer_than_shipped": f"{c3}/{n} (need >= 7/8)", "C3_pass": c3 >= 0.875 * n}
    decision["overall_pass"] = bool(decision["C1_pass"] and decision["C2_pass"] and decision["C3_pass"])
    print(json.dumps(decision, indent=1))
    (ROOT / "outputs" / "loss_model_check.json").write_text(json.dumps(
        {"protocol": "docs/PREREG_LOSS_MODEL.md", "train": list(TRAIN), "test": list(TEST),
         "results": results, "decision": decision}, indent=1))


if __name__ == "__main__":
    main()
