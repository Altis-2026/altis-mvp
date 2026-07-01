"""
fema.py — FEMA NFHL flood-zone lookup (Round 7).

Queries FEMA's public National Flood Hazard Layer (ArcGIS REST) for the
regulatory flood zone at a point: is this property in a Special Flood Hazard
Area (SFHA — the 100-year floodplain, zones A*/V*), Zone X, etc. For a
Dispatch property, an SFHA hit usually means an NFIP policy is in play, which
changes how the carrier handles the claim.

US-only by nature of the dataset. Coordinates outside the US bounding check
return zone=None immediately (no wasted network call), and any network/service
failure degrades to zone='unavailable' rather than blocking the analysis —
the flood detection itself never depends on this.
"""
import time
from concurrent.futures import ThreadPoolExecutor

import requests

NFHL_URL = ("https://hazards.fema.gov/arcgis/rest/services/"
            "public/NFHL/MapServer/28/query")
TIMEOUT_S = 6

# Conterminous US + AK/HI/PR rough bounds — cheap pre-filter so non-US
# portfolios (e.g. the Australia demo) never hit the network at all.
_US_BOXES = [
    (24.0, 50.0, -125.5, -66.0),   # CONUS
    (51.0, 72.0, -170.0, -129.0),  # Alaska
    (18.5, 22.5, -161.0, -154.0),  # Hawaii
    (17.5, 18.6, -67.5, -65.2),    # Puerto Rico
]

_cache: dict = {}


def is_us_coord(lat: float, lon: float) -> bool:
    return any(s <= lat <= n and w <= lon <= e for s, n, w, e in _US_BOXES)


def flood_zone_at(lat: float, lon: float) -> dict:
    """
    {'flood_zone': 'AE'|'X'|...|None|'unavailable', 'sfha': bool|None}
    None = outside NFHL coverage (non-US); 'unavailable' = service failure.
    """
    if not is_us_coord(lat, lon):
        return {'flood_zone': None, 'sfha': None}

    key = (round(lat, 4), round(lon, 4))
    if key in _cache:
        return _cache[key]

    try:
        r = requests.get(NFHL_URL, params={
            'geometry':      f"{lon},{lat}",
            'geometryType':  'esriGeometryPoint',
            'inSR':          4326,
            'spatialRel':    'esriSpatialRelIntersects',
            'outFields':     'FLD_ZONE,ZONE_SUBTY,SFHA_TF',
            'returnGeometry': 'false',
            'f':             'json',
        }, timeout=TIMEOUT_S)
        r.raise_for_status()
        feats = r.json().get('features', [])
        if not feats:
            out = {'flood_zone': 'AREA NOT MAPPED', 'sfha': False}
        else:
            attrs = feats[0].get('attributes', {})
            zone = str(attrs.get('FLD_ZONE') or '').strip() or 'UNKNOWN'
            sfha = str(attrs.get('SFHA_TF') or '').strip().upper() == 'T'
            out = {'flood_zone': zone, 'sfha': sfha}
    except Exception:
        out = {'flood_zone': 'unavailable', 'sfha': None}

    _cache[key] = out
    return out


def flood_zones_batch(coords: list, concurrency: int = 8,
                      budget_s: float = 25.0) -> list:
    """
    Look up many (lat, lon) points concurrently with an overall time budget.
    Returns a list of flood_zone_at() dicts in input order. Points not
    resolved inside the budget come back 'unavailable' — the caller never
    waits on FEMA to show flood results.
    """
    results = [None] * len(coords)
    us_idx = [i for i, (lat, lon) in enumerate(coords) if is_us_coord(lat, lon)]
    for i, (lat, lon) in enumerate(coords):
        if i not in us_idx:
            results[i] = {'flood_zone': None, 'sfha': None}

    if not us_idx:
        return results

    deadline = time.time() + budget_s
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(flood_zone_at, *coords[i]): i for i in us_idx}
        for fut, i in futures.items():
            remaining = max(0.1, deadline - time.time())
            try:
                results[i] = fut.result(timeout=remaining)
            except Exception:
                results[i] = {'flood_zone': 'unavailable', 'sfha': None}
    return results
