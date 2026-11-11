#!/usr/bin/env python3
"""
Do 'possible missed claim' properties (radar dry, river-gauge crest >= 1 ft)
sit in zips with more NFIP claims than other radar-dry properties?

Zip-level proxy only (redacted NFIP claims): it cannot confirm a per-house
flag, but a real signal should at least not point the wrong way.
Writes outputs/gauge_conflict_zip_check.json.
"""
import json
import sys
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.database import load_event_data  # noqa: E402


def main():
    out = {}
    for ev in ("brazos", "harvey"):
        df = load_event_data(ev)
        lab = pd.read_csv(ROOT / "outputs" / f"{ev}_labels.csv")[["property_id", "zip", "zip_truth_rate"]]
        d = df.merge(lab, on="property_id", how="inner")
        dry = d[pd.to_numeric(d.pct_flooded, errors="coerce").fillna(0) <= 5]
        flag = dry[dry.gauge_conflict == 1]
        unf = dry[dry.gauge_conflict != 1]
        z = dry.groupby("zip").agg(n=("property_id", "size"),
                                   nflag=("gauge_conflict", lambda s: int((s == 1).sum())),
                                   rate=("zip_truth_rate", "first"))
        rho = spearmanr(z.nflag / z.n, z.rate)
        out[ev] = {"radar_dry": int(len(dry)), "flagged": int(len(flag)),
                   "mean_zip_claim_rate_flagged": round(float(flag.zip_truth_rate.mean()), 4),
                   "mean_zip_claim_rate_unflagged": round(float(unf.zip_truth_rate.mean()), 4),
                   "n_zips": int(len(z)), "spearman_share_flagged_vs_rate": round(float(rho.statistic), 3),
                   "p_value": round(float(rho.pvalue), 3)}
        print(ev, out[ev])
    out["reading"] = ("Inconclusive. Brazos points the wrong way (flagged properties sit in lower-claim "
                      "zips), Harvey the right way; neither correlation is significant. Zip resolution "
                      "cannot verify a per-house flag; per-address claims are needed (FEMA ISAA or a "
                      "carrier pilot).")
    (ROOT / "outputs" / "gauge_conflict_zip_check.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
