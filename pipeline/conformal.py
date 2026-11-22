"""Claim-level band widening from the pre-registered conformal calibration.

q values come from outputs/conformal_q.json (validation/conformal_check.py,
protocol docs/PREREG_CONFORMAL.md). The reserved-storm test did NOT reach the
nominal level on every cell, so the widened band is labelled a typical range,
never a guarantee.
"""
from __future__ import annotations

import json
from pathlib import Path

Q_PATH = Path(__file__).resolve().parent.parent / "outputs" / "conformal_q.json"
CHECK_PATH = Path(__file__).resolve().parent.parent / "outputs" / "conformal_check.json"
LEVEL = "0.8"


def _load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def widen_band(p10, p90, limit, all_terms_known: bool, depth_basis: str, w_floor: float = 1000.0):
    """Return (lo, hi, info) or (p10, p90, None) when no calibration is stored."""
    store = _load(Q_PATH)
    if not store:
        return p10, p90, None
    key = f"{'full' if all_terms_known else 'min'}/{depth_basis}"
    q = (store.get("q") or {}).get(key, {}).get(LEVEL)
    if q is None:
        return p10, p90, None
    w = max(float(p90) - float(p10), float(store.get("w_floor_usd", w_floor)))
    lo = max(0.0, float(p10) - q * w)
    hi = min(float(limit), float(p90) + q * w) if limit else float(p90) + q * w
    check = _load(CHECK_PATH) or {}
    pooled = ((check.get("cells") or {}).get(key) or {}).get("pooled", {})
    return round(lo), round(hi), {
        "nominal": float(LEVEL), "held_out_coverage": pooled.get("conformal_0.8"),
        "calibrated_on": store.get("calibrated_on"), "guaranteed": False,
    }
