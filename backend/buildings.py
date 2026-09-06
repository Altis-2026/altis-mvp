"""
buildings.py — Building footprints for the 3D property-inspect view.

The one impure half of the building-context feature: fetches OpenStreetMap
building polygons from Overpass for a viewport, caches them in SQLite, and
hands them to pipeline.building_context (pure) for the property join.

Design constraints this file exists to satisfy:

  - Overpass is a free, shared, volunteer-run service. A demo that hammers it
    per pan/zoom is both rude and slow, so one request covers a whole viewport,
    results are cached by rounded bbox for BUILDINGS['cache_ttl_hours'], and
    oversized bboxes are refused outright instead of served slowly.
  - It is also unreliable at demo time (the main endpoint returns 504 under
    load — observed while building this). Mirrors are tried in order, and a
    total failure degrades to `available: False` with a readable reason, which
    the globe renders as Tier-1 placeholder boxes. The 3D view never goes
    blank because OSM had a bad minute.
  - Nothing here can affect triage. Footprints are scenery.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

from backend.database import DB_PATH
from pipeline.building_context import (
    buildings_for_properties, normalize_osm_response,
)
from pipeline.config import BUILDINGS


class BuildingFetchError(Exception):
    """Raised for actionable, user-facing footprint-fetch failures."""


# ── Viewport helpers ─────────────────────────────────────────────────────────

def bbox_for_properties(props: list, pad_m: float = 60.0) -> list:
    """
    [south, west, north, east] enclosing the properties, padded by `pad_m` so a
    house whose footprint straddles the edge of the viewport is still returned
    whole. Overpass takes its bbox in this (lat, lon) order, not GeoJSON's.
    """
    lats = [float(p['latitude']) for p in props
            if p.get('latitude') is not None]
    lons = [float(p['longitude']) for p in props
            if p.get('longitude') is not None]
    if not lats or not lons:
        raise BuildingFetchError("No geocoded properties in the request.")

    pad_lat = pad_m / 110540.0
    # Longitude padding widens toward the poles; 1e-6 guards the degenerate
    # cos(lat)=0 case at the pole itself.
    import math
    pad_lon = pad_m / max(111320.0 * math.cos(math.radians(sum(lats) / len(lats))), 1e-6)
    return [min(lats) - pad_lat, min(lons) - pad_lon,
            max(lats) + pad_lat, max(lons) + pad_lon]


def bbox_span_deg(bbox: list) -> float:
    """Largest side of a [s, w, n, e] bbox, in degrees."""
    return max(bbox[2] - bbox[0], bbox[3] - bbox[1])


def _cache_key(bbox: list) -> str:
    """
    Cache key for a bbox, rounded to ~3 decimal places (~100 m). Panning a few
    metres reuses the cached tile instead of re-querying Overpass; the padding
    in bbox_for_properties covers the rounding slack.
    """
    return ",".join(f"{round(v, 3):.3f}" for v in bbox)


# ── SQLite cache ─────────────────────────────────────────────────────────────

def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS building_footprint_cache (
            bbox_key    TEXT PRIMARY KEY,
            fetched_at  TEXT,
            source      TEXT,
            payload_json TEXT
        )
    """)


def cache_get(bbox: list, ttl_hours: int = None):
    """Cached footprints for a bbox, or None when absent or stale."""
    ttl = BUILDINGS['cache_ttl_hours'] if ttl_hours is None else ttl_hours
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT fetched_at, payload_json FROM building_footprint_cache WHERE bbox_key = ?",
            (_cache_key(bbox),)).fetchone()
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


def cache_put(bbox: list, footprints: list, source: str = 'overpass'):
    """Store footprints for a bbox, replacing any previous entry."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_table(conn)
        conn.execute("""
            INSERT OR REPLACE INTO building_footprint_cache
            (bbox_key, fetched_at, source, payload_json) VALUES (?, ?, ?, ?)
        """, (_cache_key(bbox), datetime.now(timezone.utc).isoformat(),
              source, json.dumps(footprints)))
        conn.commit()
    finally:
        conn.close()


# ── Overpass ─────────────────────────────────────────────────────────────────

def overpass_query(bbox: list) -> str:
    """
    Overpass QL for every building way/relation in a [s, w, n, e] bbox.
    `out geom;` returns inline coordinates, so no second node lookup is needed.
    """
    s, w, n, e = bbox
    timeout = BUILDINGS['overpass_timeout_s']
    return (f'[out:json][timeout:{timeout}];'
            f'(way["building"]({s:.6f},{w:.6f},{n:.6f},{e:.6f});'
            f'relation["building"]({s:.6f},{w:.6f},{n:.6f},{e:.6f}););'
            f'out geom;')


def fetch_overpass(bbox: list, session=None) -> list:
    """
    Building footprints for a bbox, straight from Overpass (no cache).
    Tries each configured mirror in turn, then retries the whole ladder — the
    public endpoints return 429/504 under load rather than failing outright.
    Raises BuildingFetchError when every attempt fails.
    """
    if bbox_span_deg(bbox) > BUILDINGS['max_bbox_deg']:
        raise BuildingFetchError(
            f"Viewport too large for footprint lookup "
            f"({bbox_span_deg(bbox):.2f}° > {BUILDINGS['max_bbox_deg']}°). "
            f"Zoom in to a neighbourhood.")

    http = session or requests
    query = overpass_query(bbox)
    last_error = "no endpoint attempted"

    for attempt in range(BUILDINGS['overpass_retries'] + 1):
        for endpoint in BUILDINGS['overpass_endpoints']:
            try:
                resp = http.get(endpoint, params={'data': query},
                                timeout=BUILDINGS['overpass_timeout_s'] + 10,
                                headers={'User-Agent': 'Altis/1.0 (flood triage)'})
            except Exception as e:                    # network/DNS/TLS/timeout
                last_error = f"{_host(endpoint)}: {type(e).__name__}"
                continue

            if resp.status_code != 200:
                last_error = f"{_host(endpoint)}: HTTP {resp.status_code}"
                continue
            try:
                payload = resp.json()
            except ValueError:
                # Overpass returns an HTML error page when it is overloaded.
                last_error = f"{_host(endpoint)}: non-JSON response (overloaded)"
                continue

            return normalize_osm_response(payload)

        if attempt < BUILDINGS['overpass_retries']:
            time.sleep(1.5 * (attempt + 1))

    raise BuildingFetchError(f"OpenStreetMap building lookup failed — {last_error}")


def _host(url: str) -> str:
    return url.split('//')[-1].split('/')[0]


def load_footprints(bbox: list, use_cache: bool = True, session=None) -> tuple[list, str]:
    """
    Footprints for a bbox, cache-first. Returns (footprints, source) where
    source is 'cache' or 'overpass'.
    """
    if use_cache:
        cached = cache_get(bbox)
        if cached is not None:
            return cached, 'cache'

    footprints = fetch_overpass(bbox, session=session)
    if use_cache:
        cache_put(bbox, footprints)
    return footprints, 'overpass'


# ── Entry point used by the API ──────────────────────────────────────────────

def building_context(props: list, use_cache: bool = True, session=None) -> dict:
    """
    Building records for a list of geocoded properties, ready for the globe.

    Always returns a renderable answer. When Overpass is unreachable or the
    viewport is too large, `available` is False and every property still gets a
    labelled placeholder box, with `reason` explaining what happened — the 3D
    view degrades to Tier-1 boxes rather than going blank.
    """
    geocoded = [p for p in props or []
                if p.get('latitude') is not None and p.get('longitude') is not None]
    if not geocoded:
        return {'available': False, 'reason': 'No geocoded properties in the request.',
                'buildings': [], 'summary': {'requested': len(props or []), 'rendered': 0}}

    if len(geocoded) > BUILDINGS['max_properties']:
        geocoded = geocoded[:BUILDINGS['max_properties']]

    bbox = bbox_for_properties(geocoded)

    try:
        footprints, source = load_footprints(bbox, use_cache=use_cache, session=session)
    except BuildingFetchError as e:
        records, summary = buildings_for_properties(geocoded, [])
        return {'available': False, 'reason': str(e), 'footprint_source': 'placeholder',
                'buildings': records, 'summary': summary, 'bbox': bbox}

    records, summary = buildings_for_properties(geocoded, footprints)
    return {'available': True, 'reason': None, 'footprint_source': source,
            'buildings': records, 'summary': summary, 'bbox': bbox}
