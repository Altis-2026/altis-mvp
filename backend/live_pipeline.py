"""
live_pipeline.py — On-demand, global flood analysis.

Given a set of geocoded properties (anywhere on Earth) and an event date/window,
this runs the *real* Sentinel-1 flood-detection pipeline live on Google Earth
Engine and returns the same triage shape as the pre-computed demo events — so
the rest of the app (results endpoint, dispatch queue, drawer, PDF) works
identically whether the data came from a baked CSV or a fresh Colombia run.

Authentication is via a GEE service-account key — either a file on disk
(pipeline.config.GEE_SERVICE_ACCOUNT_KEY, for local dev) or the key's raw
JSON content in an env var (GEE_SERVICE_ACCOUNT_KEY_JSON, for containerized
deploys where mounting a secret file is awkward). If neither is configured,
`gee_available()` is False and the caller returns an honest "live analysis
needs credentials" response rather than pretending. Nothing here fabricates
results.
"""
import json
import math
from datetime import datetime, timedelta

from pipeline.config import (
    GEE_PROJECT, GEE_SERVICE_ACCOUNT_KEY, GEE_SERVICE_ACCOUNT_KEY_JSON,
    TRIAGE, ENSEMBLE,
)

COLOR_MAP = {
    'Dispatch':       '#FF4444',
    'Remote-Approve': '#4CAF82',
    'Remote-Deny':    '#6B8FA3',
    'Review':         '#FFB347',
    'No Coverage':    '#444444',
}

_ee_ready = False


class LiveAnalysisError(Exception):
    """Raised for actionable, user-facing live-analysis failures."""


def gee_available() -> bool:
    """True when a service-account key is configured, as a file or raw JSON."""
    return bool(GEE_SERVICE_ACCOUNT_KEY or GEE_SERVICE_ACCOUNT_KEY_JSON)


def _ffh(match):
    """First-floor height (ft) from an NSI match row, or None."""
    if not match:
        return None
    from pipeline.structures import first_floor_height_ft
    return first_floor_height_ft(match.get('found_ht'))


def _foundation(match):
    """Human-readable foundation type from an NSI match row, or None."""
    if not match:
        return None
    from pipeline.structures import foundation_label
    return foundation_label(match.get('found_type'))


def _depth_ffe(match, depth_above_ground_ft):
    """
    Depth above first floor, or None when the foundation height is unknown.

    Never falls back to depth above ground — reporting an unadjusted number in
    a field labelled "above first floor" is exactly the confusion Phase 2
    exists to remove.
    """
    if not match:
        return None
    from pipeline.structures import depth_above_first_floor
    return depth_above_first_floor(depth_above_ground_ft, match.get('found_ht'))


def init_ee() -> bool:
    """
    Initialize Earth Engine with the service-account credentials. Idempotent.
    Returns True on success; raises LiveAnalysisError with a clear message if
    credentials are missing or authentication fails.
    """
    global _ee_ready
    if _ee_ready:
        return True
    if not gee_available():
        raise LiveAnalysisError(
            "Live satellite analysis requires a Google Earth Engine service-account "
            "key. Set GEE_SERVICE_ACCOUNT_KEY_JSON (the key's raw JSON content — the "
            "deploy-friendly form) or GEE_SERVICE_ACCOUNT_KEY / secrets/ee-sa-key.json "
            "(a file path, for local dev) and restart the backend.")
    try:
        import ee
        if GEE_SERVICE_ACCOUNT_KEY_JSON:
            key_data = GEE_SERVICE_ACCOUNT_KEY_JSON
            email = json.loads(key_data)['client_email']
            creds = ee.ServiceAccountCredentials(email, key_data=key_data)
        else:
            with open(GEE_SERVICE_ACCOUNT_KEY) as f:
                email = json.load(f)['client_email']
            creds = ee.ServiceAccountCredentials(email, GEE_SERVICE_ACCOUNT_KEY)
        ee.Initialize(creds, project=GEE_PROJECT)
        _ee_ready = True
        return True
    except Exception as e:
        raise LiveAnalysisError(f"Earth Engine authentication failed: {e}")


# ── Event window helpers ──────────────────────────────────────────────────────

def derive_windows(event_date: str, pre_days: int = 30, post_days: int = 14) -> dict:
    """
    Turn a single event date (the flood/landfall date) into the pre/post SAR
    windows the detector needs:
      pre  = [event-‍pre_days, event-2]   (baseline, dry)
      post = [event,          event+post_days]  (captures the flood)
    Returns a dict with the four dates + days_since_event for confidence recency.
    """
    try:
        d = datetime.strptime(event_date[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        raise LiveAnalysisError(f"Invalid event date '{event_date}'. Use YYYY-MM-DD.")
    return {
        'pre_start':  (d - timedelta(days=pre_days)).strftime("%Y-%m-%d"),
        'pre_end':    (d - timedelta(days=2)).strftime("%Y-%m-%d"),
        'post_start': d.strftime("%Y-%m-%d"),
        'post_end':   (d + timedelta(days=post_days)).strftime("%Y-%m-%d"),
        'days_since_event': 3,
    }


def bbox_from_properties(props: list, pad_deg: float = 0.05) -> list:
    """
    Bounding box [west, south, east, north] enclosing all geocoded properties,
    padded so each point sits comfortably inside the analyzed scene. Pad is
    ~5km; widened for a single isolated property.
    """
    lats = [float(p['latitude']) for p in props if p.get('latitude') is not None]
    lons = [float(p['longitude']) for p in props if p.get('longitude') is not None]
    if not lats or not lons:
        raise LiveAnalysisError("No geocoded properties to analyze.")
    if max(lats) - min(lats) < 0.01 and max(lons) - min(lons) < 0.01:
        pad_deg = max(pad_deg, 0.08)  # single cluster → give the scene room
    return [min(lons) - pad_deg, min(lats) - pad_deg,
            max(lons) + pad_deg, max(lats) + pad_deg]


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze_portfolio_live(props: list, event_date: str = None,
                           windows: dict = None, wse_radius_m: int = 300,
                           label: str = "Custom event") -> dict:
    """
    Run a live flood analysis over `props` (geocoded portfolio properties) for
    the given event. Provide either `event_date` (auto-windowed) or explicit
    `windows` (pre_start/pre_end/post_start/post_end[/days_since_event]).

    Returns {results, bbox, meta} where `results` matches the pre-computed
    analysis shape (impact_class, max_depth_ft, pct_flooded, confidence_score,
    adjuster_note, color, …) so existing persistence + UI just work.
    """
    init_ee()
    import pandas as pd
    from pipeline.flood_detect import (
        load_dem, load_sar_composite, load_optical_water_mask,
        load_sar_vh_composite, load_sar_slice, load_rainfall_sum,
        load_ndvi_median, build_flood_depth_image, sample_properties,
    )
    from pipeline.uncertainty import depth_interval_ft
    from pipeline.triage_core import (
        confidence_breakdown, classify_triage, ensemble_disagreement,
        dualpol_review_override,
    )
    from pipeline.severity import estimate_claim_range
    from pipeline.config import DURATION, VEGETATION

    geocoded = [p for p in props if p.get('latitude') is not None
                and p.get('longitude') is not None]
    if not geocoded:
        raise LiveAnalysisError("None of the portfolio properties are geocoded.")

    if windows is None:
        if not event_date:
            raise LiveAnalysisError("Provide an event date or explicit windows.")
        windows = derive_windows(event_date)
    days_since = int(windows.get('days_since_event', 3))

    bbox = bbox_from_properties(geocoded)

    props_df = pd.DataFrame([{
        'property_id': p['property_id'],
        'address':     p.get('address', ''),
        'latitude':    float(p['latitude']),
        'longitude':   float(p['longitude']),
    } for p in geocoded])

    # ── Earth Engine detection ────────────────────────────────────────────
    dem, dem_res = load_dem(bbox)
    pre_img, pre_n, orbit = load_sar_composite(
        bbox, windows['pre_start'], windows['pre_end'])
    post_img, post_n, _ = load_sar_composite(
        bbox, windows['post_start'], windows['post_end'], orbit_pass=orbit)
    optical_water, optical_valid, optical_n = load_optical_water_mask(
        bbox, windows['post_start'], windows['post_end'])

    # ── Auxiliary signals. Each one degrades independently: a failure logs a
    #    clear reason into meta['signal_status'] and the analysis continues —
    #    an auxiliary signal must never crash the core flood detection, and
    #    its absence must be explainable, never a silent zero.
    signal_status = {}

    signal_status['optical'] = ('ok' if optical_n > 0
                                else 'unavailable: no cloud-free Sentinel-2 scene in window')

    pre_vh = post_vh = None
    vh_n = 0
    try:
        pre_vh, _ = load_sar_vh_composite(
            bbox, windows['pre_start'], windows['pre_end'], orbit)
        post_vh, vh_n = load_sar_vh_composite(
            bbox, windows['post_start'], windows['post_end'], orbit)
        if pre_vh is None or post_vh is None:
            pre_vh = post_vh = None
            vh_n = 0
            signal_status['dual_pol'] = 'unavailable: no VH-polarized scene in window'
        else:
            signal_status['dual_pol'] = 'ok'
    except Exception as e:
        pre_vh = post_vh = None
        vh_n = 0
        signal_status['dual_pol'] = f'error: {e}'
        print(f"  [signal] dual-pol VH failed: {e}")

    rain_img = None
    try:
        rain_img, _ = load_rainfall_sum(bbox, windows['pre_end'], windows['post_end'])
        signal_status['rainfall'] = 'ok'
    except Exception as e:
        signal_status['rainfall'] = f'error: {e}'
        print(f"  [signal] CHIRPS rainfall failed: {e}")

    ndvi_pre = ndvi_post = None
    try:
        ndvi_pre, _ = load_ndvi_median(bbox, windows['pre_start'], windows['pre_end'])
        ndvi_post, _ = load_ndvi_median(bbox, windows['post_start'], windows['post_end'])
        if ndvi_pre is None or ndvi_post is None:
            ndvi_pre = ndvi_post = None
            signal_status['vegetation'] = 'unavailable: no cloud-free pre/post optical pair'
        else:
            signal_status['vegetation'] = 'ok'
    except Exception as e:
        ndvi_pre = ndvi_post = None
        signal_status['vegetation'] = f'error: {e}'
        print(f"  [signal] NDVI delta failed: {e}")

    # Inundation-duration slices over the post window (same orbit + threshold).
    post_s = datetime.strptime(windows['post_start'], "%Y-%m-%d")
    post_e = datetime.strptime(windows['post_end'], "%Y-%m-%d")
    total_days = max(1, (post_e - post_s).days)
    n_slices = DURATION['n_slices']
    slice_edges = [post_s + timedelta(days=round(total_days * i / n_slices))
                   for i in range(n_slices + 1)]
    post_slices, slice_meta = [], []
    for i in range(n_slices):
        s0, s1 = slice_edges[i], slice_edges[i + 1]
        try:
            img, cnt = load_sar_slice(bbox, s0.strftime("%Y-%m-%d"),
                                      s1.strftime("%Y-%m-%d"), orbit)
        except Exception as e:
            img, cnt = None, 0
            print(f"  [signal] duration slice {i} failed: {e}")
        post_slices.append(img)
        slice_meta.append({'start': s0.strftime("%Y-%m-%d"),
                           'end': s1.strftime("%Y-%m-%d"),
                           'days': (s1 - s0).days, 'scenes': cnt})
    with_scenes = sum(1 for s in slice_meta if s['scenes'] > 0)
    signal_status['duration'] = (
        'ok' if with_scenes >= 2
        else f'unavailable: only {with_scenes} post-window slice(s) have scenes')
    print(f"  [signals] {signal_status}")

    # ── Phase 1a: multi-temporal baseline (per-pixel mean/std over ~a year of
    #    same-orbit pre-event scenes). Degrades to the single pre-event
    #    composite exactly like every other auxiliary signal here.
    baseline_mean = baseline_std = None
    baseline_n = 0
    base_start = base_end = None
    try:
        from pipeline.flood_detect import load_sar_baseline, baseline_window
        from pipeline.config import BASELINE
        base_start, base_end = baseline_window(windows['post_start'])
        baseline_mean, baseline_std, baseline_n = load_sar_baseline(
            bbox, base_start, base_end, orbit)
        if baseline_n < BASELINE['min_scenes']:
            baseline_mean = baseline_std = None
            signal_status['baseline'] = (
                f'unavailable: only {baseline_n} pre-event scenes on orbit '
                f'{orbit} (need {BASELINE["min_scenes"]})')
        else:
            signal_status['baseline'] = f'ok: {baseline_n} scenes'
    except Exception as e:
        baseline_mean = baseline_std = None
        signal_status['baseline'] = f'error: {e}'
        print(f"  [signal] multi-temporal baseline failed: {e}")

    # ── Phase 1c: cross-orbit stacking — every other orbit with post-event
    #    coverage contributes an independently-thresholded mask.
    orbit_stack = {}
    try:
        from pipeline.flood_detect import load_sar_orbits, load_sar_baseline
        from pipeline.config import CROSS_ORBIT, BASELINE
        if CROSS_ORBIT['enabled']:
            for other, (composite, n_sc) in load_sar_orbits(
                    bbox, windows['post_start'], windows['post_end']).items():
                if other == orbit:
                    continue
                o_mean, o_std, o_n = (None, None, 0)
                if base_start:
                    o_mean, o_std, o_n = load_sar_baseline(
                        bbox, base_start, base_end, other)
                    if o_n < BASELINE['min_scenes']:
                        o_mean = o_std = None
                orbit_stack[other] = {'post': composite, 'pre': None,
                                      'baseline_mean': o_mean, 'baseline_std': o_std}
            signal_status['cross_orbit'] = (
                f'ok: {len(orbit_stack) + 1} orbits' if orbit_stack
                else 'unavailable: only one orbit covers this window')
    except Exception as e:
        orbit_stack = {}
        signal_status['cross_orbit'] = f'error: {e}'
        print(f"  [signal] cross-orbit stacking failed: {e}")

    # ── Phase 1b: HAND for the DEM-hydrology plausibility vote.
    hand_img, hand_source = None, 'unavailable'
    try:
        from pipeline.flood_detect import load_hand
        hand_img, hand_source = load_hand(bbox)
        signal_status['hand'] = ('ok' if hand_img is not None
                                 else f'unavailable: {hand_source}')
    except Exception as e:
        signal_status['hand'] = f'error: {e}'
        print(f"  [signal] HAND failed: {e}")

    combined = build_flood_depth_image(
        bbox, pre_img, post_img, dem, wse_radius_m,
        optical_water=optical_water, optical_valid=optical_valid,
        pre_vh=pre_vh, post_vh=post_vh, rain=rain_img,
        ndvi_pre=ndvi_pre, ndvi_post=ndvi_post, post_slices=post_slices,
        hand=hand_img, baseline_mean=baseline_mean, baseline_std=baseline_std,
        orbit_stack=orbit_stack)

    # ── Phase 2: structure attributes (CONUS only). When a property matches an
    #    NSI structure we sample its footprint at Sentinel-1's native 10m
    #    spacing instead of a 50m circle centred on the geocoded point.
    nsi_by_pid = {}
    sample_df = props_df.copy()
    sample_scale = 30
    try:
        from pipeline import structures as struct
        nsi_df = struct.fetch_nsi_structures(bbox, verbose=False)
        nsi_match = struct.match_properties_to_structures(props_df, nsi_df)
        n_matched = int(nsi_match['nsi_matched'].sum())
        nsi_by_pid = {r['property_id']: r for r in nsi_match.to_dict('records')}
        if n_matched:
            m = nsi_match.set_index('property_id')
            sample_df['sample_lat'] = sample_df['property_id'].map(
                m['nsi_lat'].where(m['nsi_matched']))
            sample_df['sample_lon'] = sample_df['property_id'].map(
                m['nsi_lon'].where(m['nsi_matched']))
            sample_df['sample_radius_m'] = sample_df['property_id'].map(
                {pid: struct.footprint_radius_m(a) if ok else None
                 for pid, a, ok in zip(m.index, m['ftprntsqft'], m['nsi_matched'])})
            sample_scale = 10
        signal_status['structures'] = (
            f'ok: {n_matched}/{len(props_df)} matched to NSI structures'
            if n_matched else 'unavailable: no NSI structures (outside CONUS?)')
    except Exception as e:
        nsi_by_pid = {}
        signal_status['structures'] = f'error: {e}'
        print(f"  [signal] NSI structures failed: {e}")

    sampled = sample_properties(combined, sample_df, batch_size=100,
                                throttle=False, scale=sample_scale)
    sampled = sampled.set_index('property_id')

    # FEMA NFHL flood zones (US properties only; non-US resolve to None
    # instantly, service failures degrade to 'unavailable' without blocking).
    from backend.fema import flood_zones_batch, is_us_coord
    coords = [(float(p['latitude']), float(p['longitude'])) for p in geocoded]
    fema_zones = flood_zones_batch(coords)
    fema_by_pid = {p['property_id']: z for p, z in zip(geocoded, fema_zones)}
    us_count = sum(1 for lat, lon in coords if is_us_coord(lat, lon))
    if us_count == 0:
        signal_status['fema_nfhl'] = 'not applicable: no US properties (NFHL is US-only)'
    elif all(z['flood_zone'] == 'unavailable' for z, (lat, lon) in
             zip(fema_zones, coords) if is_us_coord(lat, lon)):
        signal_status['fema_nfhl'] = 'error: FEMA NFHL service unreachable'
    else:
        signal_status['fema_nfhl'] = 'ok'

    # ── Triage scoring (identical calibrated logic as the demo events) ────
    event_cfg = {'days_since_event': days_since}
    results = []
    flooded = 0
    by_pid = {p['property_id']: p for p in geocoded}

    for pid, prop in by_pid.items():
        if pid in sampled.index:
            s = sampled.loc[pid].to_dict()
        else:
            s = {'pct_flooded': 0.0, 'max_depth_ft': 0.0, 'urban_flag': 0,
                 'optical_available': 0, 'optical_water_pct': 0.0,
                 'wse_spread_ft': 0.0, 'rel_elev_ft': 0.0}

        row = {
            'max_depth_ft':      float(s.get('max_depth_ft', 0.0)),
            'pct_flooded':       float(s.get('pct_flooded', 0.0)),  # 0-1 here
            'urban_flag':        int(s.get('urban_flag', 0)),
            'optical_available': int(s.get('optical_available', 0)),
            'optical_water_pct': float(s.get('optical_water_pct', 0.0)),
            'rel_elev_ft':       float(s.get('rel_elev_ft', 0.0)),
            'wse_spread_ft':     float(s.get('wse_spread_ft', 0.0)),
            'vh_available':      int(s.get('vh_available', 0) or 0),
            'vh_water_pct':      float(s.get('vh_water_pct', 0.0) or 0.0),
            # Phase 1: HAND drives the DEM-hydrology vote when present. Passed
            # through as-is (including None) — ensemble_votes must be able to
            # tell "no HAND here" from "HAND is 0", which means at the drainage
            # line and is the most flood-prone value there is.
            'hand_ft':           s.get('hand_ft'),
        }

        breakdown = confidence_breakdown(row, event_cfg)
        row['confidence_score'] = breakdown['final_score']
        impact_class, action = classify_triage(row, TRIAGE)

        disagree, ens_note, votes = ensemble_disagreement(row, ENSEMBLE)
        if disagree and ENSEMBLE['downgrade_to_review'] and impact_class != 'Review':
            impact_class = 'Review'
            action = 'Flag for manual review — independent sensors (SAR/optical/DEM) disagree'

        # Dual-pol hard override: VV flood call uncorroborated by VH → Review.
        dp_override, dp_note = dualpol_review_override(row)
        if dp_override and impact_class != 'Review':
            impact_class = 'Review'
            action = 'Flag for manual review — dual-polarization channels disagree'
            if not ens_note:
                ens_note = dp_note

        lo, hi, ci = depth_interval_ft(row['max_depth_ft'], dem_res, row['wse_spread_ft'])
        is_flooded = row['pct_flooded'] >= 0.10 or row['max_depth_ft'] > 0.1
        if row['max_depth_ft'] > 0.1:
            flooded += 1

        # Inundation duration from post-window slices (None = insufficient data).
        known = [(i, s.get(f'flood_s{i}')) for i in range(n_slices)
                 if s.get(f'flood_s{i}') is not None
                 and not (isinstance(s.get(f'flood_s{i}'), float)
                          and math.isnan(s.get(f'flood_s{i}')))]
        if len(known) >= 2 and is_flooded:
            duration_days = sum(slice_meta[i]['days'] for i, v in known
                                if v >= DURATION['slice_flood_pct'])
        elif len(known) >= 2:
            duration_days = 0
        else:
            duration_days = None

        # Claim severity range in dollars (reserving aid). The displayed depth
        # CI is conservatively built from the DEM's ABSOLUTE vertical RMSE, but
        # depth is a same-DEM difference (WSE − ground) where that bias is
        # common-mode and largely cancels — so the dollar range uses the
        # relative-error component (≈25% of depth, floor 0.5ft) rather than
        # letting a coarse global DEM push every low estimate to $0.
        sev_ci = min(ci, max(0.5, 0.25 * row['max_depth_ft']))
        # Phase 3: select the depth-damage curve from the structure's own NSI
        # attributes and index it on depth above the FIRST FLOOR, which is what
        # published curves take. All of these degrade to None outside CONUS or
        # without an NSI match, in which case estimate_claim_range falls back
        # to the generic curve on depth above ground exactly as before.
        _nsi = nsi_by_pid.get(pid) or {}
        sev = estimate_claim_range(
            row['max_depth_ft'], sev_ci, prop.get('coverage_amount'),
            contents_coverage=prop.get('contents_coverage_amount')
                              or _nsi.get('val_cont'),
            occupancy_type=_nsi.get('occtype'),
            num_stories=_nsi.get('num_story'),
            basement_type=_nsi.get('basement_type'),
            depth_above_first_floor_ft=_depth_ffe(_nsi or None,
                                                  row['max_depth_ft']),
            duration_days=duration_days)

        # Subrogation candidate: flooded AND adjacent to permanent water /
        # drainage — worth checking whether third-party infrastructure
        # channeled the water. A screening flag, not a legal determination.
        near_water = int(s.get('near_water_flag', 0) or 0)
        subrogation = bool(is_flooded and near_water)

        # Surge-miss safeguard: storm surge can recede between satellite
        # passes, so a low-lying waterfront parcel that reads dry gets a
        # verification flag instead of a silently confident "no flood" —
        # the honest mitigation for SAR's revisit-cadence blind spot.
        surge_check = bool((not is_flooded) and near_water
                           and row['rel_elev_ft'] <= 4.0)

        ndvi_valid = int(s.get('ndvi_valid', 0) or 0)
        ndvi_delta = float(s.get('ndvi_delta', 0.0) or 0.0)
        fema = fema_by_pid.get(pid, {'flood_zone': None, 'sfha': None})

        results.append({
            **prop,
            'impact_class':      impact_class,
            'max_depth_ft':      round(row['max_depth_ft'], 2),
            'pct_flooded':       round(row['pct_flooded'] * 100, 1),  # to % for display
            'depth_lower_ft':    lo,
            'depth_upper_ft':    hi,
            'depth_ci_ft':       ci,
            'confidence_score':  row['confidence_score'],
            'confidence_factors': json.dumps(breakdown['factors']),
            'recommended_action': action,
            'urban_flag':        row['urban_flag'],
            'optical_available': row['optical_available'],
            'optical_water_pct': round(row['optical_water_pct'], 4),
            'ensemble_disagreement': int(disagree or dp_override),
            'ensemble_note':     ens_note,
            'ensemble_votes':    json.dumps(votes),
            # Round-7 signals
            'rain_mm':           float(s.get('rain_mm', 0.0) or 0.0),
            'vh_available':      row['vh_available'],
            'vh_water_pct':      round(row['vh_water_pct'], 4),
            'duration_days':     duration_days,
            # Per-slice flooded fraction across the post window (None where no
            # scene covered the slice) — powers the drawer's inundation
            # timeline. Slice date ranges live in meta['duration_slices'].
            'flood_slices':      json.dumps([
                (round(float(s[f'flood_s{i}']), 4)
                 if s.get(f'flood_s{i}') is not None
                 and not (isinstance(s.get(f'flood_s{i}'), float)
                          and math.isnan(s.get(f'flood_s{i}')))
                 else None)
                for i in range(n_slices)]),
            'ndvi_delta':        round(ndvi_delta, 3) if ndvi_valid else None,
            'vegetation_loss':   int(ndvi_valid and ndvi_delta >= VEGETATION['loss_flag_delta']),
            'near_water_flag':   near_water,
            'subrogation_flag':  int(subrogation),
            'surge_check_flag':  int(surge_check),
            'flood_zone':        fema['flood_zone'],
            'sfha_flag':         fema['sfha'],
            # ── Phase 2: what the adjuster needs to see which number drove the
            #    call. depth_above_ffe_ft is None (not 0) where the first-floor
            #    height is unknown, so the drawer can say "unavailable" instead
            #    of implying the adjustment was made.
            'first_floor_height_ft': _ffh(nsi_by_pid.get(pid)),
            'foundation_type':   _foundation(nsi_by_pid.get(pid)),
            'depth_above_ffe_ft': _depth_ffe(nsi_by_pid.get(pid),
                                             row['max_depth_ft']),
            'first_floor_source': ('USACE NSI (modeled)'
                                   if _ffh(nsi_by_pid.get(pid)) is not None
                                   else 'unavailable'),
            'hand_ft':           (round(float(s['hand_ft']), 2)
                                  if s.get('hand_ft') is not None
                                  and not (isinstance(s.get('hand_ft'), float)
                                           and math.isnan(s['hand_ft']))
                                  else None),
            'severity_low_usd':  sev['low'] if sev else None,
            'severity_mid_usd':  sev['mid'] if sev else None,
            'severity_high_usd': sev['high'] if sev else None,
            'severity_damage_pct': sev['damage_pct'] if sev else None,
            # ── Phase 3: which curve produced the number, what depth it was
            #    indexed on, and contents kept SEPARATE from structure because
            #    NFIP settles them as separate coverages and carriers reserve
            #    them separately.
            'severity_curve':        sev.get('curve') if sev else None,
            'severity_depth_basis':  sev.get('depth_basis') if sev else None,
            'severity_duration_mult': sev.get('duration_multiplier') if sev else None,
            'contents_low_usd':      sev.get('contents_low') if sev else None,
            'contents_mid_usd':      sev.get('contents_mid') if sev else None,
            'contents_high_usd':     sev.get('contents_high') if sev else None,
            'contents_damage_pct':   sev.get('contents_damage_pct') if sev else None,
            'total_mid_usd':         sev.get('total_mid') if sev else None,
            'adjuster_note':     _note(prop.get('address', ''), row, impact_class),
            'color':             COLOR_MAP.get(impact_class, '#6B8FA3'),
        })

    # ── Exposure summary (PIF zone-scan view: the pre-triage numbers a
    #    carrier wants within minutes of an event) ──────────────────────────
    def _cov(r):
        try:
            return float(r.get('coverage_amount') or 0)
        except (TypeError, ValueError):
            return 0.0

    in_zone = [r for r in results if r['pct_flooded'] >= 10.0 or r['max_depth_ft'] > 0.1]
    sev_rows = [r for r in results if r['severity_low_usd'] is not None]
    exposure = {
        'policies_total':    len(results),
        'policies_in_zone':  len(in_zone),
        'tiv_total':         int(sum(_cov(r) for r in results)),
        'tiv_in_zone':       int(sum(_cov(r) for r in in_zone)),
        'est_loss_low_usd':  int(sum(r['severity_low_usd'] for r in sev_rows)),
        'est_loss_mid_usd':  int(sum(r['severity_mid_usd'] for r in sev_rows)),
        'est_loss_high_usd': int(sum(r['severity_high_usd'] for r in sev_rows)),
        'by_class': {c: sum(1 for r in results if r['impact_class'] == c)
                     for c in ('Dispatch', 'Review', 'Remote-Approve', 'Remote-Deny')},
    }

    meta = {
        'label':       label,
        'bbox':        bbox,
        'windows':     windows,
        'dem_resolution_m': dem_res,
        'sar_orbit_pass':   orbit,
        'pre_scene_count':  pre_n,
        'post_scene_count': post_n,
        'optical_scene_count': optical_n,
        'vh_scene_count':   vh_n,
        'duration_slices':  slice_meta,
        'signal_status':    signal_status,
        'exposure':         exposure,
        'flooded_count':    flooded,
        'analyzed_count':   len(results),
        'is_live':          True,
    }
    return {'results': results, 'bbox': bbox, 'meta': meta}


def portfolio_risk_scores(props: list) -> dict:
    """
    Pre-event, static flood-risk score per property (1=minimal … 5=severe) —
    the 365-days-a-year underwriting/renewals view, no SAR pass needed.

    Signals (all static GEE layers, one sampling round-trip):
      - JRC Global Surface Water historical flood occurrence at the parcel
      - elevation relative to the local drainage minimum (DEM neighborhood)
      - proximity to permanent water
    Plus FEMA NFHL zone for US properties (network, budget-capped).
    """
    init_ee()
    import ee
    import pandas as pd
    from pipeline.config import RISK
    from pipeline.flood_detect import load_dem
    from backend.fema import flood_zones_batch

    geocoded = [p for p in props if p.get('latitude') is not None
                and p.get('longitude') is not None]
    if not geocoded:
        raise LiveAnalysisError("None of the portfolio properties are geocoded.")
    bbox = bbox_from_properties(geocoded)

    dem, dem_res = load_dem(bbox)
    neigh_min = (dem.reduceNeighborhood(
                     reducer=ee.Reducer.min(),
                     kernel=ee.Kernel.circle(radius=300, units='meters'))
                 .reproject(crs='EPSG:4326', scale=30))
    rel_elev_ft = dem.subtract(neigh_min).multiply(3.28084).rename('rel_elev_ft')

    gsw = ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
    occurrence = gsw.select('occurrence').unmask(0).divide(100).rename('occurrence')
    near_water = (gsw.select('occurrence').unmask(0).gte(50)
                  .focal_max(radius=300, kernelType='circle', units='meters')
                  .rename('near_water'))

    combined = (occurrence.float()
                .addBands(rel_elev_ft.float().unmask(0))
                .addBands(near_water.float().unmask(0)))

    features = [ee.Feature(
        ee.Geometry.Point([float(p['longitude']), float(p['latitude'])]).buffer(50),
        {'property_id': str(p['property_id'])}) for p in geocoded]
    sampled = combined.reduceRegions(
        collection=ee.FeatureCollection(features),
        reducer=ee.Reducer.mean(), scale=30).getInfo()

    by_pid = {}
    for feat in sampled.get('features', []):
        a = feat.get('properties', {})
        by_pid[a.get('property_id', '')] = a

    coords = [(float(p['latitude']), float(p['longitude'])) for p in geocoded]
    fema_zones = flood_zones_batch(coords)

    out = []
    for p, fema in zip(geocoded, fema_zones):
        a = by_pid.get(p['property_id'], {})
        occ = max(0.0, float(a.get('occurrence') or 0))
        rel = max(0.0, float(a.get('rel_elev_ft') or 0))
        near = float(a.get('near_water') or 0) >= 0.5

        pts = 0
        for thresh, points in RISK['occurrence_weights']:
            if occ >= thresh:
                pts += points
                break
        for thresh, points in RISK['rel_elev_weights']:
            if rel <= thresh:
                pts += points
                break
        if near:
            pts += RISK['near_water_points']
        if fema.get('sfha'):
            pts += 15

        score = 5
        for max_pts, s in RISK['score_bins']:
            if pts <= max_pts:
                score = s
                break

        out.append({
            'property_id':   p['property_id'],
            'address':       p.get('address', ''),
            'latitude':      p['latitude'],
            'longitude':     p['longitude'],
            'risk_score':    score,
            'risk_points':   pts,
            'flood_occurrence_pct': round(occ * 100, 1),
            'rel_elev_ft':   round(rel, 1),
            'near_permanent_water': int(near),
            'flood_zone':    fema.get('flood_zone'),
            'sfha_flag':     fema.get('sfha'),
        })

    return {'results': out, 'dem_resolution_m': dem_res,
            'method': 'JRC historical flood occurrence + elevation vs local '
                      'drainage + permanent-water proximity + FEMA NFHL (US)'}


_SAR_PALETTE = ['000000', '2A3A4A', '4A6A8A', 'A8D4E6', 'FFFFFF']


def real_thumbnail(lat: float, lon: float, windows: dict, is_post: bool,
                   view: str = 'sar') -> str | None:
    """
    Generate a REAL satellite thumbnail (data URL) for a point + event window
    via Earth Engine getThumbURL — Sentinel-1 VV for 'sar' (Altis blue-gray
    ramp; post uses the wettest scene), Sentinel-2 true-color for 'optical'.
    File-cached by point+window+view. Returns None on any failure so the caller
    cleanly falls back to the synthetic render.
    """
    if not gee_available():
        return None
    import hashlib
    from backend.database import get_cached_thumbnail, save_thumbnail_cache

    key = "live-" + hashlib.md5(
        f"{round(lat,4)},{round(lon,4)},{windows.get('post_start')},{view}".encode()
    ).hexdigest()[:16]
    cached = get_cached_thumbnail(key, is_post)
    if cached:
        return cached

    try:
        init_ee()
        import ee
        import requests as _rq
        region = ee.Geometry.Point([lon, lat]).buffer(450).bounds()
        start, end = ((windows['post_start'], windows['post_end']) if is_post
                      else (windows['pre_start'], windows['pre_end']))

        if view == 'optical':
            col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                   .filterBounds(region).filterDate(start, end)
                   .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 60))
                   .sort('CLOUDY_PIXEL_PERCENTAGE'))
            if col.size().getInfo() == 0:
                return None
            img = col.median().select(['B4', 'B3', 'B2'])
            params = {'region': region, 'dimensions': '300x200', 'format': 'png',
                      'min': 0, 'max': 3000, 'gamma': 1.3}
        else:
            col = (ee.ImageCollection("COPERNICUS/S1_GRD")
                   .filterBounds(region).filterDate(start, end)
                   .filter(ee.Filter.eq('instrumentMode', 'IW'))
                   .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
                   .select('VV'))
            if col.size().getInfo() == 0:
                return None
            # Post = wettest (min backscatter) to surface transient flooding;
            # pre = median baseline.
            img = (col.min() if is_post else col.median())
            params = {'region': region, 'dimensions': '300x200', 'format': 'png',
                      'min': -25, 'max': 0, 'palette': _SAR_PALETTE}

        url = img.getThumbURL(params)
        resp = _rq.get(url, timeout=40)
        if resp.status_code != 200:
            return None
        import base64
        data_url = "data:image/png;base64," + base64.b64encode(resp.content).decode()
        save_thumbnail_cache(key, is_post, data_url)
        return data_url
    except Exception as e:
        print(f"real_thumbnail failed ({lat},{lon},{view}): {e}")
        return None


def _note(address: str, row: dict, impact_class: str) -> str:
    """
    Deterministic, professional one-line adjuster note. Live analysis doesn't
    depend on an LLM round-trip (which may be unavailable), but the wording
    mirrors the batch pipeline's fallback so the UI reads consistently.
    """
    street = (address or 'this property').split(',')[0]
    depth = row['max_depth_ft']
    pct = int(round(row['pct_flooded'] * 100))
    conf = row['confidence_score']
    urban = " Dense urban setting adds measurement uncertainty." if row.get('urban_flag') else ""
    verb = {
        'Dispatch':       'Dispatch an adjuster',
        'Remote-Approve': 'Approve remotely with documentation',
        'Remote-Deny':    'Deny remotely',
        'Review':         'Route to manual review',
    }.get(impact_class, 'Review')
    return (f"Satellite analysis at {street} shows {depth:.1f}ft max flood depth "
            f"across {pct}% of the parcel at {conf}% confidence; "
            f"{verb.lower()}.{urban}")[:230]
