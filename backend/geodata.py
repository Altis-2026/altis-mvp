"""
geodata.py — Free, key-less geospatial data for the physics & intelligence layer.

Every source here is public, needs no API key or Earth Engine credentials, and
is cached on disk, so the same code path serves Railway, offline baking of the
demo events, and tests (which inject fakes):

  source                          used for                         licence
  ─────────────────────────────── ──────────────────────────────── ───────────
  AWS Terrain Tiles (Terrarium)   DEM: router, HAND, footprints    open (Mapzen
                                                                   / USGS 3DEP,
                                                                   SRTM, …)
  JRC Global Surface Water tiles  pre-existing water occurrence    CC-BY 4.0
  NOAA/NWS Stage IV daily QPE     US event rainfall (radar + gauge  public domain
                                  multi-sensor — far better than
                                  satellite-only on extreme storms)
  CHIRPS v2 daily COGs            rainfall elsewhere (same product  public domain
                                  the GEE pipeline uses)
  Copernicus Data Space STAC      exact Sentinel-1 pass times      open
  OpenStreetMap Overpass          road network                     ODbL
  NHC HURDAT2 best track          wind radii (see wind_field.py)   public domain

All bboxes in this module are [west, south, east, north].
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from pipeline.cogread import CogReader, http_fetcher
from pipeline.terrain import Grid, decode_terrarium, mosaic_tiles, zoom_for_resolution

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.getenv('DATA_DIR', str(BASE_DIR))) / 'cache' / 'geodata'

TERRARIUM_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
JRC_OCC_URL = ('https://storage.googleapis.com/global-surface-water/tiles2021/'
               'occurrence/{z}/{x}/{y}.png')
JRC_MAX_ZOOM = 13
CHIRPS_URL = ('https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/cogs/p05/'
              '{y}/chirps-v2.0.{y}.{m:02d}.{d:02d}.cog')
STAGEIV_URL = ('https://water.noaa.gov/resources/downloads/precip/stageIV/'
               '{y}/{m:02d}/{d:02d}/nws_precip_1day_{y}{m:02d}{d:02d}_conus.tif')
S1_STAC_URL = 'https://stac.dataspace.copernicus.eu/v1/collections/sentinel-1-grd/items'
OVERPASS_ENDPOINTS = [
    'https://overpass.kumi.systems/api/interpreter',
    'https://overpass-api.de/api/interpreter',
    'https://overpass.private.coffee/api/interpreter',
]
UA = {'User-Agent': 'Altis/1.0 (flood triage research)'}


class GeoDataError(RuntimeError):
    pass


# ── Disk cache ───────────────────────────────────────────────────────────────

def _cache_path(kind: str, key: str, ext: str) -> Path:
    p = CACHE_DIR / kind / f'{key}.{ext}'
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _get(url, session=None, timeout=30, retries=2, **kw):
    http = session or requests
    last = None
    for attempt in range(retries + 1):
        try:
            r = http.get(url, timeout=timeout, headers=UA, **kw)
            if r.status_code == 429 and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return r
        except Exception as e:                       # network/DNS/TLS/timeout
            last = e
            time.sleep(1 + attempt)
    raise GeoDataError(f'{url.split("/")[2]}: {type(last).__name__}')


# ── DEM ──────────────────────────────────────────────────────────────────────

def terrarium_tile(z, x, y, session=None):
    path = _cache_path('terrarium', f'{z}_{x}_{y}', 'npy')
    if path.exists():
        return np.load(path)
    r = _get(TERRARIUM_URL.format(z=z, x=x, y=y), session=session)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise GeoDataError(f'terrain tile HTTP {r.status_code}')
    from PIL import Image
    arr = decode_terrarium(np.asarray(Image.open(io.BytesIO(r.content)).convert('RGB')))
    arr = arr.astype(np.float32)
    np.save(path, arr)
    return arr


def dem_grid(bbox, res_m: float = 10.0, session=None) -> Grid:
    """Terrain for a bbox at roughly res_m (never finer than the tiles)."""
    w, s, e, n = bbox
    z = zoom_for_resolution(res_m, (s + n) / 2)
    grid = mosaic_tiles(bbox, z, lambda zz, xx, yy: terrarium_tile(zz, xx, yy, session))
    if not np.isfinite(grid.data).any():
        raise GeoDataError('no terrain coverage for this area')
    return grid


def dem_at_resolution(bbox, res_m: float, session=None) -> Grid:
    """DEM resampled onto a regular grid of ~res_m cells (router/HAND input)."""
    import math
    base = dem_grid(bbox, res_m=min(res_m, 30.0), session=session)
    lat = (bbox[1] + bbox[3]) / 2
    rx = res_m / (111320.0 * math.cos(math.radians(lat)))
    ry = res_m / 110540.0
    if rx <= base.dx * 1.05:
        return base
    # Block-average rather than point-sample when coarsening, so a narrow
    # channel does not vanish between samples.
    fx = max(1, int(round(rx / base.dx)))
    fy = max(1, int(round(ry / base.dy)))
    d = base.data
    h, wd = (d.shape[0] // fy) * fy, (d.shape[1] // fx) * fx
    blk = d[:h, :wd].reshape(h // fy, fy, wd // fx, fx)
    return Grid(np.nanmean(blk, axis=(1, 3)), base.west, base.north,
                base.dx * fx, base.dy * fy)


# ── JRC surface-water occurrence ─────────────────────────────────────────────

def jrc_tile(z, x, y, session=None):
    """Occurrence 0–100 (% of valid months with water, 1984–2021)."""
    path = _cache_path('jrc', f'{z}_{x}_{y}', 'npy')
    if path.exists():
        return np.load(path)
    r = _get(JRC_OCC_URL.format(z=z, x=x, y=y), session=session)
    if r.status_code == 404 and z <= JRC_MAX_ZOOM:
        arr = np.zeros((256, 256), np.float32)        # no water ever mapped here
    elif r.status_code != 200:
        raise GeoDataError(f'JRC tile HTTP {r.status_code}')
    else:
        from PIL import Image
        rgba = np.asarray(Image.open(io.BytesIO(r.content)).convert('RGBA')).astype(np.float32)
        # The occurrence palette runs red (rare) → blue (permanent) with
        # R + B ≈ 254, so blue encodes occurrence to ~1% precision.
        arr = np.where(rgba[..., 3] > 0, np.clip(rgba[..., 2] / 2.54, 0, 100), 0.0)
        arr = arr.astype(np.float32)
    np.save(path, arr)
    return arr


def jrc_occurrence_grid(bbox, session=None) -> Grid:
    return mosaic_tiles(bbox, JRC_MAX_ZOOM, lambda z, x, y: jrc_tile(z, x, y, session))


# ── CHIRPS rainfall ──────────────────────────────────────────────────────────

def chirps_day(day: date, bbox, session=None) -> Grid:
    """CHIRPS daily precipitation (mm) over a bbox, via HTTP range reads."""
    w, s, e, n = bbox
    key = hashlib.md5(f'{day}{w:.3f}{s:.3f}{e:.3f}{n:.3f}'.encode()).hexdigest()[:16]
    path = _cache_path('chirps', key, 'npz')
    if path.exists():
        z = np.load(path)
        return Grid(z['data'], *z['geo'])
    url = CHIRPS_URL.format(y=day.year, m=day.month, d=day.day)
    try:
        reader = CogReader(http_fetcher(url, session=session))
        arr, (west, north, rx, ry) = reader.read_window(w, s, e, n, pad=1)
    except Exception as ex:
        raise GeoDataError(f'CHIRPS {day}: {ex}')
    arr[arr < -900] = np.nan                            # ocean / nodata
    np.savez(path, data=arr, geo=np.array([west, north, rx, ry]))
    return Grid(arr, west, north, rx, ry)


def chirps_series(bbox, start: date, end: date, session=None) -> list[tuple[date, Grid]]:
    out = []
    d = start
    while d <= end:
        out.append((d, chirps_day(d, bbox, session)))
        d += timedelta(days=1)
    return out


# ── NOAA Stage IV (US) ───────────────────────────────────────────────────────

def in_stageiv_domain(lon, lat) -> bool:
    return 24.0 <= lat <= 50.0 and -125.0 <= lon <= -66.0


def _hrap_xy(lon, lat):
    """HRAP polar stereographic (sphere R=6371200, true at 60°N, λ0=−105°)."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    R = 6371200.0
    rho = R * (1 + np.sin(np.radians(60.0))) * np.tan(np.pi / 4 - np.radians(lat) / 2)
    lam = np.radians(lon + 105.0)
    return rho * np.sin(lam), -rho * np.cos(lam)


def stageiv_day_points(day: date, lons, lats, session=None) -> np.ndarray:
    """
    24-h precipitation (mm) ending 12 UTC on `day` at each point, from the
    NWS multi-sensor (radar + gauge) Stage IV analysis.
    """
    lons, lats = np.asarray(lons, float), np.asarray(lats, float)
    key = hashlib.md5(f'st4{day}{lons.min():.3f}{lats.min():.3f}{lons.max():.3f}'
                      f'{lats.max():.3f}'.encode()).hexdigest()[:16]
    path = _cache_path('stageiv', key, 'npz')
    url = STAGEIV_URL.format(y=day.year, m=day.month, d=day.day)
    if path.exists():
        z = np.load(path)
        win, r0, c0, geo = z['data'], int(z['r0']), int(z['c0']), z['geo']
    else:
        try:
            reader = CogReader(http_fetcher(url, session=session))
        except Exception as ex:
            raise GeoDataError(f'Stage IV {day}: {ex}')
        x, y = _hrap_xy(lons, lats)
        cols = (x - reader.origin_x) / reader.res_x
        rows = (reader.origin_y - y) / reader.res_y
        win, (r0, c0) = reader.read_rows_cols(int(rows.min()) - 2, int(rows.max()) + 3,
                                              int(cols.min()) - 2, int(cols.max()) + 3)
        geo = np.array([reader.origin_x, reader.origin_y, reader.res_x, reader.res_y])
        np.savez(path, data=win, r0=r0, c0=c0, geo=geo)
    x, y = _hrap_xy(lons, lats)
    ox, oy, rx, ry = geo
    cc = np.floor((x - ox) / rx).astype(int) - c0
    rr = np.floor((oy - y) / ry).astype(int) - r0
    ok = (rr >= 0) & (rr < win.shape[0]) & (cc >= 0) & (cc < win.shape[1])
    out = np.full(lons.shape, np.nan)
    out[ok] = win[rr[ok], cc[ok]] * 25.4
    return out


# ── Sentinel-1 acquisition times ─────────────────────────────────────────────

def s1_acquisitions(bbox, start: str, end: str, session=None) -> list[dict]:
    """Every Sentinel-1 GRD scene intersecting bbox in [start, end] (ISO dates)."""
    w, s, e, n = bbox
    key = hashlib.md5(f's1{w}{s}{e}{n}{start}{end}'.encode()).hexdigest()[:16]
    path = _cache_path('s1', key, 'json')
    if path.exists():
        return json.loads(path.read_text())
    r = _get(S1_STAC_URL, session=session, timeout=40, params={
        'bbox': f'{w},{s},{e},{n}',
        'datetime': f'{start}T00:00:00Z/{end}T23:59:59Z', 'limit': 100})
    if r.status_code != 200:
        raise GeoDataError(f'Copernicus STAC HTTP {r.status_code}')
    items = []
    for f in r.json().get('features', []):
        p = f.get('properties', {})
        items.append({'datetime': p.get('datetime'), 'orbit_state': p.get('sat:orbit_state'),
                      'mode': p.get('sar:instrument_mode'), 'id': f.get('id')})
    # One acquisition appears as several products (GRD, COG, reprocessings);
    # keep one entry per acquisition second.
    seen, uniq = set(), []
    for it in sorted(items, key=lambda i: i['datetime'] or ''):
        k = (it['datetime'] or '')[:19]
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    path.write_text(json.dumps(uniq))
    return uniq


# ── OSM roads ────────────────────────────────────────────────────────────────

DRIVABLE = ('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified',
            'residential', 'living_street', 'motorway_link', 'trunk_link',
            'primary_link', 'secondary_link', 'tertiary_link', 'service')


def osm_roads(bbox, session=None, timeout_s: float = 120, attempts: int = 3) -> list[dict]:
    """
    Drivable OSM ways: [{'id', 'highway', 'coords': [[lon, lat], …]}].
    Batch jobs keep the patient defaults; interactive callers (the evidence
    pack's site map) pass a short timeout and a single attempt.
    """
    w, s, e, n = bbox
    key = hashlib.md5(f'roads{w:.4f}{s:.4f}{e:.4f}{n:.4f}'.encode()).hexdigest()[:16]
    path = _cache_path('roads', key, 'json')
    if path.exists():
        return json.loads(path.read_text())
    # A cached query whose box contains this one answers it without a network
    # call (the site map inside an event's already-fetched road network).
    index_path = _cache_path('roads', 'index', 'json')
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    for k, bb in index.items():
        if bb[0] <= w and bb[1] <= s and bb[2] >= e and bb[3] >= n and _cache_path('roads', k, 'json').exists():
            ways = json.loads(_cache_path('roads', k, 'json').read_text())
            return [wy for wy in ways if any(w <= lo <= e and s <= la <= n for lo, la in wy['coords'])]
    hw = '|'.join(DRIVABLE)
    q = (f'[out:json][timeout:90];way["highway"~"^({hw})$"]'
         f'({s:.6f},{w:.6f},{n:.6f},{e:.6f});out geom;')
    last = 'no endpoint attempted'
    for attempt in range(attempts):
        for ep in OVERPASS_ENDPOINTS[:3 if attempts > 1 else 1]:
            try:
                r = (session or requests).post(ep, data={'data': q}, timeout=timeout_s, headers=UA)
            except Exception as ex:
                last = f'{ep.split("/")[2]}: {type(ex).__name__}'
                continue
            if r.status_code != 200:
                last = f'{ep.split("/")[2]}: HTTP {r.status_code}'
                continue
            try:
                els = r.json().get('elements', [])
            except ValueError:
                last = f'{ep.split("/")[2]}: non-JSON'
                continue
            ways = [{'id': el['id'], 'highway': el.get('tags', {}).get('highway'),
                     'coords': [[g['lon'], g['lat']] for g in el.get('geometry', [])]}
                    for el in els if el.get('type') == 'way' and el.get('geometry')]
            path.write_text(json.dumps(ways))
            index[key] = [w, s, e, n]
            index_path.write_text(json.dumps(index))
            return ways
        if attempt + 1 < attempts:
            time.sleep(2 * (attempt + 1))
    raise GeoDataError(f'OSM road lookup failed — {last}')


# ── NHC best track (full archive, for storm auto-detection) ────────────────

HURDAT_INDEX = 'https://www.nhc.noaa.gov/data/hurdat/'


def _latest_hurdat_urls(session=None):
    r = _get(HURDAT_INDEX, session=session, timeout=30)
    if r.status_code != 200:
        raise GeoDataError(f'NHC index HTTP {r.status_code}')
    import re
    names = set(re.findall(r'hurdat2-(?:1851|nepac-1949)-[0-9-]+\.txt', r.text))
    atl = sorted(n for n in names if n.startswith('hurdat2-1851'))
    pac = sorted(n for n in names if n.startswith('hurdat2-nepac'))

    def newest(ns):   # filenames end in the release date MMDDYY(YY)
        def key(n):
            tail = n.rsplit('-', 1)[-1].split('.')[0]
            return (tail[-4:] if len(tail) == 8 else '20' + tail[-2:], tail[:4])
        return max(ns, key=key) if ns else None
    return [HURDAT_INDEX + n for n in (newest(atl), newest(pac)) if n]


def hurdat_archive(session=None) -> str:
    path = _cache_path('hurdat', 'archive', 'txt')
    if path.exists() and time.time() - path.stat().st_mtime < 30 * 86400:
        return path.read_text()
    parts = []
    for url in _latest_hurdat_urls(session):
        r = _get(url, session=session, timeout=90)
        if r.status_code == 200:
            parts.append(r.text.strip())
    if not parts:
        raise GeoDataError('could not download HURDAT2')
    text = '\n'.join(parts) + '\n'
    path.write_text(text)
    return text


def find_tropical_cyclone(bbox, start: date, end: date, session=None, radius_nm: float = 250.0):
    """
    The Atlantic / east-Pacific tropical cyclone that passed within radius_nm
    of the study area during [start, end] at ≥ 34 kt, or None. Picks the storm
    with the strongest wind while within range.
    """
    from pipeline.wind_field import parse_hurdat2, _gc
    w, s, e, n = bbox
    cx, cy = (w + e) / 2, (s + n) / 2
    if not (-180 <= cx <= 0 or cx >= 170) or cy < 0:
        return None                       # outside NHC's basins
    t0 = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    t1 = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    storms = parse_hurdat2(hurdat_archive(session))
    best = None
    for sid, st in storms.items():
        fx = st['fixes']
        if not fx or fx[-1]['time'] < t0 or fx[0]['time'] > t1:
            continue
        peak = 0
        for f in fx:
            if t0 <= f['time'] <= t1 and (f['vmax'] or 0) >= 34:
                d, _ = _gc(f['lat'], f['lon'], cy, cx)
                if d <= radius_nm:
                    peak = max(peak, f['vmax'])
        if peak and (best is None or peak > best['peak_kt']):
            best = {'id': sid, 'name': st['name'], 'peak_kt': peak, 'storm': st}
    return best


# ── Rainfall forecast (Open-Meteo, free, no key) ───────────────────────────

FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'


def forecast_rain(lat: float, lon: float, days: int = 7, session=None) -> dict:
    """
    Daily precipitation forecast (mm) at a point from Open-Meteo's blend of
    national weather models, with the heaviest 3-day window and 7-day total.
    """
    r = _get(FORECAST_URL, session=session, timeout=30, params={
        'latitude': round(lat, 4), 'longitude': round(lon, 4), 'daily': 'precipitation_sum',
        'forecast_days': days, 'timezone': 'UTC'})
    if r.status_code != 200:
        raise GeoDataError(f'forecast HTTP {r.status_code}')
    d = r.json().get('daily') or {}
    vals = [float(v or 0) for v in d.get('precipitation_sum') or []]
    if not vals:
        raise GeoDataError('forecast returned no data')
    r3 = max(sum(vals[i:i + 3]) for i in range(max(1, len(vals) - 2)))
    return {'days': d.get('time'), 'daily_mm': vals, 'max_3day_mm': round(r3, 1),
            'total_mm': round(sum(vals), 1), 'source': 'Open-Meteo forecast (national weather model blend)'}


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace('Z', '+00:00'))
