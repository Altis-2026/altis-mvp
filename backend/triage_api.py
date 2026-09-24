"""
triage_api.py — The six insurer workflows as decisions with reasons.

ICEYE's flood-claims post names six places a depth layer changes an insurer's
day. Each function below answers one of them for a single property (or, for
silent claims, a portfolio) from the triage row + intel, and always returns
its reasons. None of them makes the coverage decision; they route, size and
flag so a human decides faster and with the evidence in hand.

  route()         #1 FNOL routing: fast-track / desk / field (+ constraints)
  emergency()     #2 emergency advance eligibility
  habitability()  #3 accommodation planning
  (evidence pack) #4 settlement without a visit: inputs, never the decision
  consistency()   #5 claim-vs-observation consistency (outliers with reasons,
                     deliberately not a fraud score)
  silent()        #6 expected losses with no first notice of loss
"""
from __future__ import annotations

FAST_TRACK_MIN_CONF = 78


def _flags(row):
    return (row.get('intel') or {}).get('flags') or []


def _has(row, code, level=None):
    return any(f['code'] == code and (level is None or f['level'] == level) for f in _flags(row))


def _above_floor(row):
    it = row.get('intel') or {}
    sd, pk = it.get('structure'), it.get('structure_peak')
    best = pk if (pk and sd and pk['depth_above_floor_ft'] > sd['depth_above_floor_ft']) else sd
    return None if best is None else best['depth_above_floor_ft']


def route(row: dict) -> dict:
    ic = row.get('impact_class')
    conf = int(row.get('confidence_score') or 0)
    reasons, constraints = [], []
    acc = ((row.get('intel') or {}).get('access') or {}).get('status')
    if acc == 'isolated':
        constraints.append('No dry road route — schedule remote evidence first; field visit after water recedes.')
    elif acc == 'street_flooded':
        constraints.append('Street flooded at the door — high-clearance vehicle or stage from the nearest dry point.')
    alerts = [f for f in _flags(row) if f['level'] == 'alert']
    if _has(row, 'WIND_WATER', 'alert') and (row.get('intel') or {}).get('peril') == 'concurrent':
        decision = 'field'
        reasons.append('Concurrent wind and flood damage: peril allocation needs an on-site inspection.')
    elif ic == 'Dispatch':
        decision = 'field'
        reasons.append('Major flooding measured at the structure.')
    elif ic == 'Remote-Approve' and not alerts and conf >= FAST_TRACK_MIN_CONF:
        decision = 'fast-track'
        reasons.append(f'Flooding confirmed remotely with {conf}% confidence and no open flags — '
                       f'eligible for desk settlement on documentation.')
    elif ic == 'Remote-Deny' and not alerts:
        decision = 'desk'
        reasons.append('Satellite shows no significant flooding and no flag contradicts it — desk review '
                       'of the claim narrative before any denial.')
    else:
        decision = 'desk'
        reasons.append('Borderline measurements or open flags — experienced desk adjuster review.')
    for f in alerts:
        reasons.append(f"{f['title']}.")
    if row.get('original_class') and row.get('original_class') != ic:
        reasons.append(f"Held back from {row['original_class']} by a flag.")
    return {'decision': decision, 'reasons': reasons, 'constraints': constraints,
            'class': ic, 'confidence': conf}


def habitability(row: dict) -> dict:
    hab = (row.get('intel') or {}).get('habitability')
    if not hab:
        return {'available': False, 'reason': 'No structure-depth estimate for this property.'}
    return {'available': True, **hab}


def emergency(row: dict, per_diem_usd: float | None = None) -> dict:
    above = _above_floor(row)
    hab = (row.get('intel') or {}).get('habitability') or {}
    conf = int(row.get('confidence_score') or 0)
    reasons = []
    eligible = False
    if above is not None and above > 0 and hab.get('displacement'):
        if conf >= 60 or above >= 1.0:
            eligible = True
            reasons.append(f'Water about {above:.1f} ft above the finished floor — the household is displaced.')
        else:
            reasons.append('Water likely above the floor but confidence is low — verify before advancing.')
    elif _has(row, 'TRANSIENT_MISS', 'alert'):
        reasons.append('Satellite missed the peak here; eligibility cannot be ruled out — verify by phone or photo.')
    else:
        reasons.append('No evidence of water above the finished floor.')
    out = {'eligible': eligible, 'reasons': reasons,
           'displacement_days': [hab.get('days_low'), hab.get('days_high')] if hab.get('displacement') else None}
    if eligible and per_diem_usd and hab.get('days_low'):
        out['suggested_advance_usd'] = int(round(min(30, hab['days_low']) * float(per_diem_usd)))
        out['advance_basis'] = f"min(30, {hab['days_low']} planning days) × ${float(per_diem_usd):,.0f} per diem"
    return out


def consistency(row: dict, claimed_peril: str | None = None, claimed_depth_ft: float | None = None,
                claimed_water_in_home: bool | None = None) -> dict:
    """
    How well a claim's statements agree with what was observed. Returns a 0–100
    agreement score plus the specific disagreements. An outlier is a reason to
    look, not an accusation — flags such as TRANSIENT_MISS widen what counts as
    consistent, because the observation itself may have missed the water.
    """
    it = row.get('intel') or {}
    depth = float(row.get('max_depth_ft') or 0)
    ci = float(row.get('depth_ci_ft') or 0)
    peak = (it.get('hydrograph') or {}).get('peak_depth_ft')
    hi = max(depth + ci, (peak or 0) + ci)
    above = _above_floor(row)
    transient = _has(row, 'TRANSIENT_MISS')
    wind = it.get('wind')
    score, notes = 100, []
    if claimed_depth_ft is not None:
        c = float(claimed_depth_ft)
        if c > hi and not transient:
            gap = c - hi
            score -= min(60, int(15 + gap * 10))
            notes.append(f'Claimed {c:.1f} ft exceeds the observed range (up to {hi:.1f} ft).')
        elif c > hi and transient:
            notes.append(f'Claimed {c:.1f} ft exceeds the observed range, but the satellite likely missed the peak.')
    if claimed_water_in_home:
        if (above is None or above <= 0) and not transient and depth <= 0.3 and (peak or 0) <= 0.3:
            score -= 45
            notes.append('Interior water claimed, but no flooding was observed or modelled and no flag suggests a miss.')
        elif above is not None and above <= 0 and depth > 0.3:
            score -= 15
            notes.append('Water observed on the lot but below the finished floor.')
    if claimed_peril == 'wind':
        if not wind:
            score -= 30
            notes.append('Wind claimed, but no tropical cyclone passed this location.')
        elif wind['peak_kt'] < 34:
            score -= 25
            notes.append(f"Wind claimed; estimated peak sustained wind only {wind['peak_kt']:.0f} kt.")
    if claimed_peril == 'flood' and depth <= 0.3 and (peak or 0) <= 0.3 and not transient:
        score -= 30
        notes.append('Flood claimed outside the observed and modelled flood extent.')
    score = max(0, score)
    return {'agreement_score': score, 'outlier': score < 60, 'notes': notes or ['Claim is consistent with the observation.'],
            'basis': 'claim statements vs SAR depth ± uncertainty, routed peak, structure depth, wind; '
                     'not a fraud score'}


def expected_loss(row: dict) -> tuple[bool, float, str]:
    """(expected, severity key, reason) for silent-claim detection."""
    it = row.get('intel') or {}
    above = _above_floor(row)
    ic = row.get('impact_class')
    if above is not None and above > 0:
        return True, 2.0 + above, f'water ≈{above:.1f} ft above the finished floor'
    if ic in ('Dispatch', 'Remote-Approve'):
        return True, 1.5 + float(row.get('max_depth_ft') or 0) / 10, f'{ic}: flooding detected'
    if _has(row, 'TRANSIENT_MISS', 'alert'):
        return True, 1.0, 'transient flooding likely (satellite missed the peak)'
    if it.get('peril') in ('wind', 'wind_possible_water', 'concurrent') and (it.get('wind') or {}).get('peak_kt', 0) >= 64:
        return True, 0.8, 'hurricane-force wind'
    return False, 0.0, ''


def silent(rows: list, fnol_ids) -> dict:
    """Properties that should have a claim but have not reported one."""
    reported = {str(x).strip() for x in (fnol_ids or []) if str(x).strip()}
    out = []
    for r in rows:
        pid = str(r.get('property_id'))
        if pid in reported:
            continue
        exp, key, why = expected_loss(r)
        if exp:
            out.append({'property_id': pid, 'address': r.get('address'), 'policy_number': r.get('policy_number'),
                        'impact_class': r.get('impact_class'), 'priority': round(key, 2), 'reason': why,
                        'latitude': r.get('latitude'), 'longitude': r.get('longitude')})
    out.sort(key=lambda x: -x['priority'])
    expected_total = sum(1 for r in rows if expected_loss(r)[0])
    return {'reported': len(reported), 'expected_losses': expected_total,
            'silent_count': len(out), 'silent': out,
            'note': 'Expected losses without a first notice of loss, highest priority first — for proactive '
                    'outreach (away households, hidden water ingress).'}
