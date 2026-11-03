"""
building_context.py — Per-property building footprint + height, for the 3D
property-inspect view.

Turns a geocoded point into the shape of the structure standing on it: a real
OpenStreetMap building footprint where one can be matched, an estimated wall
height, and an honest label saying where both came from. The globe extrudes
that footprint and draws the modeled flood depth as a water plane cutting
through it, so a claims manager sees "water is 3.2 ft up this house" instead
of reading it off a number.

This module is deliberately VISUALIZATION-ONLY. Nothing here feeds triage,
severity, calibration, or confidence — depth still comes from the SAR/DEM
pipeline exactly as before, and a wrong footprint match can only ever make a
picture wrong, never a dollar figure.

Two honesty rules are enforced in code, not just in the UI:
  - A footprint match beyond BUILDINGS['match_radius_m'] is REJECTED. In a
    dense block the nearest polygon to a drifted geocode is frequently the
    neighbour's house, and drawing the wrong house under a dollar reserve is
    worse than drawing an obvious placeholder box.
  - Every record carries `footprint_source` and `height_source` naming the
    rung of the ladder it came from, so nothing inferred is ever presented as
    surveyed. `height_source='typology'` in particular is a guess from the
    building class and footprint area — see config.BUILDINGS.

Pure functions, no network and no Earth Engine — the Overpass fetch and the
SQLite cache live in backend/buildings.py, so everything here is unit-tested
directly on fixtures.
"""
from __future__ import annotations

import math

try:
    from config import BUILDINGS
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import BUILDINGS

_M_PER_DEG_LAT = 110540.0
_M_PER_DEG_LON_EQ = 111320.0


# ── Geodesy helpers (local equirectangular — exact enough at building scale) ──

def meters_per_degree(lat: float) -> tuple[float, float]:
    """(metres per degree of longitude, per degree of latitude) at `lat`."""
    return (_M_PER_DEG_LON_EQ * math.cos(math.radians(lat)), _M_PER_DEG_LAT)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Planar distance in metres between two nearby points. At the tens-of-metres
    separations this module cares about, the flat-earth error against haversine
    is far below a metre, and it keeps the nearest-footprint scan cheap.
    """
    mx, my = meters_per_degree((lat1 + lat2) / 2.0)
    return math.hypot((lon2 - lon1) * mx, (lat2 - lat1) * my)


def polygon_centroid(ring: list) -> tuple[float, float]:
    """
    (lon, lat) area-weighted centroid of a [[lon, lat], ...] ring. Falls back to
    the vertex mean for degenerate (zero-area) rings so a collapsed polygon
    still yields a usable point instead of dividing by zero.

    The shoelace cross-products are evaluated RELATIVE TO THE FIRST VERTEX.
    Computed on raw lon/lat, the products are ~95 × ~30 while the differences
    that matter across a house are ~1e-7 of that, and float64 cancellation
    walks the centroid of a 20 m building at -95.47° about 14 m away from
    where it belongs — which would mis-join footprints to properties and hang
    every roof off the side of its walls. Shifting to a local origin first
    keeps full precision. `polygon_area_m2` does the same, for the same reason.
    """
    pts = _open_ring(ring)
    if not pts:
        raise ValueError("empty ring")

    def _mean():
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    if len(pts) < 3:
        return _mean()

    ox, oy = pts[0]
    a = cx = cy = 0.0
    for (px0, py0), (px1, py1) in zip(pts, pts[1:] + pts[:1]):
        x0, y0 = px0 - ox, py0 - oy
        x1, y1 = px1 - ox, py1 - oy
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-20:
        return _mean()
    return (ox + cx / (3.0 * a), oy + cy / (3.0 * a))


def polygon_area_m2(ring: list) -> float:
    """
    Shoelace area of a [[lon, lat], ...] ring, in square metres, projected to a
    local metric frame at the ring's own latitude. Sign-independent.
    """
    pts = _open_ring(ring)
    if len(pts) < 3:
        return 0.0
    lat0 = sum(p[1] for p in pts) / len(pts)
    mx, my = meters_per_degree(lat0)
    ox, oy = pts[0]
    a = 0.0
    for (px0, py0), (px1, py1) in zip(pts, pts[1:] + pts[:1]):
        x0, y0 = (px0 - ox) * mx, (py0 - oy) * my
        x1, y1 = (px1 - ox) * mx, (py1 - oy) * my
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def _open_ring(ring: list) -> list:
    """Ring as a list of (lon, lat) tuples with any duplicated closing vertex dropped."""
    pts = [(float(p[0]), float(p[1])) for p in (ring or [])]
    while len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def close_ring(ring: list) -> list:
    """Ring as GeoJSON wants it: a list of [lon, lat] whose last point repeats the first."""
    pts = _open_ring(ring)
    if not pts:
        return []
    return [[x, y] for x, y in pts] + [[pts[0][0], pts[0][1]]]


# ── OSM normalization ────────────────────────────────────────────────────────

def normalize_osm_element(el: dict, cfg=BUILDINGS) -> dict | None:
    """
    One Overpass `way`/`relation` element (from `out geom;`) → a normalized
    footprint dict, or None when it isn't usable as a building:

        {'id', 'ring': [[lon, lat], …closed], 'centroid': (lon, lat),
         'area_m2', 'tags'}

    Rejects rings with too few vertices and footprints outside the configured
    area band — a 6 m² awning or a 40,000 m² mall roof should never win a
    nearest-centroid match against a house.
    """
    geometry = el.get('geometry') or []
    ring = [[float(p['lon']), float(p['lat'])] for p in geometry
            if p.get('lon') is not None and p.get('lat') is not None]
    if len(_open_ring(ring)) < 3:
        return None

    area = polygon_area_m2(ring)
    if not (cfg['min_footprint_area_m2'] <= area <= cfg['max_footprint_area_m2']):
        return None

    return {
        'id':       str(el.get('id', '')),
        'ring':     close_ring(ring),
        'centroid': polygon_centroid(ring),
        'area_m2':  round(area, 1),
        'tags':     dict(el.get('tags') or {}),
    }


def normalize_osm_response(payload: dict, cfg=BUILDINGS) -> list:
    """Whole Overpass JSON response → list of normalized footprints (bad ones dropped)."""
    out = []
    for el in (payload or {}).get('elements', []) or []:
        try:
            f = normalize_osm_element(el, cfg)
        except (TypeError, ValueError, KeyError):
            continue
        if f is not None:
            out.append(f)
    return out


# ── Height estimation ────────────────────────────────────────────────────────

def parse_osm_height(raw) -> float | None:
    """
    An OSM `height` tag → metres, or None if it isn't parseable.

    OSM heights are metres by convention but are written loosely: '12', '12 m',
    '12.5m'. Feet-and-inches notation ("40'6\"") is also accepted since it
    appears in US data. Anything else is refused rather than guessed at.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower().replace(',', '.')
    if not s:
        return None

    # Feet/inches: 40', 40'6", 40 ft
    if "'" in s or s.endswith('ft') or s.endswith('feet'):
        digits = s.replace('ft', ' ').replace('feet', ' ').replace('"', ' ')
        parts = [p for p in digits.replace("'", ' ').split() if p]
        try:
            feet = float(parts[0])
            inches = float(parts[1]) if len(parts) > 1 else 0.0
        except (ValueError, IndexError):
            return None
        return round((feet + inches / 12.0) * 0.3048, 2)

    s = s.replace('metres', '').replace('meters', '').replace('m', '').strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def estimate_height_m(tags: dict, area_m2: float = 0.0, cfg=BUILDINGS) -> tuple[float, str, float]:
    """
    Wall (eave) height in metres for a building, with the rung of the ladder it
    came from. Returns (height_m, source, levels).

    Ladder, best first:
      'osm_height' — explicit height tag (a real measurement)
      'osm_levels' — building:levels × storey height (a real storey count)
      'typology'   — class + footprint area (an inference, labelled as one)

    Height is clamped to [1 storey, max_levels storeys]: OSM carries occasional
    unit errors (a house tagged 300) and a 300 m house in the middle of a
    subdivision destroys the scene.
    """
    tags = tags or {}
    storey = float(cfg['storey_height_m'])
    cap = cfg['max_levels'] * storey

    explicit = parse_osm_height(tags.get('height'))
    if explicit is not None and storey * 0.5 <= explicit <= cap:
        return (round(explicit, 2), 'osm_height', round(explicit / storey, 2))

    levels = _parse_levels(tags.get('building:levels'))
    if levels is not None:
        # Roof levels are habitable space inside the roof, not extra walls, so
        # they are deliberately not added to the wall height.
        levels = min(levels, cfg['max_levels'])
        return (round(levels * storey, 2), 'osm_levels', float(levels))

    levels = _typology_levels(tags.get('building'), area_m2, cfg)
    return (round(levels * storey, 2), 'typology', float(levels))


def _parse_levels(raw) -> int | None:
    """A `building:levels` tag → a positive integer storey count, or None."""
    if raw is None:
        return None
    try:
        v = float(str(raw).strip().split(';')[0].replace(',', '.'))
    except (ValueError, AttributeError):
        return None
    if v < 1 or v > 200:
        return None
    return int(round(v))


def _typology_levels(building_class, area_m2: float, cfg) -> int:
    """Storeys inferred from the OSM building class, floored by footprint size."""
    key = str(building_class or '').strip().lower()
    levels = cfg['levels_by_class'].get(key, cfg['default_levels'])
    try:
        area = float(area_m2 or 0.0)
    except (TypeError, ValueError):
        area = 0.0
    for min_area, floor_levels in sorted(cfg['area_levels_floor'], reverse=True):
        if area >= min_area:
            levels = max(levels, floor_levels)
            break
    return max(1, min(int(levels), cfg['max_levels']))


# ── Footprint ↔ property join ────────────────────────────────────────────────

def match_footprint(lat: float, lon: float, footprints: list, cfg=BUILDINGS):
    """
    Nearest footprint to a geocoded point, subject to the match radius.
    Returns (footprint | None, distance_m | None).

    A point INSIDE a footprint always wins at distance 0, regardless of whose
    centroid is closer — a long L-shaped building can easily have its centroid
    further from the front door than a neighbour's centroid is.
    """
    best = None
    best_d = None
    for f in footprints or []:
        if point_in_ring(lon, lat, f['ring']):
            return (f, 0.0)
        clon, clat = f['centroid']
        d = distance_m(lat, lon, clat, clon)
        if best_d is None or d < best_d:
            best, best_d = f, d

    if best is None or best_d > cfg['match_radius_m']:
        return (None, best_d)
    return (best, round(best_d, 1))


def point_in_ring(lon: float, lat: float, ring: list) -> bool:
    """Ray-casting point-in-polygon against a [[lon, lat], …] ring."""
    pts = _open_ring(ring)
    if len(pts) < 3:
        return False
    inside = False
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        if (y0 > lat) != (y1 > lat):
            x_cross = x0 + (lat - y0) * (x1 - x0) / (y1 - y0)
            if lon < x_cross:
                inside = not inside
    return inside


def placeholder_footprint(lat: float, lon: float, cfg=BUILDINGS) -> list:
    """
    The Tier-1 illustrative box: an axis-aligned rectangle of the configured
    size centred on the property, as a closed [[lon, lat], …] ring. Used
    wherever no real footprint matched, and always labelled as illustrative.
    """
    mx, my = meters_per_degree(lat)
    dx = (cfg['placeholder_width_m'] / 2.0) / max(mx, 1e-9)
    dy = (cfg['placeholder_depth_m'] / 2.0) / max(my, 1e-9)
    return [[lon - dx, lat - dy], [lon + dx, lat - dy], [lon + dx, lat + dy],
            [lon - dx, lat + dy], [lon - dx, lat - dy]]


def building_for_property(prop: dict, footprints: list, cfg=BUILDINGS) -> dict:
    """
    One property + the candidate footprints around it → the record the globe
    renders. Never raises for a bad property and never returns None: a property
    with no match still gets a placeholder box so the 3D view has no holes in
    it, flagged `footprint_source='placeholder'`.

    Keys: property_id, ring (closed [[lon,lat],…]), area_m2, height_m, levels,
    footprint_source ('osm'|'placeholder'), height_source
    ('osm_height'|'osm_levels'|'typology'|'default'), match_distance_m,
    osm_id, building_class.
    """
    lat = float(prop['latitude'])
    lon = float(prop['longitude'])

    match, dist = match_footprint(lat, lon, footprints, cfg)

    if match is None:
        return {
            'property_id':      str(prop.get('property_id', '')),
            'ring':             placeholder_footprint(lat, lon, cfg),
            'area_m2':          round(cfg['placeholder_width_m'] * cfg['placeholder_depth_m'], 1),
            'height_m':         round(cfg['default_levels'] * cfg['storey_height_m'], 2),
            'levels':           float(cfg['default_levels']),
            'footprint_source': 'placeholder',
            'height_source':    'default',
            'match_distance_m': round(dist, 1) if dist is not None else None,
            'osm_id':           None,
            'building_class':   None,
            # Needed by any per-building enrichment (e.g. the Solar API roof
            # lookup) to know where to ask about.
            'centroid':         [lon, lat],
        }

    height, height_source, levels = estimate_height_m(
        match['tags'], match['area_m2'], cfg)
    return {
        'property_id':      str(prop.get('property_id', '')),
        'ring':             match['ring'],
        'area_m2':          match['area_m2'],
        'height_m':         height,
        'levels':           levels,
        'footprint_source': 'osm',
        'height_source':    height_source,
        'match_distance_m': dist,
        'osm_id':           match['id'],
        'building_class':   match['tags'].get('building'),
        'centroid':         [match['centroid'][0], match['centroid'][1]],
    }


def buildings_for_properties(props: list, footprints: list, cfg=BUILDINGS) -> tuple[list, dict]:
    """
    Batch form of `building_for_property`, plus the summary a UI (and an
    operator) needs to judge whether the join is trustworthy for this book.

    Returns (records, summary) where summary carries the match rate, the
    height-source mix, and the median match distance. A low match rate is real
    signal about geocode quality in that portfolio, so it is surfaced rather
    than buried.
    """
    records = []
    for p in props or []:
        if p.get('latitude') is None or p.get('longitude') is None:
            continue
        try:
            records.append(building_for_property(p, footprints, cfg))
        except (TypeError, ValueError, KeyError):
            continue

    matched = [r for r in records if r['footprint_source'] == 'osm']
    dists = sorted(r['match_distance_m'] for r in matched
                   if r['match_distance_m'] is not None)
    height_mix = {}
    for r in records:
        height_mix[r['height_source']] = height_mix.get(r['height_source'], 0) + 1

    return records, {
        'requested':          len(props or []),
        'rendered':           len(records),
        'footprints_matched': len(matched),
        'match_rate':         round(len(matched) / len(records), 3) if records else 0.0,
        'median_match_m':     dists[len(dists) // 2] if dists else None,
        'height_sources':     height_mix,
        'candidates':         len(footprints or []),
    }
