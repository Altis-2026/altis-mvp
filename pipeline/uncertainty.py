"""
uncertainty.py — Per-property depth uncertainty interval (Round 3).

A depth reading of "2.3 ft" with no error bar invites false precision. The
estimate is WSE minus ground elevation, and both come from a DEM with a finite
vertical accuracy; the water surface itself also varies across a neighborhood.
This module turns those two physically-grounded error sources into an honest
±1σ (~68%) interval, so the product can say "2.3 ft ± 0.5 ft" instead of
implying millimetre certainty.

Pure functions — no Earth Engine, no I/O — so they are fully unit-testable and
can be reused by the pipeline and the API alike.
"""
from __future__ import annotations

import math

from config import UNCERTAINTY, SAR

_M_TO_FT = 3.28084


def _dem_sigma_ft(dem_resolution_m, cfg=UNCERTAINTY) -> float:
    """Vertical 1σ of the DEM in feet, looked up by resolution."""
    try:
        res = int(round(float(dem_resolution_m)))
    except (TypeError, ValueError):
        res = None
    rmse_m = cfg['dem_vertical_rmse_m'].get(res, cfg['dem_vertical_rmse_default_m'])
    return rmse_m * _M_TO_FT


def depth_uncertainty_ft(depth_ft, dem_resolution_m, wse_spread_ft=None,
                         cfg=UNCERTAINTY) -> float:
    """
    ±1σ half-width (in feet) of an estimated flood depth.

    Combines, in quadrature:
      - DEM vertical accuracy (dominant term; depends on 3DEP vs SRTM), and
      - water-surface spread: a fraction of the measured neighborhood elevation
        std among flooded pixels, or a depth-proportional fallback when the
        pipeline did not supply a measured spread.

    Returns 0.0 for dry properties (depth <= 0); otherwise at least min_ci_ft.
    """
    try:
        depth_ft = float(depth_ft)
    except (TypeError, ValueError):
        return 0.0
    if depth_ft <= 0.0:
        return 0.0

    sigma_dem = _dem_sigma_ft(dem_resolution_m, cfg)

    if wse_spread_ft is not None and not (isinstance(wse_spread_ft, float)
                                          and math.isnan(wse_spread_ft)):
        sigma_wse = cfg['wse_spread_to_sigma'] * max(0.0, float(wse_spread_ft))
    else:
        sigma_wse = cfg['depth_frac_fallback'] * depth_ft

    sigma = math.sqrt(sigma_dem ** 2 + sigma_wse ** 2)
    ci = max(cfg['min_ci_ft'], cfg['k_sigma'] * sigma)
    return round(ci, 2)


def depth_interval_ft(depth_ft, dem_resolution_m, wse_spread_ft=None,
                      cfg=UNCERTAINTY):
    """
    Return (lower_ft, upper_ft, half_width_ft) for a depth, clamped to
    [0, max_plausible_depth_ft]. Lower is floored at 0 (can't have negative
    depth); upper is capped at the same physical cap the pipeline uses.
    """
    ci = depth_uncertainty_ft(depth_ft, dem_resolution_m, wse_spread_ft, cfg)
    try:
        depth_ft = float(depth_ft)
    except (TypeError, ValueError):
        depth_ft = 0.0
    cap = SAR['max_plausible_depth_ft']
    lower = max(0.0, round(depth_ft - ci, 2))
    upper = min(cap, round(depth_ft + ci, 2))
    return lower, upper, ci


def format_depth_with_interval(depth_ft, dem_resolution_m, wse_spread_ft=None,
                               cfg=UNCERTAINTY) -> str:
    """Human-readable '2.3 ft ± 0.5 ft' (or 'dry' for zero depth)."""
    try:
        d = float(depth_ft)
    except (TypeError, ValueError):
        return "n/a"
    if d <= 0.0:
        return "dry (0 ft)"
    ci = depth_uncertainty_ft(d, dem_resolution_m, wse_spread_ft, cfg)
    return f"{d:.1f} ft ± {ci:.1f} ft"
