"""
flags.py — Adjuster flags: specific, evidenced reasons to look twice.

A triage class answers "what do we do with this claim". A flag answers "what
could make that answer wrong, or expensive". Each flag carries the evidence
that raised it, a severity, and — for a few — a concrete action on the triage
decision. Pure functions over already-fetched inputs; no network.

  WIND_WATER      peril allocation (wind vs flood vs concurrent)
  TRANSIENT_MISS  heavy rain / surge exposure but SAR reads dry — water that
                  drained before the satellite passed. Blocks remote denial.
  PRIOR_WATER     location wet in many historical observations (JRC GSW) —
                  chronic pooling / repeat-loss / pre-existing damage check
  ACCESS          no dry road route to the arterial network — dispatch plan
  FLOOR_CLEAR     water reached the lot but not the finished floor

Severity: 'alert' (changes what you should do), 'caution' (verify before
acting), 'info' (context for the file).
"""
from __future__ import annotations

try:
    from config import INTEL
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import INTEL


def _flag(code, level, title, detail, evidence, action=None):
    return {'code': code, 'level': level, 'title': title, 'detail': detail,
            'evidence': evidence, 'action': action}


# ── Wind vs water ────────────────────────────────────────────────────────────

def wind_vs_water(wind: dict | None, depth_ft: float, above_floor_ft: float | None,
                  water_uncertain: bool = False):
    """
    Peril attribution. Returns (verdict, flag|None). `water_uncertain` means a
    TRANSIENT_MISS alert fired: SAR reads dry but likely missed the water, so
    "no flooding observed" must not be read as "no flooding".
    """
    if not wind:
        return 'no_tc', None
    cfg = INTEL['wind']
    kt = wind['peak_kt']
    water_in = (above_floor_ft if above_floor_ft is not None else depth_ft) > cfg['water_in_ft']
    water_lot = depth_ft > cfg['water_lot_ft']
    strong = kt >= cfg['damaging_kt']
    hurricane = kt >= 64
    ev = {'peak_wind_kt': kt, 'wind_class': wind['category'], 'peak_wind_utc': wind['peak_time'],
          'hours_ge_34kt': wind['hours_ge_34kt'], 'hours_ge_64kt': wind['hours_ge_64kt'],
          'closest_approach_nm': wind['closest_nm'], 'depth_ft': round(depth_ft, 2),
          'depth_above_floor_ft': None if above_floor_ft is None else round(above_floor_ft, 2)}
    if water_in and strong:
        return 'concurrent', _flag(
            'WIND_WATER', 'alert', 'Concurrent wind + flood — allocate perils',
            f"Estimated {kt:.0f} kt sustained wind ({wind['category'].lower()}) AND water above "
            f"the finished floor. Expect a wind/flood allocation dispute; document roof and "
            f"interior water lines separately (anti-concurrent-causation clauses apply).", ev)
    if strong and not water_lot and water_uncertain:
        return 'wind_possible_water', _flag(
            'WIND_WATER', 'alert', 'Hurricane wind + possible undetected flooding',
            f"Estimated {kt:.0f} kt sustained wind ({wind['category'].lower()}). Radar reads the "
            f"lot dry, but the pass came too late to rule out surge/flash flooding (see transient "
            f"flag). Treat as a potential concurrent wind + water loss: inspect for interior "
            f"water lines before allocating to wind.", ev)
    if strong and not water_lot:
        return 'wind', _flag(
            'WIND_WATER', 'alert' if hurricane else 'caution',
            'Wind-driven — no flooding observed',
            f"Estimated {kt:.0f} kt sustained wind but no flood water detected at the structure. "
            f"Damage here is most likely wind/rain-intrusion: route to the wind/HO claim, not flood.",
            ev)
    if strong and water_lot:
        return 'wind_exterior_water', _flag(
            'WIND_WATER', 'caution', 'Strong wind, water on lot below floor',
            f"Estimated {kt:.0f} kt wind; flood water reached the lot but not the finished floor. "
            f"Interior damage is more likely wind-driven.", ev)
    if water_in:
        return 'water', _flag(
            'WIND_WATER', 'info', 'Water-dominant loss',
            f"Flooding above the floor with sub-damaging winds (≈{kt:.0f} kt) — a flood-peril loss.",
            ev)
    return 'neither', None


# ── Transient flood the satellite missed ─────────────────────────────────────

def transient_miss(sar_dry: bool, rain: dict | None, hand_m: float | None,
                   lag_hours: float | None, wind: dict | None = None,
                   ground_asl_m: float | None = None):
    """
    Heavy rain (or surge exposure) + low-lying ground + a SAR pass that came
    hours/days later + SAR reads dry  →  the dry reading is not evidence of
    no loss. Returns a flag or None.
    """
    if not sar_dry:
        return None
    cfg = INTEL['transient']
    reasons = []
    level = None
    rain3 = (rain or {}).get('max_3day_mm')
    low = hand_m is not None and hand_m <= cfg['low_hand_m']
    very_low = hand_m is not None and hand_m <= cfg['very_low_hand_m']
    late = lag_hours is None or lag_hours >= cfg['min_lag_hours']
    if rain3 is not None and late:
        if rain3 >= cfg['extreme_rain_3day_mm'] and low:
            level = 'alert'
            reasons.append(f"{rain3:.0f} mm fell in 3 days (extreme)")
        elif rain3 >= cfg['heavy_rain_3day_mm'] and very_low:
            level = 'caution'
            reasons.append(f"{rain3:.0f} mm fell in 3 days (heavy)")
    surge = (wind and wind['peak_kt'] >= 64 and ground_asl_m is not None
             and ground_asl_m <= cfg['surge_max_ground_m'])
    if surge and late:
        # Very low ground under hurricane wind is an alert; the next band up
        # (surge plausible but less likely to have reached) is a caution.
        surge_alert = ground_asl_m <= cfg['surge_alert_ground_m']
        if surge_alert:
            level = 'alert'
        elif level is None:
            level = 'caution'
        reasons.append(f"hurricane-force wind over ground only {ground_asl_m:.1f} m above sea level "
                       f"(storm-surge exposure)")
    if not level:
        return None
    lag_txt = (f"The first post-event radar pass came {lag_hours / 24:.1f} days after the peak"
               if lag_hours is not None else "The radar pass timing relative to the peak is unknown")
    terrain = (f"the property sits {hand_m:.1f} m above the nearest drainage line"
               if hand_m is not None else 'terrain position unknown')
    return _flag(
        'TRANSIENT_MISS', level, 'Transient flooding likely missed by SAR',
        f"{'; '.join(reasons).capitalize()}, and {terrain}. {lag_txt} — water that drained "
        f"before then is invisible to radar. A dry reading here is not evidence of no loss.",
        {'rain_3day_max_mm': rain3, 'rain_event_total_mm': (rain or {}).get('total_mm'),
         'rain_peak_day': (rain or {}).get('peak_day'), 'hand_m': hand_m,
         'sar_lag_hours': lag_hours, 'ground_asl_m': ground_asl_m,
         'surge_exposed': bool(surge)},
        action='hold_remote_deny')


# ── Pre-existing water ───────────────────────────────────────────────────────

def prior_water(occ_point: float | None, occ_near: float | None, sar_flooded: bool):
    cfg = INTEL['prior_water']
    if occ_point is None and occ_near is None:
        return None
    p = occ_point or 0.0
    near = occ_near or 0.0
    if p < cfg['point_min_pct'] and near < cfg['near_min_pct']:
        return None
    level = 'caution' if (sar_flooded and max(p, near) >= cfg['caution_pct']) else 'info'
    return _flag(
        'PRIOR_WATER', level, 'Historically wet location',
        f"Satellite records show surface water here in {p:.0f}% of observations since 1984 "
        f"({near:.0f}% within {cfg['near_radius_m']:.0f} m). Check prior-loss history and "
        f"pre-existing damage; some detected water may be recurring rather than event-caused.",
        {'jrc_occurrence_at_point_pct': round(p, 1), 'jrc_occurrence_nearby_pct': round(near, 1),
         'dataset': 'JRC Global Surface Water v1.4 (1984–2021)'})


# ── Road access ──────────────────────────────────────────────────────────────

def access(acc: dict | None):
    if not acc or acc.get('status') in (None, 'accessible', 'unknown'):
        return None
    if acc['status'] == 'street_flooded':
        return _flag(
            'ACCESS', 'caution', 'Street flooded at the property',
            f"The nearest road segment carries ≥{acc['threshold_m'] * 100:.0f} cm of water. "
            f"A standard vehicle cannot reach the door; stage from the nearest dry point "
            f"({acc.get('dry_point_m', '—')} m away) or plan a high-clearance visit.",
            acc)
    return _flag(
        'ACCESS', 'alert', 'Cut off — no dry road route',
        f"No road route from this property to the arterial network stays below "
        f"{acc['threshold_m'] * 100:.0f} cm of water. Don't schedule a standard field "
        f"inspection until water recedes; prioritise remote evidence.", acc)


# ── Water below the finished floor ───────────────────────────────────────────

def floor_clear(depth_ft: float, above_floor_ft: float | None, basis: str):
    if above_floor_ft is None or depth_ft <= INTEL['wind']['water_lot_ft'] or above_floor_ft > 0:
        return None
    return _flag(
        'FLOOR_CLEAR', 'info', 'Water stayed below the finished floor',
        f"{depth_ft:.1f} ft of water at grade, but the {basis} puts the finished floor "
        f"{-above_floor_ft:.1f} ft above that water line. Expect exterior/foundation and "
        f"contents-in-crawlspace claims rather than interior finish losses.",
        {'depth_ft': round(depth_ft, 2), 'depth_above_floor_ft': round(above_floor_ft, 2),
         'basis': basis})


LEVEL_RANK = {'alert': 0, 'caution': 1, 'info': 2}


def sort_flags(flags):
    return sorted([f for f in flags if f], key=lambda f: LEVEL_RANK[f['level']])
