"""
intel.py — The per-property intelligence layer (flags, structure depth, wind,
terrain, access, routed hydrograph) on top of the SAR triage result.

One entry point, `compute_intel(props, ctx)`, used two ways:
  * offline, by pipeline/05_build_intel.py, to bake the demo events into
    outputs/{event}_intel.json (served with the event, no network at request
    time), and
  * on demand, by POST /api/portfolio/{pid}/intel/{event_id}, for live
    portfolios (cached in SQLite).

Every stage degrades independently: a source that fails is recorded in
`sources[stage]` with its reason and the dependent flags simply abstain —
the triage result is never blocked on enrichment.

`props`: dicts with property_id, latitude, longitude, max_depth_ft,
depth_ci_ft, pct_flooded (percent 0–100), impact_class.
`ctx`: {event_id, label, bbox [w,s,e,n], post_start, post_end, storm_id?}.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
import traceback
from datetime import date, datetime, timedelta, timezone

import numpy as np

from backend import geodata
from pipeline import access as access_mod
from pipeline import flags as F
from pipeline.config import INTEL
from pipeline.structure_depth import structure_depth, damage_depth_ft
from pipeline import habitability
from pipeline.terrain import Grid, hand
from pipeline.water_surface import observed_depth_grid
from pipeline.wind_field import load_storm, property_wind_history, EVENT_STORMS

FT_PER_M = 3.28084
INTEL_VERSION = '1.0.0'


def _d(s: str) -> date:
    return datetime.strptime(s[:10], '%Y-%m-%d').date()


def _pad(bbox, deg):
    w, s, e, n = bbox
    return [w - deg, s - deg, e + deg, n + deg]


def _stage(sources, name, fn, log):
    t0 = time.time()
    try:
        out = fn()
        sources[name] = {'ok': True, 'seconds': round(time.time() - t0, 1)}
        return out
    except Exception as ex:                       # noqa: BLE001 — record and abstain
        sources[name] = {'ok': False, 'reason': f'{type(ex).__name__}: {ex}'[:300]}
        log(f'  [intel] {name} unavailable: {ex}')
        if not isinstance(ex, geodata.GeoDataError):
            log(traceback.format_exc(limit=3))
        return None


def foundation_overrides(property_ids) -> dict:
    """Latest adjuster-observed first-floor type per property."""
    from backend.database import DB_PATH
    ids = [str(p) for p in property_ids]
    if not ids:
        return {}
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            "SELECT property_id, first_floor_type FROM adjuster_feedback "
            "WHERE first_floor_type IS NOT NULL AND first_floor_type != '' ORDER BY id")
        out = {}
        wanted = set(ids)
        for pid, ff in rows:
            if pid in wanted:
                out[pid] = ff
        return out
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def rain_summary(series_vals: list[tuple[date, float]], end_hour: int = 24) -> dict | None:
    vals = [(d, v) for d, v in series_vals if v is not None and math.isfinite(v)]
    if not vals:
        return None
    days = [d for d, _ in vals]
    mm = np.array([v for _, v in vals])
    roll = np.convolve(mm, np.ones(3), mode='valid') if len(mm) >= 3 else np.array([mm.sum()])
    k = int(np.argmax(roll))
    window = days[k:k + 3] if len(mm) >= 3 else days
    peak_day = window[int(np.argmax(mm[k:k + 3]))] if len(mm) >= 3 else days[int(np.argmax(mm))]
    peak_end = datetime(peak_day.year, peak_day.month, peak_day.day, tzinfo=timezone.utc) + \
        timedelta(hours=end_hour)
    return {'total_mm': round(float(mm.sum()), 1), 'max_3day_mm': round(float(roll.max()), 1),
            'max_day_mm': round(float(mm.max()), 1), 'peak_day': peak_day.isoformat(),
            'peak_end_utc': peak_end.isoformat(),
            'window_end_day': window[-1].isoformat(),
            'daily': [[d.isoformat(), round(float(v), 1)] for d, v in vals]}


def compute_intel(props: list, ctx: dict, session=None, log=print, run_router: bool = True,
                  router_opts: dict | None = None) -> dict:
    t_start = time.time()
    sources = {}
    products = {}
    bbox = ctx['bbox']
    pids = [str(p['property_id']) for p in props]
    lons = np.array([float(p['longitude']) for p in props])
    lats = np.array([float(p['latitude']) for p in props])
    depth_ft = np.array([float(p.get('max_depth_ft') or 0.0) for p in props])
    ci_ft = np.array([float(p.get('depth_ci_ft') or 0.0) for p in props])
    pct = np.array([float(p.get('pct_flooded') or 0.0) for p in props])
    sar_flooded = (depth_ft > 0.3) | (pct >= 10.0)
    sar_dry = (depth_ft <= 0.3) & (pct < 5.0)
    post_start, post_end = _d(ctx['post_start']), _d(ctx['post_end'])

    # ── Terrain: ground elevation + HAND ────────────────────────────────
    def terrain():
        dem = geodata.dem_at_resolution(_pad(bbox, 0.03), INTEL['dem_res_m'], session)
        h, acc, stream = hand(dem.data, dem.cell_m, INTEL['hand_stream_km2'])
        return dem, Grid(h, dem.west, dem.north, dem.dx, dem.dy)
    tr = _stage(sources, 'terrain', terrain, log)
    dem, hand_g = tr if tr else (None, None)
    ground = dem.sample(lons, lats) if dem else np.full(len(props), np.nan)
    hand_m = hand_g.sample(lons, lats) if hand_g else np.full(len(props), np.nan)

    # ── JRC surface-water history ───────────────────────────────────────
    def jrc():
        from scipy.ndimage import maximum_filter
        g = geodata.jrc_occurrence_grid(_pad(bbox, 0.005), session)
        px_m = g.cell_m[0]
        k = max(1, int(round(INTEL['prior_water']['near_radius_m'] / px_m)))
        near = Grid(maximum_filter(np.nan_to_num(g.data), size=2 * k + 1), g.west, g.north, g.dx, g.dy)
        return g.sample(lons, lats, 'nearest'), near.sample(lons, lats, 'nearest')
    jr = _stage(sources, 'jrc', jrc, log)
    occ_pt, occ_near = jr if jr else (np.full(len(props), np.nan),) * 2

    # ── Rainfall (CHIRPS) ───────────────────────────────────────────────
    look = INTEL['transient']['rain_lookback_days']
    rain_start = post_start - timedelta(days=look)
    rain_end = min(post_end, post_start + timedelta(days=3))

    us = all(geodata.in_stageiv_domain(lo, la) for lo, la in zip(lons, lats))

    def rain():
        days = [rain_start + timedelta(days=k) for k in range((rain_end - rain_start).days + 1)]
        if us:
            # Stage IV day D covers 12 UTC D−1 → 12 UTC D.
            cols = [geodata.stageiv_day_points(d, lons, lats, session) for d in days]
            product, end_hour = 'NOAA/NWS Stage IV multi-sensor QPE (radar + gauge, ~4 km)', 12
        else:
            grids = geodata.chirps_series(_pad(bbox, 0.06), rain_start, rain_end, session)
            cols = [g.sample(lons, lats, 'nearest') for _, g in grids]
            product, end_hour = 'CHIRPS v2.0 daily (0.05°)', 24
        products['rain'] = product
        mat = np.column_stack(cols)
        return [rain_summary(list(zip(days, mat[i].tolist())), end_hour) for i in range(len(props))]
    rain_per = _stage(sources, 'rain', rain, log) or [None] * len(props)

    # ── Sentinel-1 pass times ───────────────────────────────────────────
    def passes():
        return geodata.s1_acquisitions(bbox, (post_start - timedelta(days=2)).isoformat(),
                                       post_end.isoformat(), session)
    s1 = _stage(sources, 's1_passes', passes, log) or []
    s1_times = [geodata.parse_iso(p['datetime']) for p in s1 if p.get('datetime')]

    def first_pass_after(t: datetime):
        after = [x for x in s1_times if x >= t and x.date() >= post_start - timedelta(days=1)]
        return min(after) if after else None

    # ── Hurricane wind ──────────────────────────────────────────────────
    storm_id = ctx.get('storm_id') or EVENT_STORMS.get(ctx.get('event_id'))
    wind_per = [None] * len(props)
    storm = None
    found = None
    if not storm_id and ctx.get('detect_storm', True):
        found = _stage(sources, 'storm_search', lambda: geodata.find_tropical_cyclone(
            bbox, post_start - timedelta(days=4), post_end, session), log)
        if found:
            storm_id = found['id']
    if storm_id:
        def wind():
            st = found['storm'] if found else load_storm(storm_id)
            if st is None:
                raise geodata.GeoDataError(f'storm {storm_id} not in best-track file')
            w, s, e, n = bbox
            gx = np.linspace(w, e, max(2, int((e - w) / 0.02) + 1))
            gy = np.linspace(s, n, max(2, int((n - s) / 0.02) + 1))
            # Only fixes within ~600 nm matter; trimming keeps this fast.
            cx, cy = (w + e) / 2, (s + n) / 2
            near = [f for f in st['fixes'] if abs(f['lat'] - cy) < 10 and abs(f['lon'] - cx) < 12]
            trimmed = {'name': st['name'], 'fixes': near}
            lf = INTEL['wind']['land_factor']
            products['wind'] = (f"NHC HURDAT2 best track, modified-Rankine profile fitted to the "
                                f"34/50/64-kt radii; ×{lf} marine→open-terrain surface factor")
            grid = [[property_wind_history(trimmed, y, x, surface_factor=lf) for x in gx] for y in gy]
            peak = np.array([[c['peak_kt'] for c in row] for row in grid])
            pg = Grid(peak[::-1], w - (gx[1] - gx[0]) / 2, n + (gy[1] - gy[0]) / 2,
                      gx[1] - gx[0], gy[1] - gy[0])
            out = []
            for i in range(len(props)):
                iy = int(np.argmin(np.abs(gy - lats[i])))
                ix = int(np.argmin(np.abs(gx - lons[i])))
                cell = dict(grid[iy][ix])
                cell['peak_kt'] = round(float(pg.sample([lons[i]], [lats[i]])[0]), 1)
                from pipeline.wind_field import wind_category
                cell['category'] = wind_category(cell['peak_kt'])
                out.append(cell)
            return st, out
        wr = _stage(sources, 'wind', wind, log)
        if wr:
            storm, wind_per = wr

    # ── Observed water surface (from SAR points) ────────────────────────
    obs_grid = None
    if dem is not None:
        obs_grid = _stage(sources, 'observed_surface', lambda: observed_depth_grid(
            dem, lons, lats, depth_ft / FT_PER_M), log)

    # ── Hydraulic router (Phase C) ──────────────────────────────────────
    routed = None
    if run_router and dem is not None and obs_grid is not None:
        from backend.routing import route_event
        routed = _stage(sources, 'router', lambda: route_event(
            props, ctx, dem, obs_grid, s1_times, rain_start, rain_end, session=session,
            log=log, **(router_opts or {})), log)

    # ── Road access ─────────────────────────────────────────────────────
    acc_res = {}
    if obs_grid is not None:
        def roads():
            ways = geodata.osm_roads(_pad(bbox, 0.01), session)
            depth_src = routed['peak_depth_grid'] if routed and routed.get('peak_depth_grid') else obs_grid
            fn = lambda lo, la: depth_src.sample(lo, la)
            pts = [{'property_id': pid, 'lon': lo, 'lat': la} for pid, lo, la in zip(pids, lons, lats)]
            res = access_mod.assess(ways, fn, pts, _pad(bbox, 0.01),
                                    INTEL['access']['impassable_m'], INTEL['access']['snap_m'])
            for r in res.values():
                r['depth_basis'] = 'routed peak' if depth_src is not obs_grid else 'SAR observation'
            return res
        acc_res = _stage(sources, 'roads', roads, log) or {}

    # ── Per-property assembly ───────────────────────────────────────────
    overrides = foundation_overrides(pids)
    out_props = {}
    counts = {'flags': {}, 'held_remote_deny': 0, 'peril': {}}
    for i, p in enumerate(props):
        pid = pids[i]
        rt = (routed or {}).get('properties', {}).get(pid)
        d_ft = float(depth_ft[i])
        d_ci = float(ci_ft[i])
        peak_ft = rt['peak_depth_ft'] if rt else None
        sd = structure_depth(d_ft, d_ci, overrides.get(pid))
        sd_peak = structure_depth(peak_ft, d_ci, overrides.get(pid)) if peak_ft is not None else None
        worst_sd = sd_peak if (sd_peak and sd_peak['depth_above_floor_ft'] > sd['depth_above_floor_ft']) else sd
        w = wind_per[i]
        rain_i = rain_per[i]
        # Lag: flood peak (end of the heaviest-rain day, or peak wind for a
        # hurricane) → first radar pass.
        peak_t = None
        if rain_i and rain_i.get('max_3day_mm', 0) >= INTEL['transient']['heavy_rain_3day_mm']:
            peak_t = datetime.fromisoformat(rain_i['peak_end_utc'])
        if w and w['peak_kt'] >= 64:
            wt = datetime.fromisoformat(w['peak_time'])
            # Latest plausible peak → the shortest (most conservative) lag.
            peak_t = wt if peak_t is None else max(peak_t, wt)
        lag_h = None
        if peak_t is not None and s1_times:
            fp = first_pass_after(peak_t)
            lag_h = round((fp - peak_t).total_seconds() / 3600, 1) if fp else None
        g_asl = float(ground[i]) if np.isfinite(ground[i]) else None
        h_m = float(hand_m[i]) if np.isfinite(hand_m[i]) else None

        f_transient = F.transient_miss(bool(sar_dry[i]) and not (peak_ft and peak_ft > 0.3), rain_i,
                                       h_m, lag_h, w, g_asl)
        wet_any = d_ft > 0.1 or (peak_ft or 0) > 0.1
        verdict, f_wind = F.wind_vs_water(w, max(d_ft, peak_ft or 0.0),
                                          worst_sd['depth_above_floor_ft'] if wet_any else None,
                                          water_uncertain=bool(f_transient and f_transient['level'] == 'alert'))
        f_list = [
            f_wind,
            f_transient,
            F.prior_water(float(occ_pt[i]) if np.isfinite(occ_pt[i]) else None,
                          float(occ_near[i]) if np.isfinite(occ_near[i]) else None,
                          bool(sar_flooded[i])),
            F.access(acc_res.get(pid)),
            F.floor_clear(max(d_ft, peak_ft or 0.0), worst_sd['depth_above_floor_ft'],
                          worst_sd['foundation']),
        ]
        flags = F.sort_flags(f_list)
        override = None
        ic = p.get('impact_class')
        if (INTEL['hold_remote_deny'] and ic == 'Remote-Deny'
                and any(f['action'] == 'hold_remote_deny' and f['level'] == 'alert' for f in flags)):
            override = {'from': 'Remote-Deny', 'to': 'Review',
                        'reason': 'Held from remote denial: transient flooding likely missed by SAR.'}
            counts['held_remote_deny'] += 1
        for f in flags:
            counts['flags'][f['code']] = counts['flags'].get(f['code'], 0) + 1
        hours_above = None
        if rt:
            hourly = rt.pop('_series_ft_hourly', None) or []
            fh = sd['floor_height_ft']
            hours_above = float(sum(1 for v in hourly if v > fh))
            rt['hours_above_floor'] = hours_above
        hab = habitability.estimate(worst_sd['depth_above_floor_ft'] if wet_any else None, hours_above)
        if hab and hab.get('displacement'):
            counts['displaced'] = counts.get('displaced', 0) + 1
        counts['peril'][verdict] = counts['peril'].get(verdict, 0) + 1
        out_props[pid] = {
            'flags': flags,
            'class_override': override,
            'structure': sd,
            'structure_peak': sd_peak,
            'damage_depth_ft': round(damage_depth_ft(worst_sd), 2),
            'peril': verdict,
            'wind': w,
            'rain': ({k: v for k, v in rain_i.items() if k != 'daily'} if rain_i else None),
            'terrain': {'ground_asl_m': None if g_asl is None else round(g_asl, 2),
                        'hand_m': None if h_m is None else round(h_m, 2)},
            'jrc_occurrence_pct': None if not np.isfinite(occ_pt[i]) else round(float(occ_pt[i]), 1),
            'access': acc_res.get(pid),
            'sar_lag_hours': lag_h,
            'hydrograph': rt,
            'habitability': hab,
        }

    event_rain = None
    if rain_per and any(rain_per):
        valid = [r for r in rain_per if r]
        mid = valid[len(valid) // 2]
        event_rain = {'daily_median_property': mid.get('daily'),
                      'max_3day_mm_max': max(r['max_3day_mm'] for r in valid),
                      'max_3day_mm_median': float(np.median([r['max_3day_mm'] for r in valid]))}
    return json_safe({
        'version': INTEL_VERSION,
        'event_id': ctx.get('event_id'),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'seconds': round(time.time() - t_start, 1),
        'sources': sources,
        'products': products,
        'event': {
            'sar_passes': [p['datetime'] for p in s1],
            'storm': ({'id': storm_id, 'name': storm['name']} if storm else None),
            'rain': event_rain,
            'router': ({k: v for k, v in routed.items() if k not in ('properties', 'peak_depth_grid')}
                       if routed else None),
        },
        'summary': counts,
        'properties': out_props,
    })


def json_safe(obj):
    """Recursively replace NaN/±inf with None (browsers reject NaN in JSON)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        return json_safe(obj.item())
    return obj


def _router_timing(intel: dict):
    router = ((intel or {}).get('event') or {}).get('router') or {}
    times = router.get('times') or []
    t0 = times[0] if times else None
    step_h = None
    if len(times) >= 2:
        step_h = (datetime.fromisoformat(times[1]) - datetime.fromisoformat(times[0])).total_seconds() / 3600
    return t0, step_h, (router.get('skill_at_pass') or {}).get('csi')


def client_property(it: dict, timing) -> dict:
    """One property's intel with router timing/skill folded into its hydrograph."""
    it = dict(it)
    hy = it.get('hydrograph')
    if hy:
        t0, step_h, csi = timing
        hy = dict(hy)
        hy.setdefault('t0', t0)
        hy.setdefault('step_h', step_h)
        hy.setdefault('skill_csi', csi)
        it['hydrograph'] = hy
    return it


def client_payload(intel: dict) -> dict:
    """Intel shaped for the browser: per-property hydrographs self-describing."""
    timing = _router_timing(intel)
    out = {k: v for k, v in intel.items() if k != 'properties'}
    out['properties'] = {pid: client_property(it, timing)
                         for pid, it in (intel.get('properties') or {}).items()}
    return out


def merge_intel_rows(rows: list, intel: dict, colors: dict) -> list:
    """
    Attach each property's intel to its row and apply any held-back class
    change (keeping `original_class`).
    """
    by = (intel or {}).get('properties', {})
    timing = _router_timing(intel)
    for r in rows:
        it = by.get(str(r.get('property_id')))
        if not it:
            continue
        it = client_property(it, timing)
        r['intel'] = it
        ov = it.get('class_override')
        if ov and r.get('impact_class') == ov['from']:
            r['original_class'] = ov['from']
            r['impact_class'] = ov['to']
            r['recommended_action'] = ov['reason']
            r['color'] = colors.get(ov['to'], r.get('color'))
    return rows


def inherit_from_event(portfolio_rows: list, event_rows: list, event_intel: dict,
                       max_km: float = 2.0) -> dict:
    """
    Portfolio intel for a pre-baked event: each property takes the intel of the
    nearest analysed event parcel within max_km — the same nearest-match rule
    the portfolio's triage class was inherited by.
    """
    from scipy.spatial import cKDTree
    ev = [r for r in event_rows if r.get('latitude') is not None]
    tree = cKDTree([[r['latitude'], r['longitude'] * math.cos(math.radians(r['latitude']))]
                    for r in ev])
    by = event_intel.get('properties', {})
    props = {}
    for p in portfolio_rows:
        if p.get('latitude') is None or p.get('longitude') is None:
            continue
        d, i = tree.query([p['latitude'], p['longitude'] * math.cos(math.radians(p['latitude']))])
        if d * 111.0 > max_km:
            continue
        src = by.get(str(ev[i]['property_id']))
        if src:
            it = dict(src)
            it['inherited_from'] = {'property_id': str(ev[i]['property_id']),
                                    'distance_m': int(round(d * 111000))}
            props[str(p['property_id'])] = it
    out = {k: v for k, v in event_intel.items() if k != 'properties'}
    out['properties'] = props
    out['inherited'] = True
    return out
