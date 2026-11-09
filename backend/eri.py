"""
NOAA Emergency Response Imagery: post-storm aerial photographs (about 30 cm).

After major US storms NOAA's National Geodetic Survey flies the affected area
and publishes the photos as free public-domain map tiles, one layer per
flight. For a property this answers what radar cannot: is there water against
THIS house. It is also direct visual evidence an adjuster can check.

This module finds which flights cover a point after a given event date, and
fetches tiles through our own server (so password-protected deployments work
and only layers in the NOAA catalogue can be requested).

Honest limits: coverage is patchy (flights follow the worst-hit areas, and a
point can fall between them); water may have receded between the event and the
flight; blank tiles mean "no imagery here", not "dry".
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

CATALOG_PATH = Path(__file__).resolve().parent.parent / "outputs" / "library" / "eri_layers.json"
UA = {"User-Agent": "altis-imagery/1.0 (flood research)"}
BLANK_MAX_BYTES = 400          # NOAA's blank "no imagery" tile is 95 bytes
PROBE_TIMEOUT = 15
_catalog_cache: dict = {}
_probe_cache: dict = {}
_tile_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
_lock = threading.Lock()
TILE_CACHE_MAX = 600


def catalog() -> list[dict]:
    try:
        m = CATALOG_PATH.stat().st_mtime
    except OSError:
        return []
    if _catalog_cache.get("m") != m:
        _catalog_cache["rows"] = json.loads(CATALOG_PATH.read_text())
        _catalog_cache["m"] = m
        _catalog_cache["by_code"] = {r["code"]: r for r in _catalog_cache["rows"]}
    return _catalog_cache["rows"]


def layer(code: str) -> Optional[dict]:
    catalog()
    return _catalog_cache.get("by_code", {}).get(code)


def tile_xy(lat: float, lon: float, z: int) -> tuple[int, int, float, float]:
    """Slippy-map tile and the fractional position of the point inside it."""
    n = 2 ** z
    fx = (lon + 180.0) / 360.0 * n
    fy = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return int(fx), int(fy), fx - int(fx), fy - int(fy)


def _in_bounds(lat, lon, b) -> bool:
    return not b or (b[0] <= lon <= b[2] and b[1] <= lat <= b[3])


def fetch_tile(code: str, z: int, x: int, y: int) -> Optional[tuple[bytes, str]]:
    """Tile bytes and media type for a catalogued layer; None if unknown/failed."""
    lyr = layer(code)
    if not lyr or not (8 <= z <= 21) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
        return None
    key = (code, z, x, y)
    with _lock:
        if key in _tile_cache:
            _tile_cache.move_to_end(key)
            return _tile_cache[key]
    try:
        r = requests.get(f"https://{lyr['host']}/{code}/{z}/{x}/{y}", headers=UA, timeout=PROBE_TIMEOUT)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    body = r.content
    media = "image/png" if body[:4] == b"\x89PNG" else ("image/jpeg" if body[:2] == b"\xff\xd8" else "image/png")
    with _lock:
        _tile_cache[key] = (body, media)
        while len(_tile_cache) > TILE_CACHE_MAX:
            _tile_cache.popitem(last=False)
    return body, media


def _service_error(code: str, z: int, x: int, y: int) -> bool:
    """True when NOAA could not be reached or answered 5xx (as opposed to a clean
    'no imagery' reply), so an outage is never reported as 'no coverage'."""
    lyr = layer(code)
    if not lyr:
        return False
    try:
        r = requests.get(f"https://{lyr['host']}/{code}/{z}/{x}/{y}", headers=UA, timeout=PROBE_TIMEOUT)
    except requests.RequestException:
        return True
    return r.status_code >= 500


def has_imagery(code: str, z: int, x: int, y: int) -> Optional[bool]:
    """True/False for a definite answer, None when the NOAA service failed."""
    key = (code, z, x, y)
    if key in _probe_cache:
        return _probe_cache[key]
    t = fetch_tile(code, z, x, y)
    if t is None:
        if _service_error(code, z, x, y):
            return None            # not cached: retry next time
        _probe_cache[key] = False
        return False
    ok = len(t[0]) > BLANK_MAX_BYTES
    _probe_cache[key] = ok
    return ok


def _best_zoom(code: str, lat: float, lon: float) -> Optional[int]:
    lyr = layer(code) or {}
    top = min(19, int(lyr.get("maxzoom") or 19))
    for z in range(top, 16, -1):
        x, y, _, _ = tile_xy(lat, lon, z)
        if has_imagery(code, z, x, y):
            return z
    return None


def flights_for(lat: float, lon: float, event_date: str, before_days: int = 10,
                after_days: int = 60, max_candidates: int = 60) -> dict:
    """Flights that actually have imagery at this point, earliest first."""
    cat = catalog()
    if not cat:
        return {"flights": [], "checked": 0, "note": "imagery catalogue not built"}
    try:
        d0 = date.fromisoformat(event_date[:10])
    except (TypeError, ValueError):
        return {"flights": [], "checked": 0, "note": "no valid event date"}
    lo, hi = d0 - timedelta(days=before_days), d0 + timedelta(days=after_days)
    cands = [r for r in cat if lo <= date.fromisoformat(r["date"]) <= hi and _in_bounds(lat, lon, r.get("bounds"))]
    cands = cands[:max_candidates]

    def probe(r):
        x, y, _, _ = tile_xy(lat, lon, 17)
        return r, has_imagery(r["code"], 17, x, y)

    with ThreadPoolExecutor(10) as ex:
        probed = list(ex.map(probe, cands))
    failed = sum(1 for _, ok in probed if ok is None)
    hits = [r for r, ok in probed if ok]
    with ThreadPoolExecutor(6) as ex:
        zooms = list(ex.map(lambda r: _best_zoom(r["code"], lat, lon), hits))
    flights = [{"code": r["code"], "storm": r["storm"], "date": r["date"], "zoom": z,
                "days_after_event": (date.fromisoformat(r["date"]) - d0).days}
               for r, z in zip(hits, zooms) if z]
    flights.sort(key=lambda f: f["date"])
    return {"flights": flights, "checked": len(cands),
            "service_error": bool(failed) and not flights,
            "attribution": "NOAA National Geodetic Survey, Emergency Response Imagery (public domain)"}
