"""
wind_field.py — Per-property hurricane wind history from NHC best track.

Wind-vs-water is the most litigated question in US hurricane claims: a
homeowners policy usually covers wind and excludes flood, an NFIP policy the
reverse, and many policies carry anti-concurrent-causation language. The
adjuster needs to know, for this address: how hard did the wind blow, when,
and was the water there at the same time?

Source: HURDAT2 (Landsea & Franklin 2013), the NHC's public-domain best
track — 6-hourly centre fixes with maximum sustained wind (1-min, 10 m, kt),
the radius of maximum wind (RMW, 2021+) and the 34/50/64-kt wind radii in
each compass quadrant (2004+). We vendor the records for the demo storms
(pipeline/data/hurdat2_subset.txt) and can parse any HURDAT2 file.

Wind profile at distance r from the centre, per quadrant:
  r ≤ Rmax : V = Vmax · r / Rmax                  (solid-body inner core)
  r > Rmax : V = Vmax · (Rmax / r)^x               (modified Rankine vortex)
with the decay exponent x fitted, per quadrant and per fix, to the observed
34/50/64-kt radii — so the profile reproduces NHC's own analysed wind extent
rather than a textbook shape. Rmax comes from the best track when present,
otherwise from Willoughby, Darling & Rahn (2006), eq. 7a:
  Rmax[km] = 46.4 · exp(−0.0155 · Vmax[m/s] + 0.0169 · |lat|).

Best-track intensity and radii describe marine exposure. Over land, surface
roughness lowers sustained wind; callers pass `surface_factor` (Altis uses
0.85, a conventional marine→open-terrain reduction for 1-min sustained wind)
and the output is labelled as an open-terrain estimate. Dense urban terrain
is rougher still, so it remains an upper bound there.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

KT_PER_MS = 1.943844
NM_KM = 1.852
_QUAD_BEARING = (45.0, 135.0, 225.0, 315.0)          # NE, SE, SW, NW
DATA_FILE = Path(__file__).resolve().parent / 'data' / 'hurdat2_subset.txt'

# Demo event → HURDAT2 storm id.
EVENT_STORMS = {'harvey': 'AL092017', 'ian': 'AL092022'}


def parse_hurdat2(text: str) -> dict:
    """{storm_id: {'name', 'fixes': [ {time, lat, lon, vmax, pmin, r34, r50, r64, rmw} ]}}"""
    storms = {}
    lines = [ln for ln in text.splitlines() if ln.strip()]
    i = 0
    while i < len(lines):
        head = [p.strip() for p in lines[i].split(',')]
        sid, name, n = head[0], head[1], int(head[2])
        fixes = []
        for ln in lines[i + 1:i + 1 + n]:
            try:
                fixes.append(_parse_fix(ln))
            except (ValueError, IndexError):
                continue          # a malformed record line is skipped, not fatal
        storms[sid] = {'name': name.title(), 'fixes': fixes}
        i += 1 + n
    return storms


_LATLON = re.compile(r'(\d+(?:\.\d+)?)\s*([NS])[\s,]+(\d+(?:\.\d+)?)\s*([EW])')


def _parse_fix(ln: str) -> dict:
    p = [x.strip() for x in ln.split(',')]
    t = datetime.strptime(p[0] + p[1], '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
    m = _LATLON.search(ln)
    if not m:
        raise ValueError('no position')
    lat = float(m.group(1)) * (1 if m.group(2) == 'N' else -1)
    lon = float(m.group(3)) * (1 if m.group(4) == 'E' else -1)
    # Numeric fields after the position: vmax, pmin, 12 radii, [rmw].
    tail = ln[m.end():].split(',')
    nums = []
    for x in tail:
        x = x.strip()
        if x == '':
            continue
        nums.append(int(x) if x != '-999' else None)
    nums += [None] * (15 - len(nums))
    vmax, pmin = nums[0], nums[1]
    rad = [v if v is not None and v >= 0 else 0 for v in nums[2:14]]
    rmw = nums[14] if nums[14] and nums[14] > 0 else None
    return {'time': t, 'status': p[3], 'record': p[2], 'lat': lat, 'lon': lon,
            'vmax': vmax if vmax is not None and vmax >= 0 else None, 'pmin': pmin,
            'r34': rad[0:4], 'r50': rad[4:8], 'r64': rad[8:12], 'rmw': rmw}


def load_storm(storm_id: str) -> dict | None:
    return parse_hurdat2(DATA_FILE.read_text()).get(storm_id)


def willoughby_rmax_nm(vmax_kt: float, lat: float) -> float:
    vms = vmax_kt / KT_PER_MS
    return 46.4 * math.exp(-0.0155 * vms + 0.0169 * abs(lat)) / NM_KM


def _interp_fix(a, b, frac):
    lerp = lambda x, y: x + (y - x) * frac
    out = {'time': a['time'] + (b['time'] - a['time']) * frac,
           'lat': lerp(a['lat'], b['lat']), 'lon': lerp(a['lon'], b['lon']),
           'vmax': lerp(a['vmax'] or 0, b['vmax'] or 0)}
    for k in ('r34', 'r50', 'r64'):
        # A quadrant radius of 0 means "this wind speed not reached"; blend
        # only when both ends report it, otherwise hold the nearer end.
        out[k] = [lerp(x, y) if (x and y) else (x if frac < 0.5 else y)
                  for x, y in zip(a[k], b[k])]
    ra = a['rmw'] or willoughby_rmax_nm(a['vmax'] or 0, a['lat'])
    rb = b['rmw'] or willoughby_rmax_nm(b['vmax'] or 0, b['lat'])
    out['rmw'] = lerp(ra, rb)
    return out


def track_at(fixes: list, step_min: int = 15) -> list:
    out = []
    for a, b in zip(fixes, fixes[1:]):
        span = (b['time'] - a['time']).total_seconds() / 60
        if span <= 0:
            continue
        n = max(1, int(span // step_min))
        for k in range(n):
            out.append(_interp_fix(a, b, k / n))
    if fixes:
        out.append(_interp_fix(fixes[-1], fixes[-1], 0.0))
    return out


def _gc(lat1, lon1, lat2, lon2):
    """Great-circle distance (nm) and initial bearing (deg) from 1 → 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d_km = 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return d_km / NM_KM, (math.degrees(math.atan2(y, x)) + 360) % 360


def _quadrant_weights(bearing: float):
    """Linear blend between the two nearest quadrant centres."""
    b = (bearing - 45.0) % 360.0
    q0 = int(b // 90) % 4
    f = (b % 90) / 90.0
    return q0, (q0 + 1) % 4, f


def _fit_exponent(rmax, vmax, pairs):
    """Least-squares x in V = Vmax (Rmax/r)^x through (r, V) pairs beyond Rmax."""
    pts = [(r, v) for r, v in pairs if r > rmax * 1.05 and v < vmax]
    if not pts:
        return 0.5
    num = sum(math.log(vmax / v) * math.log(r / rmax) for r, v in pts)
    den = sum(math.log(r / rmax) ** 2 for r, v in pts)
    return float(min(1.2, max(0.25, num / den))) if den > 0 else 0.5


def wind_at(fix: dict, lat: float, lon: float) -> float:
    """1-min sustained wind (kt) at a point for one (interpolated) fix."""
    vmax = fix['vmax'] or 0.0
    if vmax <= 0:
        return 0.0
    r, bearing = _gc(fix['lat'], fix['lon'], lat, lon)
    rmax = max(3.0, fix['rmw'] or willoughby_rmax_nm(vmax, fix['lat']))
    q0, q1, f = _quadrant_weights(bearing)

    def v_quad(q):
        pairs = [(fix[k][q], s) for k, s in (('r34', 34.0), ('r50', 50.0), ('r64', 64.0))
                 if fix[k][q] and s < vmax]
        x = _fit_exponent(rmax, vmax, pairs)
        if r <= rmax:
            return vmax * r / rmax
        v = vmax * (rmax / r) ** x
        # Respect NHC's analysed extent: outside the 34-kt radius of this
        # quadrant the wind is, by definition, below 34 kt.
        r34 = fix['r34'][q]
        if r34 and r > r34 and v >= 34.0:
            v = 34.0 * (r34 / r) ** x
        if not any(fix['r34']) and v >= 34.0 and r > 2 * rmax:
            v = min(v, 33.0)
        return v
    return (1 - f) * v_quad(q0) + f * v_quad(q1)


def property_wind_history(storm: dict, lat: float, lon: float, step_min: int = 15,
                          surface_factor: float = 1.0) -> dict:
    """
    Peak sustained wind at a point across the storm's life, plus timing.
    Returns {'peak_kt', 'peak_time', 'onset_34kt', 'end_34kt', 'hours_ge_34kt',
             'hours_ge_64kt', 'closest_nm', 'closest_time', 'category'}.
    """
    series = []
    closest = (1e9, None)
    for fx in track_at(storm['fixes'], step_min):
        v = wind_at(fx, lat, lon) * surface_factor
        series.append((fx['time'], v))
        d, _ = _gc(fx['lat'], fx['lon'], lat, lon)
        if d < closest[0]:
            closest = (d, fx['time'])
    if not series:
        return None
    peak_t, peak_v = max(series, key=lambda s: s[1])
    ge34 = [t for t, v in series if v >= 34]
    hrs = lambda thr: round(sum(1 for _, v in series if v >= thr) * step_min / 60.0, 2)
    return {
        'peak_kt': round(peak_v, 1),
        'peak_time': peak_t.isoformat(),
        'onset_34kt': ge34[0].isoformat() if ge34 else None,
        'end_34kt': ge34[-1].isoformat() if ge34 else None,
        'hours_ge_34kt': hrs(34), 'hours_ge_64kt': hrs(64),
        'closest_nm': round(closest[0], 1),
        'closest_time': closest[1].isoformat() if closest[1] else None,
        'category': wind_category(peak_v),
        'surface_factor': surface_factor,
    }


def wind_category(kt: float) -> str:
    if kt >= 137: return 'Cat 5 hurricane-force'
    if kt >= 113: return 'Cat 4 hurricane-force'
    if kt >= 96:  return 'Cat 3 hurricane-force'
    if kt >= 83:  return 'Cat 2 hurricane-force'
    if kt >= 64:  return 'Cat 1 hurricane-force'
    if kt >= 50:  return 'Strong tropical-storm'
    if kt >= 34:  return 'Tropical-storm'
    return 'Below gale'
