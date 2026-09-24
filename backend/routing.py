"""
routing.py — Synthetic revisit: the hydraulic router, calibrated to the SAR pass.

    route_event(props, ctx, dem, obs_grid, s1_times, rain_start, rain_end)

1. Domain. The study box padded by ROUTER['pad_deg'] so the router sees the
   contributing catchment, not just the neighbourhood.
2. Terrain. AWS Terrain Tiles block-reduced to the router grid; cells on the
   drainage network (≥ channel_area_km2 upstream) take their block MINIMUM so
   rivers stay connected at coarse resolution ("channel burning"), everywhere
   else the block mean.
3. Forcing. Observed daily rainfall (Stage IV in the US, CHIRPS elsewhere)
   on the grid, spread evenly through each day, times a forcing multiplier k
   that absorbs infiltration losses, rainfall bias and any upstream area
   outside the domain.
4. Calibration. k is chosen to maximise the Critical Success Index
   CSI = hits / (hits + misses + false alarms) between simulated and
   SAR-observed wet/dry at the property points at the pass time — the
   standard flood-extent fit statistic (Bates & De Roo 2000). Grid search,
   then golden-section refinement.
5. Anchoring. At each property the SAR depth at the pass is ground truth.
   The router supplies the *shape* of the hydrograph; its level is shifted so
   the curve passes through the observation:
       depth(t) = max(0, sim(t) − sim(t_pass) + obs)
   so peak depth = observed + how much higher the water stood at the peak.
6. Report. CSI, hits/misses/false alarms and depth RMSE at the pass are
   returned with every result and shown next to every routed number. The
   language is "hydraulically consistent with the observation", never "more
   accurate": we have not validated per-property depth against ground truth.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from backend import geodata
from pipeline.config import ROUTER
from pipeline.hydraulic_route import route
from pipeline.terrain import (Grid, d4_conditioned, drainage, flow_accumulation, mosaic_tiles,
                              zoom_for_resolution)

FT_PER_M = 3.28084


class NotCalibratable(geodata.GeoDataError):
    pass


def _router_dem(domain, res_m, session=None):
    """(z Grid, channel mask) at res_m, with channel burning."""
    w, s, e, n = domain
    lat = (s + n) / 2
    z = zoom_for_resolution(40.0, lat)
    base = mosaic_tiles(domain, z, lambda zz, xx, yy: geodata.terrarium_tile(zz, xx, yy, session))
    rx = res_m / (111320.0 * math.cos(math.radians(lat)))
    ry = res_m / 110540.0
    fx = max(1, int(round(rx / base.dx)))
    fy = max(1, int(round(ry / base.dy)))
    d = base.data
    H, W = (d.shape[0] // fy) * fy, (d.shape[1] // fx) * fx
    blk = d[:H, :W].reshape(H // fy, fy, W // fx, fx)
    zmean = np.nanmean(blk, axis=(1, 3))
    zmin = np.nanmin(blk, axis=(1, 3))
    g = Grid(zmean, base.west, base.north, base.dx * fx, base.dy * fy)
    _, parent, order = drainage(zmean)
    acc_km2 = flow_accumulation(parent, order).reshape(zmean.shape) * \
        (g.cell_m[0] * g.cell_m[1] / 1e6)
    channel = acc_km2 >= ROUTER['channel_area_km2']
    # A real channel is tens of metres wide, not a whole coarse cell: lower
    # channel cells toward their block minimum by at most burn_max_m, so the
    # network stays connected without carving cell-wide trenches that would
    # swallow the flood.
    burn = np.minimum(zmean - zmin, ROUTER['burn_max_m'])
    zb = np.where(channel, zmean - burn, zmean)
    # Remove closed pits (DEM artefacts at this scale) and breach diagonal-only
    # valleys so rain can always drain across the router's orthogonal faces.
    sea = sea_mask(zb)
    zb = np.where(sea, zb, d4_conditioned(zb, outlets=sea))
    return Grid(zb, g.west, g.north, g.dx, g.dy), channel


def sea_mask(z: np.ndarray) -> np.ndarray:
    """Open ocean: below-sea-level cells connected to the domain edge."""
    from scipy.ndimage import label
    below = np.nan_to_num(z, nan=1.0) < 0.0
    lab, _ = label(below)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    return np.isin(lab, list(edge)) if edge else np.zeros_like(below)


def _rain_stack(domain, grid: Grid, day0, ndays, us: bool, session=None):
    """(ndays, h, w) mm/day on the router grid, and the UTC start of day 0."""
    rows, cols = np.mgrid[0:grid.shape[0], 0:grid.shape[1]]
    lon, lat = grid.lonlat(rows.ravel(), cols.ravel())
    stack = []
    days = [day0 + timedelta(days=k) for k in range(ndays)]
    if us:
        # Stage IV day D spans 12 UTC D−1 → 12 UTC D; sample on a thinned
        # set of points then fill the grid (the product is ~4 km anyway).
        step = max(1, int(round(4000 / grid.cell_m[0])))
        sub = (rows.ravel() % step == 0) & (cols.ravel() % step == 0)
        from scipy.interpolate import NearestNDInterpolator
        for d in days:
            vals = geodata.stageiv_day_points(d, lon[sub], lat[sub], session)
            ok = np.isfinite(vals)
            f = NearestNDInterpolator(np.column_stack([lon[sub][ok], lat[sub][ok]]), vals[ok])
            stack.append(f(lon, lat).reshape(grid.shape))
        t0 = datetime(day0.year, day0.month, day0.day, tzinfo=timezone.utc) - timedelta(hours=12)
    else:
        for d in days:
            g = geodata.chirps_day(d, [domain[0] - 0.06, domain[1] - 0.06,
                                       domain[2] + 0.06, domain[3] + 0.06], session)
            v = g.sample(lon, lat, 'nearest').reshape(grid.shape)
            stack.append(np.nan_to_num(v, nan=0.0))
        t0 = datetime(day0.year, day0.month, day0.day, tzinfo=timezone.utc)
    return np.maximum(0.0, np.array(stack)), t0


def _obs_points(props):
    depth_ft = np.array([float(p.get('max_depth_ft') or 0) for p in props])
    pct = np.array([float(p.get('pct_flooded') or 0) for p in props])
    wet = depth_ft >= ROUTER['obs_wet_ft']
    dry = (depth_ft <= 0.05) & (pct < 5.0)
    return depth_ft, wet, dry


def _cells(grid: Grid, lons, lats):
    r, c = grid.rc(lons, lats)
    r = np.clip(np.rint(r).astype(int), 0, grid.shape[0] - 1)
    c = np.clip(np.rint(c).astype(int), 0, grid.shape[1] - 1)
    return r, c


def _run(zg: Grid, channel, rain, t0, t_end_s, k, record=None, t_pass_s=None):
    dx, dy = zg.cell_m
    n = np.where(channel, ROUTER['manning_channel'], ROUTER['manning_floodplain'])
    ndays = rain.shape[0]
    sea = sea_mask(zg.data)
    rate = rain / 1000.0 / 86400.0 * k * (~sea)      # rain on the open sea is not flood water

    def rain_rate(t):
        i = int(t // 86400)
        return rate[i] if 0 <= i < ndays else 0.0
    return route(zg.data, dx, dy, t_end_s, rain_rate=rain_rate, manning=n,
                 record_every_s=ROUTER['record_every_h'] * 3600, record_cells=record,
                 cfl=ROUTER['cfl'], dt_max=120.0, snapshot_at_s=t_pass_s,
                 sea_mask=sea)


def _skill(sim_depth_m, depth_ft, wet, dry):
    sim_wet = sim_depth_m >= ROUTER['wet_sim_m']
    hits = int((sim_wet & wet).sum())
    misses = int((~sim_wet & wet).sum())
    fa = int((sim_wet & dry).sum())
    csi = hits / max(1, hits + misses + fa)
    both = sim_wet & wet
    rmse = float(np.sqrt(np.mean((sim_depth_m[both] * FT_PER_M - depth_ft[both]) ** 2))) if both.any() else None
    return {'csi': round(csi, 3), 'hits': hits, 'misses': misses, 'false_alarms': fa,
            'correct_dry': int((~sim_wet & dry).sum()),
            'depth_rmse_ft': None if rmse is None else round(rmse, 2)}


def route_event(props, ctx, dem, obs_grid, s1_times, rain_start, rain_end, session=None,
                log=print, res_calib_m=None, res_final_m=None):
    t_wall = time.time()
    depth_ft, wet, dry = _obs_points(props)
    if wet.sum() < ROUTER['min_wet_obs']:
        raise NotCalibratable(
            f'only {int(wet.sum())} SAR-flooded properties (need ≥ {ROUTER["min_wet_obs"]}) — '
            f'nothing to calibrate the router against; routed depths are not produced')
    post_start = datetime.strptime(ctx['post_start'][:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    passes = sorted(t for t in s1_times if t >= post_start - timedelta(hours=12))
    if not passes:
        raise NotCalibratable('no Sentinel-1 pass time found in the post-event window')
    t_pass = passes[0]
    w, s, e, n = ctx['bbox']
    pad = ROUTER['pad_deg']
    domain = [w - pad, s - pad, e + pad, n + pad]
    lons = np.array([float(p['longitude']) for p in props])
    lats = np.array([float(p['latitude']) for p in props])
    us = all(geodata.in_stageiv_domain(lo, la) for lo, la in zip(lons, lats))
    day0 = post_start.date() - timedelta(days=ROUTER['spinup_days'])
    t_end = t_pass + timedelta(hours=ROUTER['after_pass_h'])
    ndays = (t_end.date() - day0).days + 2

    # ── Calibration on the coarse grid ─────────────────────────────────
    rc_m = res_calib_m or ROUTER['res_calib_m']
    zc, chc = _router_dem(domain, rc_m, session)
    rain_c, t0 = _rain_stack(domain, zc, day0, ndays, us, session)
    t_pass_s = (t_pass - t0).total_seconds()
    t_end_s = (t_end - t0).total_seconds()
    rr, cc = _cells(zc, lons, lats)
    trials = {}

    def evaluate(k):
        k = round(float(k), 3)
        if k in trials:
            return trials[k]['csi']
        res = _run(zc, chc, rain_c, t0, t_pass_s, k)
        sk = _skill(res['h'][rr, cc], depth_ft, wet, dry)
        trials[k] = sk
        log(f'    router k={k:.3f}: CSI {sk["csi"]:.3f} (hits {sk["hits"]}, miss {sk["misses"]}, '
            f'FA {sk["false_alarms"]}) {res["steps"]} steps')
        return sk['csi']
    for k in ROUTER['k_grid']:
        evaluate(k)
    ks = sorted(trials)
    best = max(ks, key=lambda k: trials[k]['csi'])
    i = ks.index(best)
    lo, hi = ks[max(0, i - 1)], ks[min(len(ks) - 1, i + 1)]
    phi = (math.sqrt(5) - 1) / 2
    for _ in range(ROUTER['k_refine']):
        a = hi - phi * (hi - lo)
        b = lo + phi * (hi - lo)
        if evaluate(a) >= evaluate(b):
            hi = b
        else:
            lo = a
    # Highest CSI; among near-ties (within 0.01) prefer the smaller depth
    # error, then the forcing closest to observed rainfall (k = 1).
    top = max(t['csi'] for t in trials.values())
    near = [k for k in trials if trials[k]['csi'] >= top - 0.01]
    best = min(near, key=lambda k: ((trials[k]['depth_rmse_ft'] or 1e9), abs(k - 1.0)))

    # ── Final run on the reporting grid ────────────────────────────────
    rf_m = res_final_m or ROUTER['res_final_m']
    zf, chf = _router_dem(domain, rf_m, session)
    rain_f, t0f = _rain_stack(domain, zf, day0, ndays, us, session)
    t_pass_sf = (t_pass - t0f).total_seconds()
    t_end_sf = (t_end - t0f).total_seconds()
    rf, cf = _cells(zf, lons, lats)
    res = _run(zf, chf, rain_f, t0f, t_end_sf, best, record=(rf, cf), t_pass_s=t_pass_sf)
    times = res['times']
    series = res['series']                        # T × N metres
    ip = int(np.argmin(np.abs(times - t_pass_sf)))
    sim_pass = series[ip]
    final_skill = _skill(sim_pass, depth_ft, wet, dry)

    # ── Anchor each property to its SAR observation ────────────────────
    stamps = [(t0f + timedelta(seconds=float(t))).isoformat() for t in times]
    step = max(1, int(round(3 / ROUTER['record_every_h'])))   # report 3-hourly
    out = {}
    obs_m = depth_ft / FT_PER_M
    sim_wet_pass = sim_pass >= ROUTER['wet_sim_m']
    # Regional hydrograph shape: the median rise/fall relative to the pass
    # across properties where router and SAR agree it was wet. Floodplain
    # water surfaces move coherently, so this is the honest fallback where
    # the coarse router misses a wet point the satellite saw.
    agree = wet & sim_wet_pass
    regional = (np.median(series[:, agree] - sim_pass[agree], axis=1)
                if agree.sum() >= 5 else None)
    # Local shape for SAR-wet points the coarse router left dry: the median
    # rise/fall of the nearest agreeing points within 10 km (the flood wave
    # at Lismore is not the flood wave at Woodburn), else the basin median.
    local_shape = {}
    if agree.sum() >= 3:
        from scipy.spatial import cKDTree
        mx = 111320.0 * math.cos(math.radians(float(np.mean(lats))))
        xy = np.column_stack([lons * mx, lats * 110540.0])
        tree = cKDTree(xy[agree])
        ag_idx = np.flatnonzero(agree)
        k = int(min(8, agree.sum()))
        for j in np.flatnonzero(wet & ~sim_wet_pass):
            d, nn = tree.query(xy[j], k=k, distance_upper_bound=10000.0)
            d, nn = np.atleast_1d(d), np.atleast_1d(nn)
            ok = np.isfinite(d)
            if ok.sum() >= 3:
                cols = ag_idx[nn[ok]]
                local_shape[j] = np.median(series[:, cols] - sim_pass[cols], axis=1)
    for j, p in enumerate(props):
        sim = series[:, j]
        if wet[j] and not sim_wet_pass[j] and j in local_shape:
            anchored = np.maximum(0.0, obs_m[j] + local_shape[j])
            basis = 'SAR-observed; hydrograph shape from nearby routed properties'
        elif wet[j] and not sim_wet_pass[j]:
            if regional is None:
                anchored = np.full_like(sim, obs_m[j])
                basis = 'SAR-observed; router dry here, no regional shape (flat at observation)'
            else:
                anchored = np.maximum(0.0, obs_m[j] + regional)
                basis = 'SAR-observed; regional hydrograph shape'
        elif wet[j] or dry[j]:
            anchored = np.maximum(0.0, sim - sim_pass[j] + obs_m[j])
            basis = 'anchored to SAR at the pass'
        else:
            anchored = np.maximum(0.0, sim)
            basis = 'model only (SAR ambiguous here)'
        k_peak = int(np.argmax(anchored))
        peak_m = float(anchored[k_peak])
        hours_wet = float((anchored >= 0.03).sum() * ROUTER['record_every_h'])
        out[str(p['property_id'])] = {
            'depth_ft': [round(float(v) * FT_PER_M, 2) for v in anchored[::step]],
            'peak_depth_ft': round(peak_m * FT_PER_M, 2),
            'peak_time': stamps[k_peak],
            'pass_time': t_pass.isoformat(),
            'obs_depth_ft': round(float(depth_ft[j]), 2),
            'sim_depth_at_pass_ft': round(float(sim_pass[j]) * FT_PER_M, 2),
            'hours_wet': hours_wet,
            'basis': basis,
            '_series_ft_hourly': [round(float(v) * FT_PER_M, 2) for v in anchored],
        }
    regional_out = None
    if regional is not None:
        regional_out = {'rise_ft': [round(float(v) * FT_PER_M, 2) for v in regional[::step]]}

    # Peak water surface for the road network: observation plus the rise the
    # router says came before the pass (never below what SAR saw).
    rise = np.maximum(0.0, res['h_max'] - res['h_snapshot'])
    obs_on_f = obs_grid.sample(*zf.lonlat(*np.mgrid[0:zf.shape[0], 0:zf.shape[1]])).reshape(zf.shape)
    peak_grid = Grid(np.nan_to_num(obs_on_f) + rise, zf.west, zf.north, zf.dx, zf.dy)

    return {
        'method': 'Bates et al. (2010) local-inertial 2D, rain-on-grid, CSI-calibrated forcing',
        'calibrated_k': best,
        'calibration_trials': [{'k': k, **trials[k]} for k in sorted(trials)],
        'skill_at_pass': final_skill,
        'pass_time': t_pass.isoformat(),
        'sim_start': t0f.isoformat(),
        'sim_end': (t0f + timedelta(seconds=float(times[-1]))).isoformat(),
        'res_calib_m': rc_m, 'res_final_m': rf_m,
        'domain_bbox': domain, 'grid_shape': list(zf.shape),
        'steps': res['steps'],
        'rain_product': 'NOAA Stage IV' if us else 'CHIRPS v2.0',
        'mass_balance': {'rain_in_m3': round(res['volume_in']), 'out_m3': round(res['volume_out']),
                         'stored_m3': round(res['volume_final'])},
        'seconds': round(time.time() - t_wall, 1),
        'regional_shape': regional_out,
        'times': stamps[::step],
        'properties': out,
        'peak_depth_grid': peak_grid,
    }

