"""
solar.py — Measured building height and roof geometry from Google's Solar API.

Google's Building Insights endpoint returns, per roof plane, its pitch,
compass azimuth, area, and the elevation of the plane at its centre — derived
from a high-resolution aerial DSM. Two things Altis wants from that:

  1. A MEASURED building height, replacing the class-and-area typology guess
     in building_context.estimate_height_m wherever coverage exists.
  2. Real roof pitch/azimuth, so the 3D view can shape a roof from data rather
     than a procedural gable.

THIS IS THE ONLY BILLABLE CALL IN ALTIS, AND IT IS OFF BY DEFAULT.
`SOLAR['enabled']` is False unless ENABLE_SOLAR_API is explicitly set in the
environment, and `enabled()` is checked before any network call. On top of the
flag there is a hard per-request call budget, so even an enabled deployment
cannot fan out a whole portfolio into a surprise invoice. Google's pricing at
the time of writing: 10,000 Building Insights requests/month free, then
$10.00/1,000.

THE DATUM TRAP. `planeHeightAtCenterMeters` is metres above SEA LEVEL, not
above the ground under the building — verified against Google's own API
reference, and obvious in the data: a single-storey house in Meyerland reads
~18 m because that is the neighbourhood's elevation. Treating it as a height
would put every Houston bungalow six storeys tall. `building_height_m()`
therefore REQUIRES a ground elevation and returns None without one; there is
deliberately no default, because a silently wrong height is worse than no
height.

COVERAGE is real but not global. Australia (the Lismore demo) returns
NOT_FOUND. Absence is normal and callers must fall back, not error.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

from backend.database import DB_PATH
from pipeline.config import GOOGLE_MAPS_API_KEY, SOLAR

BUILDING_INSIGHTS_URL = "https://solar.googleapis.com/v1/buildingInsights:findClosest"


def enabled() -> bool:
    """
    True only when BOTH a key is configured AND the operator explicitly opted
    in via ENABLE_SOLAR_API. The key alone is never enough — this is the one
    endpoint that costs money, so switching it on has to be a deliberate act.
    """
    return bool(GOOGLE_MAPS_API_KEY) and bool(SOLAR['enabled'])


# ── Cache ────────────────────────────────────────────────────────────────────

def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS solar_cache (
            coord_key    TEXT PRIMARY KEY,
            fetched_at   TEXT,
            payload_json TEXT
        )
    """)


def _coord_key(lat: float, lon: float) -> str:
    return f"{lat:.5f},{lon:.5f}"


def cache_get(lat: float, lon: float):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT fetched_at, payload_json FROM solar_cache WHERE coord_key = ?",
            (_coord_key(lat, lon),)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        fetched = datetime.fromisoformat(row[0])
    except (TypeError, ValueError):
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - fetched > timedelta(hours=SOLAR['cache_ttl_hours']):
        return None
    try:
        return json.loads(row[1])
    except (TypeError, ValueError):
        return None


def cache_put(lat: float, lon: float, record: dict):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_table(conn)
        conn.execute("""
            INSERT OR REPLACE INTO solar_cache (coord_key, fetched_at, payload_json)
            VALUES (?, ?, ?)
        """, (_coord_key(lat, lon), datetime.now(timezone.utc).isoformat(),
              json.dumps(record)))
        conn.commit()
    finally:
        conn.close()


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_building_insights(payload: dict) -> dict:
    """
    A Building Insights response → the roof record Altis uses.

    Returns roof_planes sorted largest-first, the imagery quality/date, and
    `max_plane_elev_m` — the highest roof plane's elevation ABOVE SEA LEVEL.
    Converting that to a building height needs a ground elevation; see
    building_height_m.
    """
    potential = (payload or {}).get('solarPotential') or {}
    segments = potential.get('roofSegmentStats') or []

    planes = []
    for seg in segments:
        try:
            planes.append({
                'pitch_deg':   round(float(seg['pitchDegrees']), 1),
                'azimuth_deg': round(float(seg['azimuthDegrees']), 1),
                'elev_m':      round(float(seg['planeHeightAtCenterMeters']), 2),
                'area_m2':     round(float((seg.get('stats') or {}).get('areaMeters2', 0)), 1),
            })
        except (KeyError, TypeError, ValueError):
            continue
    planes.sort(key=lambda p: p['area_m2'], reverse=True)

    quality = (payload or {}).get('imageryQuality')
    date = (payload or {}).get('imageryDate') or {}
    date_str = None
    if date.get('year'):
        date_str = f"{date['year']:04d}-{int(date.get('month') or 1):02d}"

    return {
        'roof_planes':      planes,
        'plane_count':      len(planes),
        'max_plane_elev_m': max((p['elev_m'] for p in planes), default=None),
        'min_plane_elev_m': min((p['elev_m'] for p in planes), default=None),
        'roof_area_m2':     round(float((potential.get('wholeRoofStats') or {})
                                        .get('areaMeters2', 0)), 1) or None,
        'imagery_quality':  quality,
        'imagery_date':     date_str,
        'quality_ok':       quality in SOLAR['required_quality'],
    }


def building_height_m(record: dict, ground_elev_m) -> float | None:
    """
    Building height above local ground, in metres, or None when it cannot be
    computed honestly.

    `ground_elev_m` is REQUIRED and has no default on purpose: the Solar API
    reports roof planes in metres above sea level, so without a ground
    elevation from the same point there is no height here at all, only an
    elevation. Returns None rather than a guess when the ground is unknown,
    the imagery is too coarse to trust, or the arithmetic yields something
    physically implausible.
    """
    if record is None or ground_elev_m is None:
        return None
    if not record.get('quality_ok'):
        return None
    top = record.get('max_plane_elev_m')
    if top is None:
        return None
    try:
        height = float(top) - float(ground_elev_m)
    except (TypeError, ValueError):
        return None
    # A roof below its own ground, or taller than a 20-storey block, means the
    # ground elevation and the imagery disagree — discard rather than render.
    if height < 2.0 or height > 70.0:
        return None
    return round(height, 2)


# ── Lookup ───────────────────────────────────────────────────────────────────

def lookup(lat: float, lon: float, use_cache: bool = True, session=None) -> dict:
    """
    Roof geometry at a coordinate, or an unavailable record with a reason.
    Returns {'available', 'reason', ...parsed fields}. Never raises, and never
    makes a network call unless `enabled()`.
    """
    if not enabled():
        return {'available': False,
                'reason': 'Solar API is disabled (set ENABLE_SOLAR_API to turn it on).'}

    if use_cache:
        hit = cache_get(lat, lon)
        if hit is not None:
            return hit

    http = session or requests
    try:
        resp = http.get(BUILDING_INSIGHTS_URL, params={
            'location.latitude': lat,
            'location.longitude': lon,
            'key': GOOGLE_MAPS_API_KEY,
        }, timeout=SOLAR['timeout_s'])
        payload = resp.json()
        status = resp.status_code
    except Exception as e:
        return {'available': False, 'reason': f"Solar API request failed: {type(e).__name__}"}

    if status != 200 or 'error' in (payload or {}):
        err = ((payload or {}).get('error') or {})
        reason = err.get('status') or f"HTTP {status}"
        # NOT_FOUND is ordinary — the address is outside Google's coverage.
        record = {'available': False,
                  'reason': 'Outside Solar API coverage' if reason == 'NOT_FOUND'
                            else f"Solar API error: {reason}"}
        if use_cache and reason == 'NOT_FOUND':
            cache_put(lat, lon, record)
        return record

    record = {'available': True, 'reason': None, **parse_building_insights(payload)}
    if use_cache:
        cache_put(lat, lon, record)
    return record


def enrich_buildings(building_records: list, use_cache: bool = True, session=None) -> dict:
    """
    Add measured roof geometry to building records from building_context.

    A no-op returning zero calls when the Solar API is disabled, which is the
    default. When enabled, at most SOLAR['max_calls_per_request'] uncached
    lookups are made — the budget guard on top of the flag.

    Each enriched record gains `solar` with the roof planes and, where a
    ground elevation is known, `height_source='solar'` with a measured height.
    Records are mutated in place; the summary reports what actually happened.
    """
    if not enabled():
        return {'enabled': False, 'calls': 0, 'enriched': 0,
                'reason': 'Solar API is disabled (set ENABLE_SOLAR_API to turn it on).'}

    calls = 0
    enriched = 0
    budget = SOLAR['max_calls_per_request']

    for rec in building_records or []:
        centroid = rec.get('centroid')
        if not centroid:
            continue
        lon, lat = centroid
        cached = cache_get(lat, lon) if use_cache else None
        if cached is None:
            if calls >= budget:
                continue          # budget spent — leave the rest un-enriched
            calls += 1
        solar = lookup(lat, lon, use_cache=use_cache, session=session)
        if not solar.get('available'):
            continue
        rec['solar'] = solar
        height = building_height_m(solar, rec.get('ground_elev_m'))
        if height is not None:
            rec['height_m'] = height
            rec['height_source'] = 'solar'
        enriched += 1

    return {'enabled': True, 'calls': calls, 'enriched': enriched, 'reason': None}
