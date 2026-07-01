"""
severity.py — Claim severity estimation in dollars (Round 7).

Turns a detected flood depth + the property's dwelling coverage into an
estimated claim RANGE using a USACE/FEMA-style generic one-story residential
depth-damage curve. This is a reserving aid — the number a claims manager can
put in a 48-hour reserves report — not an adjuster's line-item estimate, and
the API/UI label it accordingly.

The range endpoints come from the depth uncertainty interval (depth ± CI), so
a depth read off a coarse DEM honestly produces a wide dollar range rather
than a falsely precise point estimate.

Pure functions, no network/EE — unit-tested directly.
"""
try:
    from config import SEVERITY
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import SEVERITY


def depth_damage_pct(depth_ft: float) -> float:
    """
    Percent of structure value damaged at a given flood depth (piecewise-linear
    interpolation over the configured curve). Clamped to the curve's ends.
    """
    curve = SEVERITY['depth_damage_curve']
    if depth_ft <= curve[0][0]:
        return curve[0][1]
    if depth_ft >= curve[-1][0]:
        return curve[-1][1]
    for (d0, p0), (d1, p1) in zip(curve, curve[1:]):
        if d0 <= depth_ft <= d1:
            frac = (depth_ft - d0) / (d1 - d0)
            return p0 + frac * (p1 - p0)
    return curve[-1][1]  # pragma: no cover - unreachable given clamps


def estimate_claim_range(depth_ft: float, depth_ci_ft: float,
                         coverage_amount: float) -> dict | None:
    """
    Estimated claim range in dollars, or None when no estimate is defensible
    (no meaningful depth, or no coverage amount to scale against).

    Returns {'low', 'high', 'mid', 'damage_pct'} — low/high from the depth
    uncertainty interval run through the damage curve.
    """
    try:
        depth = float(depth_ft or 0)
        ci = max(0.0, float(depth_ci_ft or 0))
        coverage = float(coverage_amount or 0)
    except (TypeError, ValueError):
        return None

    if depth < SEVERITY['min_depth_ft'] or coverage <= 0:
        return None

    pct_mid = depth_damage_pct(depth)
    pct_low = depth_damage_pct(max(0.0, depth - ci))
    pct_high = depth_damage_pct(depth + ci)

    return {
        'low':        int(round(coverage * pct_low / 100.0)),
        'high':       int(round(coverage * pct_high / 100.0)),
        'mid':        int(round(coverage * pct_mid / 100.0)),
        'damage_pct': round(pct_mid, 1),
    }
