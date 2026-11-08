"""
Storm calibration: learn this storm's dollar scale from the first settled
claims and apply it to the rest of the book.

WHY: depth -> dollars differs from storm to storm by a common factor (held-out
storms: total off by 0.73x to 1.33x given the TRUE depth), uniformly across
property type and size, and no public field explains it. Settled payments do.
Validated in docs/PREREG_LOSS_MODEL.md (criterion C4): after 50 / 200 / 1,000
settled claims the storm total was typically within 9% / 6% / 2.5%.

Always computed against the ORIGINAL (uncalibrated) predictions, so applying a
new batch of settled claims replaces the previous calibration instead of
stacking on it.

CAVEATS carried to the caller: claims that settle first skew small and simple,
so the factor from a handful of early closures can be biased low; the interval
returned is a sampling interval and does not cover that bias.
"""
from __future__ import annotations

import numpy as np

MIN_CLAIMS = 30


def storm_factor(pred, paid, n_boot: int = 1000, seed: int = 0) -> dict:
    pred = np.asarray(pred, float)
    paid = np.asarray(paid, float)
    ok = np.isfinite(pred) & np.isfinite(paid) & (pred >= 0) & (paid >= 0)
    pred, paid = pred[ok], paid[ok]
    n = int(len(pred))
    if n < MIN_CLAIMS or pred.sum() <= 0:
        return {"ok": False, "n": n, "reason": f"need at least {MIN_CLAIMS} settled claims with a prediction"}
    f = float(paid.sum() / pred.sum())
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        if pred[i].sum() > 0:
            boots.append(paid[i].sum() / pred[i].sum())
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"ok": True, "n": n, "factor": round(f, 4), "ci95": [round(float(lo), 4), round(float(hi), 4)],
            "predicted_total": round(float(pred.sum())), "paid_total": round(float(paid.sum())),
            "low_n": n < 100}


SCALED = ("severity_low_usd", "severity_mid_usd", "severity_high_usd", "contents_low_usd", "contents_mid_usd",
          "contents_high_usd", "total_mid_usd", "net_claim_p10_usd", "net_claim_p90_usd")


def apply_factor(rows: list, factor: float, matched_ids: set) -> dict:
    """Scale every row's dollar fields by `factor` from its ORIGINAL values.

    Returns the new exposure totals. Rows keep `uncal_<field>` so a later
    calibration starts from the original again."""
    low = mid = high = 0
    for r in rows:
        if r.get("severity_mid_usd") is None:
            continue
        for k in SCALED:
            if r.get(k) is None:
                continue
            r.setdefault(f"uncal_{k}", r[k])
            r[k] = int(round(r[f"uncal_{k}"] * factor))
        r["storm_factor"] = factor
        r["calibrated_from_settled_claim"] = r.get("property_id") in matched_ids
        low += r["severity_low_usd"]; mid += r["severity_mid_usd"]; high += r["severity_high_usd"]
    return {"est_loss_low_usd": low, "est_loss_mid_usd": mid, "est_loss_high_usd": high}


def match_rows(rows: list, settled: list) -> tuple[list, list, set]:
    """Pair settled payments with stored predictions by property_id, else policy_number.

    Prediction used is the ORIGINAL total (building + contents). Returns
    (pred, paid, matched_property_ids)."""
    by_pid = {r.get("property_id"): r for r in rows}
    by_pol = {str(r.get("policy_number")): r for r in rows if r.get("policy_number")}
    pred, paid, ids = [], [], set()
    for s in settled:
        try:
            amt = float(str(s.get("paid_amount")).replace(",", "").replace("$", ""))
        except (TypeError, ValueError):
            continue
        r = by_pid.get(s.get("property_id")) or by_pol.get(str(s.get("policy_number")))
        if not r or r.get("severity_mid_usd") is None or r["property_id"] in ids:
            continue
        base = r.get("uncal_total_mid_usd", r.get("total_mid_usd", r.get("severity_mid_usd")))
        if base is None:
            continue
        pred.append(float(base)); paid.append(amt); ids.add(r["property_id"])
    return pred, paid, ids
