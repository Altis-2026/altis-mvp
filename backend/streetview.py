"""
streetview.py — Street-level imagery availability for a property.

Answers one question per property: is there a Street View panorama near this
address, and when was it taken? That is all the backend does. The panorama
itself is rendered client-side through the Maps Embed API, which Google prices
as unlimited/no-charge, so no image ever passes through (or is stored by)
Altis.

WHY THIS EARNS ITS PLACE IN A FLOOD PRODUCT

Depth measured above GROUND is not depth above the FINISHED FLOOR, and
severity.py currently runs every property through one generic single-storey
depth-damage curve. Three feet against a slab-on-grade house is an interior
gut; the same three feet against a house on four feet of piers is a wet
crawlspace and no interior loss at all. A desk adjuster can read which one
they are looking at in about two seconds from the street — foundation type,
storey count, whether the HVAC sits at grade. That is the largest remaining
error term in the dollar figure after depth itself, and it is exactly what a
"Remote-Resolve" decision needs to be defensible.

COST: zero. Only the metadata endpoint is called, which Google documents as
consuming no quota ("Street View Static API metadata requests are available at
no charge"). The billable Street View Static *image* endpoint is deliberately
never called.

BASELINE, NOT DAMAGE. Google does not re-drive a neighbourhood after a
hurricane, so the panorama always predates the event. Every record carries its
capture date so the UI can say so plainly; presenting this as damage evidence
would be straightforwardly wrong.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

from backend.database import DB_PATH
from pipeline.config import GOOGLE_MAPS_API_KEY, STREETVIEW

METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"


def available() -> bool:
    """True when a Google Maps key is configured."""
    return bool(GOOGLE_MAPS_API_KEY)


# ── Cache ────────────────────────────────────────────────────────────────────

def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS streetview_cache (
            coord_key   TEXT PRIMARY KEY,
            fetched_at  TEXT,
            payload_json TEXT
        )
    """)


def _coord_key(lat: float, lon: float) -> str:
    """~11 m precision — finer than the panorama spacing, so no false sharing."""
    return f"{lat:.4f},{lon:.4f}"


def cache_get(lat: float, lon: float, ttl_hours: int = None):
    ttl = STREETVIEW['cache_ttl_hours'] if ttl_hours is None else ttl_hours
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT fetched_at, payload_json FROM streetview_cache WHERE coord_key = ?",
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
    if datetime.now(timezone.utc) - fetched > timedelta(hours=ttl):
        return None
    try:
        return json.loads(row[1])
    except (TypeError, ValueError):
        return None


def cache_put(lat: float, lon: float, payload: dict):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_table(conn)
        conn.execute("""
            INSERT OR REPLACE INTO streetview_cache (coord_key, fetched_at, payload_json)
            VALUES (?, ?, ?)
        """, (_coord_key(lat, lon), datetime.now(timezone.utc).isoformat(),
              json.dumps(payload)))
        conn.commit()
    finally:
        conn.close()


# ── Metadata lookup ──────────────────────────────────────────────────────────

def parse_metadata(payload: dict) -> dict:
    """
    Google's metadata response → the record the UI needs.

    `status` is the field that matters: OK means a panorama exists,
    ZERO_RESULTS means none is near enough, and anything else (REQUEST_DENIED,
    OVER_QUERY_LIMIT) is a configuration problem worth surfacing rather than
    silently rendering as "no imagery".
    """
    status = str((payload or {}).get('status', 'UNKNOWN'))
    if status != 'OK':
        return {
            'available': False,
            'status': status,
            'date': None,
            'pano_id': None,
            'lat': None,
            'lon': None,
            # A denied request is our misconfiguration, not an absence of
            # imagery — the two must not look the same to an operator.
            'error': None if status == 'ZERO_RESULTS' else
                     (payload or {}).get('error_message') or status,
        }
    loc = (payload or {}).get('location') or {}
    return {
        'available': True,
        'status': 'OK',
        'date': payload.get('date'),          # 'YYYY-MM' or 'YYYY'
        'pano_id': payload.get('pano_id'),
        'lat': loc.get('lat'),
        'lon': loc.get('lng'),
        'error': None,
    }


def lookup(lat: float, lon: float, use_cache: bool = True, session=None) -> dict:
    """
    Street View availability at a coordinate. Never raises: a network failure
    returns an unavailable record carrying the reason, because imagery is a
    nice-to-have and must not break a property drawer.
    """
    if not available():
        return {'available': False, 'status': 'NO_KEY', 'date': None,
                'pano_id': None, 'lat': None, 'lon': None,
                'error': 'GOOGLE_MAPS_API_KEY is not configured.'}

    if use_cache:
        hit = cache_get(lat, lon)
        if hit is not None:
            return hit

    http = session or requests
    try:
        resp = http.get(METADATA_URL, params={
            'location': f"{lat},{lon}",
            'radius': STREETVIEW['search_radius_m'],
            'key': GOOGLE_MAPS_API_KEY,
        }, timeout=STREETVIEW['timeout_s'])
        payload = resp.json()
    except Exception as e:
        return {'available': False, 'status': 'ERROR', 'date': None,
                'pano_id': None, 'lat': None, 'lon': None,
                'error': f"{type(e).__name__}"}

    record = parse_metadata(payload)
    # Only cache real answers. Caching a transient failure for 30 days would
    # bury a fixable outage.
    if use_cache and record['status'] in ('OK', 'ZERO_RESULTS'):
        cache_put(lat, lon, record)
    return record


def lookup_batch(props: list, use_cache: bool = True, session=None) -> dict:
    """
    Availability for a list of properties. Returns
    {'available': bool (is the feature usable at all), 'properties': {...},
     'summary': {...}}.
    """
    if not available():
        return {'available': False,
                'reason': 'Street View needs GOOGLE_MAPS_API_KEY on the backend.',
                'properties': {}, 'summary': {'requested': len(props or []), 'with_imagery': 0}}

    geocoded = [p for p in props or []
                if p.get('latitude') is not None and p.get('longitude') is not None]
    geocoded = geocoded[:STREETVIEW['max_properties']]

    out = {}
    for p in geocoded:
        try:
            out[str(p['property_id'])] = lookup(
                float(p['latitude']), float(p['longitude']),
                use_cache=use_cache, session=session)
        except (TypeError, ValueError, KeyError):
            continue

    with_imagery = sum(1 for r in out.values() if r['available'])
    denied = [r['error'] for r in out.values()
              if r['status'] not in ('OK', 'ZERO_RESULTS') and r.get('error')]
    return {
        'available': True,
        'reason': denied[0] if denied else None,
        'properties': out,
        'summary': {
            'requested': len(props or []),
            'looked_up': len(out),
            'with_imagery': with_imagery,
        },
    }
