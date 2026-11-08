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


def main():
    df = load(TRAIN)
    print(len(df), "training claims from", TRAIN)
    tables, n_train = {}, {}
    for cov, dmg, val, vmin in (("building", "buildingDamageAmount", "buildingPropertyValue", 10000),
                                ("contents", "contentsDamageAmount", "contentsPropertyValue", 1000)):
        for kind, dcol in (("interior", "interiorWaterDepth"), ("exterior", "exteriorWaterDepth")):
            x = df[(df[val] > vmin) & (df[dmg] >= 0) & (df[dcol] > 0) & (df[dcol] <= MAX_DEPTH_IN)]
            ratio = np.clip((x[dmg] / x[val]).to_numpy(), 0.0, 1.2)
            tables[f"{cov}_{kind}"] = lm.fit_distributions(ratio, x[dcol].to_numpy(), x.segment.to_numpy())
            n_train[f"{cov}_{kind}"] = int(len(x))
            print(f"  {cov}_{kind}: {len(x)} claims, mean ratio {ratio.mean():.3f}")

    # Replacement-cost uplift theta: paid + deductible = damage * (1 + theta*(RC/ACV - 1)).
    b = df[(df.buildingDamageAmount > 0) & df.ded.notna() & (df.buildingPropertyValue > 10000)
           & (df.buildingReplacementCost > 10000) & (df.totalBuildingInsuranceCoverage > 0)
           & (df.replacementCostBasis == "R")].copy()
    b = b[b.buildingDamageAmount * 1.6 < b.totalBuildingInsuranceCoverage]       # clearly below the limit
    b["up"] = b.buildingReplacementCost / b.buildingPropertyValue
    t = b[(b.up > 1.05) & (b.amountPaidOnBuildingClaim > 0)]
    theta_i = ((t.amountPaidOnBuildingClaim + t.ded) / t.buildingDamageAmount - 1) / (t.up - 1)
    theta = float(theta_i.median())
    lo, hi = np.percentile(theta_i, [25, 75])
    print(f"theta = {theta:.3f} (IQR {lo:.2f}..{hi:.2f}) on {len(t)} replacement-cost claims")

    v = df[(df.buildingPropertyValue > 10000) & (df.totalBuildingInsuranceCoverage > 0)]
    rc = df[(df.buildingReplacementCost > 10000) & (df.buildingPropertyValue > 10000)]
    fallbacks = {"acv_value_over_coverage": round(float((v.buildingPropertyValue / v.totalBuildingInsuranceCoverage).median()), 3),
                 "rc_over_acv": round(float((rc.buildingReplacementCost / rc.buildingPropertyValue).median()), 3),
                 "modal_building_deductible_code": df.buildingDeductibleCode.astype(str).mode().iat[0]}
    # What the national shapes shipped before say, at the same depths, for the record.
    from config import SEVERITY
    from severity import _interpolate
    comp = []
    for row in tables["building_interior"]["all"]:
        c = row["cell"]
        if c:
            ft = row["centre_in"] / 12.0
            comp.append({"centre_in": row["centre_in"], "claims_mean_damage_pct": round(c["mean"] * 100, 1),
                         "national_generic_pct": round(_interpolate(SEVERITY["depth_damage_curve"], ft), 1), "n": c["n"]})
    out = {"trained_on": list(TRAIN), "n_train": n_train, "theta": round(theta, 3),
           "theta_iqr": [round(float(lo), 3), round(float(hi), 3)], "theta_n": int(len(t)),
           "fallbacks": fallbacks, "tables": tables, "national_vs_claims_building_interior": comp,
           "note": "Fitted on training storms only; see docs/PREREG_LOSS_MODEL.md"}
    (ROOT / "outputs" / "loss_model.json").write_text(json.dumps(out))
    print(json.dumps(fallbacks)); 
    for r in comp: print(r)


if __name__ == "__main__":
    main()
