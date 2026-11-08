#!/usr/bin/env python3
"""
fit_loss_model.py — fit the claims-calibrated loss model on TRAINING storms
only (Matthew 2016, Louisiana 2016, Harvey 2017, Irma 2017) and write
outputs/loss_model.json. The held-out storms are never read here.

Damage ratio = ACV damage / ACV value, both from the claim record (FEMA's
buildingDamageAmount and buildingPropertyValue are actual-cash-value figures).
Depth: interiorWaterDepth (above first floor) and exteriorWaterDepth (above
lowest adjacent grade), both inches. The mixed-unit waterDepth is not used.
A recorded depth of 0 means 'unrecorded' (those claims average ~30% damage),
so only depth > 0 rows enter. Zero-damage claims are kept: the model is
conditional on a claim being filed, including ones that end at $0.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "pipeline"))
import loss_model as lm  # noqa: E402
from policy_terms import DEDUCTIBLE_CODES  # noqa: E402

# Depths above 20 ft (240 in) are placeholders such as 999 (unknown), not measurements.
MAX_DEPTH_IN = 240
TRAIN = ("matthew", "louisiana2016", "harvey", "irma")
CACHE = ROOT / "outputs" / "nfip_loss_cache"
NUM = ["buildingDamageAmount", "buildingPropertyValue", "buildingReplacementCost", "totalBuildingInsuranceCoverage",
       "amountPaidOnBuildingClaim", "contentsDamageAmount", "contentsPropertyValue", "totalContentsInsuranceCoverage",
       "interiorWaterDepth", "exteriorWaterDepth", "basementEnclosureCrawlspaceType"]


def load(names):
    df = pd.concat([pd.read_csv(CACHE / f"{n}.csv.gz", low_memory=False).assign(event=n) for n in names])
    for c in NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["ded"] = df.buildingDeductibleCode.astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True).map(DEDUCTIBLE_CODES)
    df["segment"] = [lm.segment_for(o, b in (1, 2)) for o, b in zip(df.occupancyType.astype(str), df.basementEnclosureCrawlspaceType)]
    return df


V0 = 200_000.0


def fit_power_c(x, dmg, val, dcol):
    """Slope c in  ratio ~ value^-c  from cell means (depth-bin fixed effects)."""
    r = np.clip((x[dmg] / x[val]).to_numpy(), 0, 1.2)
    v = x[val].to_numpy(); b = np.array([lm.depth_bin(d) for d in x[dcol].to_numpy()])
    vb = np.array([lm.value_bracket(a) for a in v])
    rows = []
    for bi in np.unique(b):
        for vi in np.unique(vb):
            m = (b == bi) & (vb == vi)
            if m.sum() >= 150 and r[m].mean() > 0:
                rows.append((bi, np.log(np.median(v[m])), np.log(r[m].mean()), m.sum()))
    t = np.array(rows)
    # demean log-ratio and log-value within depth bin, then weighted OLS slope
    X, Y, W = [], [], []
    for bi in np.unique(t[:, 0]):
        g = t[t[:, 0] == bi]
        if len(g) < 3:
            continue
        X += list(g[:, 1] - np.average(g[:, 1], weights=g[:, 3])); Y += list(g[:, 2] - np.average(g[:, 2], weights=g[:, 3])); W += list(g[:, 3])
    X, Y, W = map(np.array, (X, Y, W))
    return float(-(W * X * Y).sum() / (W * X * X).sum())


def build_model(df, trained_on, structure="bracket"):
    """Fit tables on a dataframe of claims. structure: 'bracket' or 'power'."""
    tables, n_train, value_norm = {}, {}, {}
    for cov, dmg, val, vmin in (("building", "buildingDamageAmount", "buildingPropertyValue", 10000),
                                ("contents", "contentsDamageAmount", "contentsPropertyValue", 1000)):
        for kind, dcol in (("interior", "interiorWaterDepth"), ("exterior", "exteriorWaterDepth")):
            x = df[(df[val] > vmin) & (df[dmg] >= 0) & (df[dcol] > 0) & (df[dcol] <= MAX_DEPTH_IN)]
            ratio = np.clip((x[dmg] / x[val]).to_numpy(), 0.0, 1.2)
            key = f"{cov}_{kind}"
            if structure == "power":
                c = max(0.0, fit_power_c(x, dmg, val, dcol))
                value_norm[key] = {"c": round(c, 3), "v0": V0}
                ratio = np.clip(ratio * (x[val].to_numpy() / V0) ** c, 0, 1.2 * 3)   # normalised ratio r'
                tables[key] = lm.fit_distributions(ratio, x[dcol].to_numpy(), x.segment.to_numpy(), np.full(len(x), V0))
                tables[key] = {k: v for k, v in tables[key].items() if k.endswith("|all") or k.endswith("|3")}
                tables[key] = {(k.replace("|3", "|all") if k.endswith("|3") else k): v for k, v in tables[key].items()}
            else:
                tables[key] = lm.fit_distributions(ratio, x[dcol].to_numpy(), x.segment.to_numpy(), x[val].to_numpy())
            n_train[key] = int(len(x))
    b = df[(df.buildingDamageAmount > 0) & df.ded.notna() & (df.buildingPropertyValue > 10000)
           & (df.buildingReplacementCost > 10000) & (df.totalBuildingInsuranceCoverage > 0)
           & (df.replacementCostBasis == "R")].copy()
    b = b[b.buildingDamageAmount * 1.6 < b.totalBuildingInsuranceCoverage]
    b["up"] = b.buildingReplacementCost / b.buildingPropertyValue
    t = b[(b.up > 1.05) & (b.amountPaidOnBuildingClaim > 0)]
    th = ((t.amountPaidOnBuildingClaim + t.ded) / t.buildingDamageAmount - 1) / (t.up - 1)
    model = {"trained_on": list(trained_on), "n_train": n_train, "structure": structure,
             "theta": round(float(th.median()), 3), "theta_n": int(len(t)), "tables": tables}
    if value_norm:
        model["value_norm"] = value_norm
    return model


def main():
    structure = sys.argv[sys.argv.index("--structure") + 1] if "--structure" in sys.argv else "bracket"
    df = load(TRAIN)
    print(len(df), "training claims from", TRAIN, "| structure:", structure)
    out = build_model(df, TRAIN, structure)
    v = df[(df.buildingPropertyValue > 10000) & (df.totalBuildingInsuranceCoverage > 0)]
    rc = df[(df.buildingReplacementCost > 10000) & (df.buildingPropertyValue > 10000)]
    out["fallbacks"] = {"acv_value_over_coverage": round(float((v.buildingPropertyValue / v.totalBuildingInsuranceCoverage).median()), 3),
                        "rc_over_acv": round(float((rc.buildingReplacementCost / rc.buildingPropertyValue).median()), 3),
                        "modal_building_deductible_code": df.buildingDeductibleCode.astype(str).mode().iat[0]}
    out["note"] = "Fitted on training storms only; see docs/PREREG_LOSS_MODEL.md"
    print("theta", out["theta"], "| value_norm", out.get("value_norm"))
    (ROOT / "outputs" / "loss_model.json").write_text(json.dumps(out))


if __name__ == "__main__":
    main()
