"""
Event watch and pre-event exposure: what is happening now, and which insured
properties are in its path.

Sources (free, no account):
  - NOAA National Hurricane Center CurrentStorms.json: every active tropical
    cyclone with its official 5-day forecast cone (KML) and track.
  - NHC GIS archive: the same cone as a shapefile for any past advisory,
    so the exposure logic can be replayed on a historical storm.
  - OpenFEMA disaster declarations: flood-type major disasters declared
    recently, with the designated counties.

Exposure is geometric: a property is "in the cone" if it lies inside the
NHC 5-day cone polygon. The NHC states the storm CENTRE stays inside the cone
only about 60-70% of the time, and impacts (rain, surge, wind) reach far
outside it, so this is a planning view for pre-positioning adjusters, not a
loss estimate.
"""
from __future__ import annotations

import io
import math
import re
import struct
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

NHC_CURRENT = "https://www.nhc.noaa.gov/CurrentStorms.json"
NHC_ARCHIVE = "https://www.nhc.noaa.gov/gis/forecast/archive/{storm}_5day_{adv}.zip"
FEMA_DECL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
_TIMEOUT = 30
_MEM: dict = {}
_TTL = 15 * 60
CLASS_NAMES = {"HU": "Hurricane", "TS": "Tropical Storm", "TD": "Tropical Depression",
               "STS": "Subtropical Storm", "STD": "Subtropical Depression", "PTC": "Potential Tropical Cyclone",
               "PC": "Post-tropical Cyclone"}


def _cached(key, fn):
    k = (key, int(time.time() // _TTL))
    if k not in _MEM:
        _MEM[k] = fn()
    return _MEM[k]


# ── Geometry readers (stdlib only) ──────────────────────────────────────────

def polygons_from_kml(kml_text: str) -> list[list[tuple[float, float]]]:
    """Outer rings of every <Polygon> in a KML document, as [(lon, lat), ...]."""
    rings = []
    root = ET.fromstring(kml_text.encode() if isinstance(kml_text, str) else kml_text)
    for poly in root.iter():
        if not poly.tag.endswith("Polygon"):
            continue
        for el in poly.iter():
            if el.tag.endswith("outerBoundaryIs"):
                for c in el.iter():
                    if c.tag.endswith("coordinates") and c.text:
                        pts = []
                        for tok in c.text.split():
                            parts = tok.split(",")
                            if len(parts) >= 2:
                                pts.append((float(parts[0]), float(parts[1])))
                        if len(pts) >= 3:
                            rings.append(pts)
    return rings


def polygons_from_shp(shp_bytes: bytes) -> list[list[tuple[float, float]]]:
    """Rings of every polygon record in an ESRI .shp (shape type 5/15/25)."""
    rings, pos = [], 100
    while pos + 8 <= len(shp_bytes):
        _, length_words = struct.unpack(">ii", shp_bytes[pos:pos + 8])
        content = shp_bytes[pos + 8:pos + 8 + length_words * 2]
        pos += 8 + length_words * 2
        if len(content) < 44:
            continue
        stype = struct.unpack("<i", content[:4])[0]
        if stype not in (5, 15, 25):
            continue
        nparts, npts = struct.unpack("<ii", content[36:44])
        parts = list(struct.unpack(f"<{nparts}i", content[44:44 + 4 * nparts]))
        off = 44 + 4 * nparts
        pts = [struct.unpack("<dd", content[off + 16 * i: off + 16 * i + 16]) for i in range(npts)]
        parts.append(npts)
        for a, b in zip(parts, parts[1:]):
            if b - a >= 3:
                rings.append([(float(x), float(y)) for x, y in pts[a:b]])
    return rings


def point_in_ring(lon: float, lat: float, ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def in_any(lon, lat, rings) -> bool:
    return any(point_in_ring(lon, lat, r) for r in rings)


# ── Active storms ────────────────────────────────────────────────────────────

def _fetch_kmz_polygons(url: str):
    r = requests.get(url, timeout=_TIMEOUT)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    kml = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
    return polygons_from_kml(z.read(kml).decode("utf-8", "replace")) if kml else []


def active_storms(with_cones: bool = True) -> list[dict]:
    def load():
        d = requests.get(NHC_CURRENT, timeout=_TIMEOUT).json()
        out = []
        for s in d.get("activeStorms", []):
            rec = {
                "id": s.get("id"), "name": s.get("name"),
                "classification": CLASS_NAMES.get(s.get("classification"), s.get("classification")),
                "intensity_kt": _num(s.get("intensity")), "pressure_mb": _num(s.get("pressure")),
                "lat": s.get("latitudeNumeric"), "lon": s.get("longitudeNumeric"),
                "movement": f"{s.get('movementDir')}° at {s.get('movementSpeed')} mph"
                            if s.get("movementDir") is not None else None,
                "last_update": s.get("lastUpdate"),
                "advisory": (s.get("trackCone") or {}).get("advNum"),
                "cone_kmz": (s.get("trackCone") or {}).get("kmzFile"),
                "surge_flooding_gis": (s.get("potentialStormSurgeFloodingGIS") or {}).get("zipFile"),
            }
            if with_cones and rec["cone_kmz"]:
                try:
                    rec["cone"] = _fetch_kmz_polygons(rec["cone_kmz"])
                except (requests.RequestException, zipfile.BadZipFile, ET.ParseError):
                    rec["cone"] = []
            out.append(rec)
        return out
    return _cached(("storms", with_cones), load)


def archived_cone(storm_id: str, advisory: str) -> list:
    """Historical 5-day cone, e.g. ('al092017', '020') for Harvey advisory 20."""
    url = NHC_ARCHIVE.format(storm=storm_id.lower(), adv=str(advisory).zfill(3))
    r = requests.get(url, timeout=_TIMEOUT)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    shp = next((n for n in z.namelist() if n.endswith("_pgn.shp")), None)
    return polygons_from_shp(z.read(shp)) if shp else []


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Recent FEMA flood-type declarations ─────────────────────────────────────

def recent_declarations(days: int = 60) -> list[dict]:
    def load():
        since = (date.today() - timedelta(days=days)).isoformat()
        flt = (f"declarationType eq 'DR' and declarationDate ge '{since}' and (incidentType eq 'Flood' or "
               "incidentType eq 'Hurricane' or incidentType eq 'Tropical Storm' or incidentType eq 'Coastal Storm')")
        rows = requests.get(FEMA_DECL, params={"$filter": flt, "$top": 5000}, timeout=90
                            ).json().get("DisasterDeclarationsSummaries", [])
        by = {}
        for r in rows:
            e = by.setdefault(r["disasterNumber"], {
                "disaster_number": r["disasterNumber"], "title": r["declarationTitle"].title(),
                "incident_type": r["incidentType"], "declared": r["declarationDate"][:10],
                "incident_start": r["incidentBeginDate"][:10],
                "incident_end": (r.get("incidentEndDate") or "")[:10] or None,
                "states": set(), "n_counties": 0})
            e["states"].add(r["state"])
            e["n_counties"] += 1
        out = sorted(by.values(), key=lambda e: e["declared"], reverse=True)
        for e in out:
            e["states"] = sorted(e["states"])
        return out
    return _cached(("decl", days), load)


# ── Portfolio exposure ───────────────────────────────────────────────────────

def _money(v):
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return 0.0


def exposure_in_rings(properties: list[dict], rings) -> dict:
    hits, tiv = [], 0.0
    for p in properties:
        lat, lon = p.get("latitude"), p.get("longitude")
        if lat is None or lon is None:
            continue
        if in_any(float(lon), float(lat), rings):
            v = _money(p.get("coverage_amount") or p.get("tiv") or p.get("total_insured_value"))
            tiv += v
            hits.append({"property_id": p.get("property_id"), "address": p.get("address"),
                         "latitude": lat, "longitude": lon, "coverage": v or None})
    return {"n_in_cone": len(hits), "n_total": len(properties), "tiv_in_cone": round(tiv),
            "properties": hits[:500]}


def portfolio_storm_exposure(properties: list[dict]) -> dict:
    storms = active_storms(with_cones=True)
    out = []
    for s in storms:
        cone = s.get("cone") or []
        exp = exposure_in_rings(properties, cone) if cone else None
        out.append({**{k: v for k, v in s.items() if k != "cone"}, "has_cone": bool(cone), "exposure": exp})
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "storms": out,
            "note": ("Properties inside the NHC 5-day forecast cone. The storm centre stays inside the cone "
                     "only about 60-70% of the time and rain, surge and wind reach far beyond it: a planning "
                     "view for pre-positioning, not a loss estimate.")}


def watch_summary() -> dict:
    storms, decl, errors = [], [], []
    try:
        storms = [{k: v for k, v in s.items() if k != "cone"} | {"has_cone": bool(s.get("cone"))}
                  for s in active_storms(with_cones=True)]
    except requests.RequestException as e:
        errors.append(f"NHC unavailable: {e}")
    try:
        decl = recent_declarations(60)
    except (requests.RequestException, ValueError) as e:
        errors.append(f"OpenFEMA unavailable: {e}")
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "active_storms": storms, "recent_disasters": decl, "errors": errors,
            "sources": ["NOAA National Hurricane Center", "OpenFEMA disaster declarations"]}
