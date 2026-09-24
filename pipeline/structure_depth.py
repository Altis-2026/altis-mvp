"""
structure_depth.py — Depth above the FINISHED FLOOR, not above the dirt.

Satellite flood depth is water surface minus ground. Damage is driven by water
above the first finished floor — that is what depth-damage curves (USACE,
FEMA/HAZUS, the curve in severity.py) are indexed on, and what decides whether
drywall, flooring and contents are wet. A slab house and a house raised on
piers take radically different losses from the same 3 ft of water.

  depth_above_floor = depth_at_grade − first_floor_height

First-floor heights above adjacent grade by foundation type are HAZUS-MH
Flood Technical Manual defaults (FEMA), with a 1σ spread reflecting the
real variation around them. When the foundation is unknown we assume
slab-on-grade (the HAZUS default for unknown pre-FIRM residential) and widen
the uncertainty. An adjuster's observed foundation type (captured in the
property drawer, stored in adjuster_feedback) overrides the assumption and is
labelled as observed.

Grade: when a building footprint is available, the adjacent grade is the mean
ground elevation around the footprint rather than the single geocoded point,
correcting depth by (ground_at_point − ground_at_footprint), clipped to ±0.5 m
because a 10 m DEM cannot resolve more than that honestly.
"""
from __future__ import annotations

import math

# type: (first-floor height above grade ft, 1σ ft, label)
FOUNDATIONS = {
    'slab':       (1.0, 0.5, 'slab-on-grade'),
    'crawlspace': (3.0, 1.0, 'crawlspace'),
    'raised':     (5.0, 2.0, 'raised floor'),
    'piers':      (7.0, 2.5, 'piers / piles'),
    'basement':   (4.0, 1.0, 'main floor over basement'),
}
UNKNOWN = (1.0, 1.5, 'assumed slab-on-grade (HAZUS default; foundation not yet observed)')
FT_PER_M = 3.28084
MAX_GRADE_CORRECTION_M = 0.5


def floor_height(foundation: str | None):
    """(height_ft, sigma_ft, label, observed: bool)."""
    f = (foundation or '').strip().lower()
    if f in FOUNDATIONS:
        h, s, lab = FOUNDATIONS[f]
        return h, s, lab, True
    h, s, lab = UNKNOWN
    return h, s, lab, False


def grade_correction_m(ground_point_m, footprint_ground_m):
    """Ground at the geocode minus mean ground around the footprint (±0.5 m)."""
    if ground_point_m is None or footprint_ground_m is None:
        return 0.0
    if not (math.isfinite(ground_point_m) and math.isfinite(footprint_ground_m)):
        return 0.0
    c = ground_point_m - footprint_ground_m
    return max(-MAX_GRADE_CORRECTION_M, min(MAX_GRADE_CORRECTION_M, c))


def structure_depth(depth_ft: float, depth_ci_ft: float, foundation: str | None = None,
                    grade_corr_m: float = 0.0) -> dict:
    """
    Depth above the finished floor with its uncertainty.
    Returns {'depth_at_structure_ft', 'floor_height_ft', 'depth_above_floor_ft',
             'depth_above_floor_ci_ft', 'foundation', 'foundation_observed',
             'basement_water', 'grade_correction_ft'}.
    """
    d = max(0.0, float(depth_ft or 0.0))
    ci = max(0.0, float(depth_ci_ft or 0.0))
    h, sigma, label, observed = floor_height(foundation)
    corr_ft = grade_corr_m * FT_PER_M
    at_structure = max(0.0, d + corr_ft) if d > 0 else 0.0
    above = at_structure - h
    return {
        'depth_at_structure_ft': round(at_structure, 2),
        'floor_height_ft': h,
        'depth_above_floor_ft': round(above, 2),
        'depth_above_floor_ci_ft': round(math.hypot(ci, sigma), 2),
        'foundation': label,
        'foundation_key': (foundation or '').strip().lower() or None,
        'foundation_observed': observed,
        'basement_water': bool((foundation or '').lower() == 'basement' and at_structure > 0.1),
        'grade_correction_ft': round(corr_ft, 2),
    }


def damage_depth_ft(sd: dict) -> float:
    """Depth to feed a first-floor-indexed damage curve (never negative)."""
    return max(0.0, sd['depth_above_floor_ft'])
