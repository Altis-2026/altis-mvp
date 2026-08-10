"""
severity.py — Claim severity estimation in dollars (Round 7, extended Phase 3).

Turns a detected flood depth + the property's coverage into an estimated claim
RANGE. This is a reserving aid — the number a claims manager can put in a
48-hour reserves report — not an adjuster's line-item estimate, and the API/UI
label it accordingly.

The range endpoints come from the depth uncertainty interval (depth ± CI), so
a depth read off a coarse DEM honestly produces a wide dollar range rather
than a falsely precise point estimate.

WHAT PHASE 3 CHANGED
--------------------
1. MULTIPLE CURVES INSTEAD OF ONE. The curve is now selected from the
   structure's own attributes — occupancy class, storeys, basement presence —
   which Phase 2 supplies from the USACE National Structure Inventory. A
   two-storey home loses a smaller FRACTION of its value to two feet of water
   than a one-storey home; a home with a basement takes damage before water
   reaches grade at all. One generic curve got both wrong in a signed,
   predictable direction. The generic curve remains the fallback whenever the
   attributes are unknown, so nothing regresses outside CONUS.

2. DEPTH ABOVE FIRST FLOOR, NOT ABOVE GROUND. Published depth-damage
   functions are indexed on depth above the first finished floor. The detector
   measures depth above ground. They differ by the foundation height, so using
   one where the other belongs overstates damage on every elevated structure.
   `estimate_claim_range` takes the first-floor depth when it is known and
   says which one it used.

3. STRUCTURE AND CONTENTS SEPARATELY. NFIP settles them as separate
   coverages and carriers reserve them separately, so returning one blended
   number was the wrong shape of answer. Contents damage also rises faster at
   shallow depths and saturates earlier — a different curve, not a fraction of
   the structure number.

4. DURATION ADJUSTMENT, CONSERVATIVELY. Applied only where duration was
   actually measured, capped well below the literature figure the roadmap
   cites, and reported as a separate labelled factor. See SEVERITY_DURATION in
   config.py for why the cited multiplier is not used as-is.

Pure functions, no network/EE — unit-tested directly.
"""
from typing import Optional

try:
    from config import (SEVERITY, SEVERITY_CURVES, SEVERITY_CONTENTS_CURVES,
                        SEVERITY_DURATION)
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import (SEVERITY, SEVERITY_CURVES,
                                 SEVERITY_CONTENTS_CURVES, SEVERITY_DURATION)


def _interpolate(curve, depth_ft: float) -> float:
    """Piecewise-linear interpolation over a curve, clamped at both ends."""
    if depth_ft <= curve[0][0]:
        return curve[0][1]
    if depth_ft >= curve[-1][0]:
        return curve[-1][1]
    for (d0, p0), (d1, p1) in zip(curve, curve[1:]):
        if d0 <= depth_ft <= d1:
            frac = (depth_ft - d0) / (d1 - d0)
            return p0 + frac * (p1 - p0)
    return curve[-1][1]  # pragma: no cover - unreachable given clamps


def depth_damage_pct(depth_ft: float) -> float:
    """
    Percent of structure value damaged at a given depth, GENERIC curve.

    Retained unchanged as the fallback and for backward compatibility. Prefer
    `select_curve_key` + `damage_pct_for` when structure attributes are known.
    """
    return _interpolate(SEVERITY['depth_damage_curve'], depth_ft)


def select_curve_key(occupancy_type=None, num_stories=None,
                     basement_type=None) -> Optional[str]:
    """
    Pick a depth-damage curve from NSI structure attributes.

    Returns a key into SEVERITY_CURVES, or None when the attributes don't
    determine one — in which case the caller must fall back to the generic
    curve rather than guess. Returning None is a real answer here: silently
    defaulting an unknown structure to "single family, one storey, no
    basement" would apply the most damage-sensitive residential curve to, say,
    a warehouse.

    `occupancy_type` is NSI's `occtype`, e.g. 'RES1-2SNB', 'RES2', 'COM1'.
    That string already encodes storeys and basement for RES1, but this also
    accepts explicit `num_stories` / `basement_type` (NSI
    `basementEnclosureCrawlspaceType`-style: 0 = none) which take precedence
    when supplied, since they come straight from the structure record.
    """
    occ = str(occupancy_type or '').strip().upper()

    if occ.startswith('RES2'):
        return 'RES2'
    if occ.startswith('RES3') or occ.startswith('RES4') or occ.startswith('RES5'):
        return 'RES3'
    if occ.startswith('COM') or occ.startswith('IND') or occ.startswith('PUB'):
        return 'COM'

    is_res1 = occ.startswith('RES1')
    if not is_res1 and not occ:
        return None
    if not is_res1:
        return None

    # Storeys: explicit value wins, else parse NSI's '...-1S...' / '...-2S...'.
    stories = None
    try:
        if num_stories is not None:
            stories = int(float(num_stories))
    except (TypeError, ValueError):
        stories = None
    if stories is None:
        if '-1S' in occ:
            stories = 1
        elif '-2S' in occ or '-3S' in occ:
            stories = 2
    if stories is None:
        return None

    # Basement: explicit value wins, else parse NSI's trailing 'NB' / 'WB'.
    basement = None
    if basement_type is not None:
        try:
            code = int(float(basement_type))
            # NSI/NFIP convention: 0 = none; 1/2 = finished/unfinished
            # basement; 3/4 = crawlspace, which is NOT a basement for damage
            # purposes — it doesn't hold finished space.
            basement = code in (1, 2)
        except (TypeError, ValueError):
            basement = None
    if basement is None:
        if occ.endswith('NB'):
            basement = False
        elif occ.endswith('WB'):
            basement = True
    if basement is None:
        return None

    storey_key = '1S' if stories <= 1 else '2S'
    basement_key = 'B' if basement else 'NB'
    return f'RES1-{storey_key}-{basement_key}'


def damage_pct_for(depth_ft: float, curve_key: Optional[str] = None,
                   contents: bool = False) -> float:
    """
    Damage percent at a depth for a specific curve, falling back to generic.

    `depth_ft` is depth above the FIRST FLOOR for the Phase 3 curves. Contents
    curves are selected with `contents=True`.
    """
    if curve_key:
        table = SEVERITY_CONTENTS_CURVES if contents else SEVERITY_CURVES
        curve = table.get(curve_key)
        if curve:
            return _interpolate(curve, depth_ft)
    if contents:
        # No generic contents curve exists; the structure curve is the closest
        # defensible stand-in, and the caller records that it was used.
        return _interpolate(SEVERITY['depth_damage_curve'], depth_ft)
    return _interpolate(SEVERITY['depth_damage_curve'], depth_ft)


def duration_multiplier(duration_days, cfg=SEVERITY_DURATION) -> float:
    """
    Multiplier on structure damage for prolonged inundation.

    Returns 1.0 (no adjustment) when duration is unknown and the config
    requires a measured value — which is the default. The pipeline reports
    duration as None when fewer than two post-window slices had a usable
    scene, and an assumed duration must never move a dollar figure.
    """
    if not cfg.get('enabled'):
        return 1.0
    if duration_days is None:
        return 1.0
    try:
        days = float(duration_days)
    except (TypeError, ValueError):
        return 1.0
    if days < 0:
        return 1.0
    mult = _interpolate(cfg['multipliers'], days)
    return min(mult, cfg['max_multiplier'])


def estimate_claim_range(depth_ft: float, depth_ci_ft: float,
                         coverage_amount: float,
                         contents_coverage: Optional[float] = None,
                         occupancy_type=None, num_stories=None,
                         basement_type=None,
                         depth_above_first_floor_ft: Optional[float] = None,
                         duration_days=None) -> Optional[dict]:
    """
    Estimated claim range in dollars, or None when no estimate is defensible
    (no meaningful depth, or no coverage amount to scale against).

    Returns {'low', 'high', 'mid', 'damage_pct', ...} — low/high from the depth
    uncertainty interval run through the damage curve. Backward compatible:
    called with the original three arguments it behaves exactly as before,
    using the generic curve on depth above ground.

    Phase 3 additions, all optional:
      `depth_above_first_floor_ft` — the depth the curves actually want. When
          supplied it REPLACES depth above ground for the damage lookup, and
          `depth_basis` records which was used.
      `occupancy_type` / `num_stories` / `basement_type` — NSI attributes that
          select a specific curve instead of the generic one.
      `contents_coverage` — when given, contents loss is estimated separately
          on its own curve and returned alongside, never blended in.
      `duration_days` — measured inundation duration for the prolonged-
          submersion adjustment. Ignored when None.
    """
    try:
        depth_ground = float(depth_ft or 0)
        ci = max(0.0, float(depth_ci_ft or 0))
        coverage = float(coverage_amount or 0)
    except (TypeError, ValueError):
        return None

    # The curves are indexed on depth above the first floor. Use it when known.
    depth_basis = 'above_ground'
    depth = depth_ground
    if depth_above_first_floor_ft is not None:
        try:
            depth = float(depth_above_first_floor_ft)
            depth_basis = 'above_first_floor'
        except (TypeError, ValueError):
            depth = depth_ground

    # Gating stays on depth above GROUND: a property with water around it but
    # not yet inside still has a defensible (small) loss estimate, and gating
    # on first-floor depth would silently drop every elevated structure.
    if depth_ground < SEVERITY['min_depth_ft'] or coverage <= 0:
        return None

    curve_key = select_curve_key(occupancy_type, num_stories, basement_type)

    pct_mid = damage_pct_for(depth, curve_key)
    pct_low = damage_pct_for(depth - ci, curve_key)
    pct_high = damage_pct_for(depth + ci, curve_key)

    mult = duration_multiplier(duration_days)
    if mult != 1.0:
        cap = 100.0
        pct_mid = min(pct_mid * mult, cap)
        pct_low = min(pct_low * mult, cap)
        pct_high = min(pct_high * mult, cap)

    out = {
        'low':        int(round(coverage * pct_low / 100.0)),
        'high':       int(round(coverage * pct_high / 100.0)),
        'mid':        int(round(coverage * pct_mid / 100.0)),
        'damage_pct': round(pct_mid, 1),
        # Provenance — an adjuster (or an actuary auditing this) can see which
        # curve produced the number and what depth it was indexed on.
        'curve': curve_key or 'generic',
        'depth_basis': depth_basis,
        'duration_multiplier': round(mult, 2),
    }

    if contents_coverage:
        try:
            c_cov = float(contents_coverage)
        except (TypeError, ValueError):
            c_cov = 0.0
        if c_cov > 0:
            c_mid = damage_pct_for(depth, curve_key, contents=True)
            c_low = damage_pct_for(depth - ci, curve_key, contents=True)
            c_high = damage_pct_for(depth + ci, curve_key, contents=True)
            out.update({
                'contents_low':  int(round(c_cov * c_low / 100.0)),
                'contents_high': int(round(c_cov * c_high / 100.0)),
                'contents_mid':  int(round(c_cov * c_mid / 100.0)),
                'contents_damage_pct': round(c_mid, 1),
                # Total is offered for convenience but the split is the point;
                # consumers should show both.
                'total_mid': int(round(coverage * pct_mid / 100.0
                                       + c_cov * c_mid / 100.0)),
            })

    return out
