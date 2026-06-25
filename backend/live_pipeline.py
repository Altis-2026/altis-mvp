"""
live_pipeline.py — On-demand, global flood analysis.

Given a set of geocoded properties (anywhere on Earth) and an event date/window,
this runs the *real* Sentinel-1 flood-detection pipeline live on Google Earth
Engine and returns the same triage shape as the pre-computed demo events — so
the rest of the app (results endpoint, dispatch queue, drawer, PDF) works
identically whether the data came from a baked CSV or a fresh Colombia run.

Authentication is via a GEE service-account key (pipeline.config
.GEE_SERVICE_ACCOUNT_KEY). If no key is configured, `gee_available()` is False
and the caller returns an honest "live analysis needs credentials" response
rather than pretending. Nothing here fabricates results.
"""
import json
import math
from datetime import datetime, timedelta

from pipeline.config import (
    GEE_PROJECT, GEE_SERVICE_ACCOUNT_KEY, TRIAGE, ENSEMBLE,
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
    """True when a service-account key is configured and the file exists."""
    return bool(GEE_SERVICE_ACCOUNT_KEY)


def init_ee() -> bool:
    """
    Initialize Earth Engine with the service-account credentials. Idempotent.
    Returns True on success; raises LiveAnalysisError with a clear message if
    credentials are missing or authentication fails.
    """
    global _ee_ready
    if _ee_ready:
        return True
    if not GEE_SERVICE_ACCOUNT_KEY:
        raise LiveAnalysisError(
            "Live satellite analysis requires a Google Earth Engine service-account "
            "key. Set GEE_SERVICE_ACCOUNT_KEY (or drop the key at secrets/ee-sa-key.json) "
            "and restart the backend.")
    try:
        import ee
        with open(GEE_SERVICE_ACCOUNT_KEY) as f:
            email = json.load(f)['client_email']
        ee.Initialize(ee.ServiceAccountCredentials(email, GEE_SERVICE_ACCOUNT_KEY),
                      project=GEE_PROJECT)
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
        build_flood_depth_image, sample_properties,
    )
    from pipeline.uncertainty import depth_interval_ft
    from pipeline.triage_core import (
        confidence_breakdown, classify_triage, ensemble_disagreement,
    )

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

    combined = build_flood_depth_image(
        bbox, pre_img, post_img, dem, wse_radius_m,
        optical_water=optical_water, optical_valid=optical_valid)

    sampled = sample_properties(combined, props_df, batch_size=100, throttle=False)
    sampled = sampled.set_index('property_id')

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
        }

        breakdown = confidence_breakdown(row, event_cfg)
        row['confidence_score'] = breakdown['final_score']
        impact_class, action = classify_triage(row, TRIAGE)

        disagree, ens_note, votes = ensemble_disagreement(row, ENSEMBLE)
        if disagree and ENSEMBLE['downgrade_to_review'] and impact_class != 'Review':
            impact_class = 'Review'
            action = 'Flag for manual review — independent sensors (SAR/optical/DEM) disagree'

        lo, hi, ci = depth_interval_ft(row['max_depth_ft'], dem_res, row['wse_spread_ft'])
        if row['max_depth_ft'] > 0.1:
            flooded += 1

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
            'ensemble_disagreement': int(disagree),
            'ensemble_note':     ens_note,
            'ensemble_votes':    json.dumps(votes),
            'adjuster_note':     _note(prop.get('address', ''), row, impact_class),
            'color':             COLOR_MAP.get(impact_class, '#6B8FA3'),
        })

    meta = {
        'label':       label,
        'bbox':        bbox,
        'windows':     windows,
        'dem_resolution_m': dem_res,
        'sar_orbit_pass':   orbit,
        'pre_scene_count':  pre_n,
        'post_scene_count': post_n,
        'optical_scene_count': optical_n,
        'flooded_count':    flooded,
        'analyzed_count':   len(results),
        'is_live':          True,
    }
    return {'results': results, 'bbox': bbox, 'meta': meta}


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
