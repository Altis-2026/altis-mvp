#!/usr/bin/env python3
"""
analog_backtest.py (v2, protocol in docs/PREREG_ANALOGS_V2.md) — Do
historical analogs predict an event's insured outcomes better than a naive
guess? v2 = payouts in constant dollars (CPI-U) + a 7-day IMERG rainfall term.
The v1 result (nominal, no rainfall) is outputs/analog_backtest_v1.json.

Leave-one-out over every FEMA-library event with >= 100 NFIP claims: for each,
query the library as it would be queried live (location of its claims,
start date, cause), with the event itself and its same-storm twins excluded
(the library's own same-event rule), and predict its median payout per paid
claim and share of claims paid from the analog priors. Baseline: the median
over all OTHER events. Scored with median absolute error (log10 dollars for
payout) and Spearman rank correlation.

Also reports the same with location only and cause only, to see what drives it.
Writes outputs/analog_backtest.json.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import event_library as el  # noqa: E402

MIN_CLAIMS = 100


def boot_ci(err_a, err_b, rng, n=2000):
    """95% CI of median(|a|) - median(|b|) by bootstrap over events."""
    a, b = np.asarray(err_a), np.asarray(err_b)
    d = []
    for _ in range(n):
        i = rng.integers(0, len(a), len(a))
        d.append(np.median(a[i]) - np.median(b[i]))
    return [round(float(v), 4) for v in np.percentile(d, [2.5, 97.5])]


def main():
    lib = el.load_library(refresh=True)
    evs = [r for r in lib if r["source"] == "fema" and r.get("nfip")
           and (r["nfip"].get("n_claims") or 0) >= MIN_CLAIMS
           and r["nfip"].get("median_paid_usd_real") and r["nfip"].get("share_paid") is not None]
    print(f"{len(evs)} FEMA events with >= {MIN_CLAIMS} NFIP claims", flush=True)
    rng = np.random.default_rng(0)
    results = {}
    for mode in ("full", "full_no_rain", "location_only", "cause_only"):
        pred_pay, pred_share, base_pay, base_share, act_pay, act_share = [], [], [], [], [], []
        for e in evs:
            others = [o for o in evs if o["id"] != e["id"]]
            base_pay.append(np.median([o["nfip"]["median_paid_usd_real"] for o in others]))
            base_share.append(np.median([o["nfip"]["share_paid"] for o in others]))
            lon, lat = e["centroid"]
            if mode == "cause_only":
                lat, lon = 0.0, 0.0   # distance term then carries no information
            res = el.find_analogs(lat, lon, e["start"] if mode != "cause_only" else None,
                                  e["cause"] if mode != "location_only" else None,
                                  k=8, exclude_ids=(e["id"],), us_only=True,
                                  rain_mm=e.get("rain_mm") if mode == "full" else None)
            pr = res["priors"]
            pred_pay.append(pr["median_paid_per_claim_usd"] if pr and pr["median_paid_per_claim_usd"] else base_pay[-1])
            pred_share.append(pr["share_of_claims_paid"] if pr and pr["share_of_claims_paid"] is not None else base_share[-1])
            act_pay.append(e["nfip"]["median_paid_usd_real"]); act_share.append(e["nfip"]["share_paid"])
        lp = lambda v: np.log10(np.maximum(np.asarray(v, float), 1))  # noqa: E731
        e_an = np.abs(lp(pred_pay) - lp(act_pay)); e_base = np.abs(lp(base_pay) - lp(act_pay))
        s_an = np.abs(np.asarray(pred_share) - act_share); s_base = np.abs(np.asarray(base_share) - act_share)
        results[mode] = {
            "n_events": len(evs),
            "payout_median_abs_err_log10": {"analogs": round(float(np.median(e_an)), 4),
                                            "baseline": round(float(np.median(e_base)), 4),
                                            "diff_ci95": boot_ci(e_an, e_base, rng)},
            "payout_spearman_analogs": round(float(spearmanr(pred_pay, act_pay).statistic), 3),
            "share_paid_median_abs_err": {"analogs": round(float(np.median(s_an)), 4),
                                          "baseline": round(float(np.median(s_base)), 4),
                                          "diff_ci95": boot_ci(s_an, s_base, rng)},
            "share_paid_spearman_analogs": round(float(spearmanr(pred_share, act_share).statistic), 3),
            # Descriptive only (does not enter the decision): how far off past estimates were.
            "error_quantiles": {
                "payout_log10_abs": {q: round(float(np.quantile(e_an, q / 100)), 4) for q in (50, 80, 90)},
                "share_paid_abs": {q: round(float(np.quantile(s_an, q / 100)), 4) for q in (50, 80, 90)},
            },
        }
        print(mode, json.dumps(results[mode]), flush=True)
    hi = results["full"]["payout_median_abs_err_log10"]["diff_ci95"][1]
    results["version"] = 2
    results["dollars"] = "constant latest-month dollars (CPI-U)"
    results["decision"] = {"rule": "forecast only if the primary diff CI lies entirely below 0",
                           "primary_ci_upper": hi, "forecast_grade": bool(hi < 0)}
    results["reading_guide"] = ("diff_ci95 is analogs minus baseline error: entirely below 0 means analogs "
                                "are reliably better; straddling 0 means not shown to help.")
    (ROOT / "outputs" / "analog_backtest.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
