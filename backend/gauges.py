"""
River gauges as flood evidence and as a physics anchor for water depth.

Sources (all free, no account):
  - NOAA National Water Prediction Service (NWPS) API: gauge list by bbox,
    live observed / forecast stage with flood category, official flood-stage
    thresholds, historic crests, and the gauge datum in NAVD88.
  - USGS NWIS instantaneous values: the stage time series for a past window,
    used when the NWPS crest list does not cover the event.

Why this matters for the product:
  1. Speed and coverage: gauges report every 15-60 minutes, so they say
     "this river is in major flood" days before the next radar pass and
     through cloud, night and dense city (where radar is weakest).
  2. Physics: stage + datum = water-surface elevation (WSE) in NAVD88.
     Interpolated along the river between gauges, WSE minus ground elevation
     gives a depth that does not depend on the radar seeing the water at all.
     It only applies where a property is hydraulically connected to that
     river (riverine floodplain), which callers must check (distance to the
     river line, HAND); it says nothing about local rainfall ponding.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import requests

NWPS = "https://api.water.noaa.gov/nwps/v1"
NWIS_IV = "https://waterservices.usgs.gov/nwis/iv/"
NWIS_SITE = "https://waterservices.usgs.gov/nwis/site/"
NLDI = "https://api.water.usgs.gov/nldi/linked-data"
_TIMEOUT = 25
_MEM: dict = {}
_LIVE_TTL_S = 15 * 60
CACHE_DIR = Path(__file__).resolve().parent.parent / "outputs" / "gauge_cache"

CATEGORY_ORDER = ["no_flooding", "action", "minor", "moderate", "major"]


def _get_json(url: str, params: Optional[dict] = None) -> dict:
    r = requests.get(url, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _valid(v) -> Optional[float]:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v <= -999 else v


# ── Static gauge metadata (thresholds, datum, crests): disk-cached ─────────

def gauge_detail(lid: str, refresh: bool = False) -> dict:
    lid = lid.upper()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{lid}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    d = _get_json(f"{NWPS}/gauges/{lid}")
    path.write_text(json.dumps(d))
    return d


def thresholds(detail: dict) -> dict:
    cats = (detail.get("flood") or {}).get("categories") or {}
    return {k: _valid((cats.get(k) or {}).get("stage")) for k in ("action", "minor", "moderate", "major")}


def navd88_datum_ft(detail: dict) -> Optional[float]:
    vals = (((detail.get("datums") or {}).get("vertical") or {}).get("value")) or []
    for v in vals:
        if (v.get("abbrev") or "").upper() == "NAVD88":
            return _valid(v.get("value"))
    return None


def nwis_datum_navd88_ft(usgs_id: str) -> Optional[float]:
    """Gage datum from the USGS site record, only when it is stated in NAVD88
    (an NGVD29 datum would need a vertical transformation we do not apply)."""
    if not usgs_id:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"nwis_site_{usgs_id}.txt"
    if path.exists():
        txt = path.read_text()
    else:
        try:
            r = requests.get(NWIS_SITE, params={"format": "rdb", "sites": usgs_id,
                                                "siteOutput": "expanded"}, timeout=_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException:
            return None
        txt = r.text
        path.write_text(txt)
    rows = [ln.split("\t") for ln in txt.splitlines() if ln and not ln.startswith("#")]
    if len(rows) < 3:
        return None
    hdr, vals = rows[0], rows[2]
    rec = dict(zip(hdr, vals))
    if rec.get("alt_datum_cd", "").strip() != "NAVD88":
        return None
    return _valid(rec.get("alt_va", "").strip())


def gauge_datum_navd88_ft(detail: dict) -> tuple[Optional[float], Optional[str]]:
    """(datum, source): NWPS first, USGS site record as fallback."""
    d = navd88_datum_ft(detail)
    if d is not None:
        return d, "NWPS"
    d = nwis_datum_navd88_ft(detail.get("usgsId"))
    return (d, "USGS NWIS site record") if d is not None else (None, None)


def classify_stage(stage: Optional[float], thr: dict) -> str:
    """Official NWS flood category for a stage, from the gauge's own thresholds."""
    if stage is None:
        return "unknown"
    cat = "no_flooding"
    for name in ("action", "minor", "moderate", "major"):
        t = thr.get(name)
        if t is not None and stage >= t:
            cat = name
    return cat


def crests_in_window(detail: dict, start: str, end: str) -> list[dict]:
    hist = (((detail.get("flood") or {}).get("crests") or {}).get("historic")) or []
    out = []
    for c in hist:
        t = (c.get("occurredTime") or "")[:10]
        if start <= t <= end and _valid(c.get("stage")) is not None and not c.get("olddatum"):
            out.append({"time": c["occurredTime"], "stage_ft": float(c["stage"]),
                        "flow_cfs": _valid(c.get("flow")), "source": "NWPS historic crest"})
    return sorted(out, key=lambda c: -c["stage_ft"])


def nwis_peak_stage(usgs_id: str, start: str, end: str) -> Optional[dict]:
    """Peak gage height (ft) from USGS instantaneous values in a window."""
    try:
        d = _get_json(NWIS_IV, {"format": "json", "sites": usgs_id, "parameterCd": "00065",
                                "startDT": start, "endDT": end, "siteStatus": "all"})
    except requests.RequestException:
        return None
    best = None
    for ts in d.get("value", {}).get("timeSeries", []):
        for block in ts.get("values", []):
            for v in block.get("value", []):
                s = _valid(v.get("value"))
                if s is not None and (best is None or s > best["stage_ft"]):
                    best = {"time": v.get("dateTime"), "stage_ft": s, "source": "USGS NWIS instantaneous"}
    return best


# ── Gauges in an area ───────────────────────────────────────────────────────

def gauges_in_bbox(bbox, include_detail: bool = True, max_gauges: int = 30) -> list[dict]:
    """(max_gauges caps detail lookups; wide validation areas raise it.)"""
    """Gauges inside [minlon, minlat, maxlon, maxlat] with live status."""
    key = ("bbox", tuple(round(v, 3) for v in bbox), include_detail, int(time.time() // _LIVE_TTL_S))
    if key in _MEM:
        return _MEM[key]
    d = _get_json(f"{NWPS}/gauges", {"bbox.xmin": bbox[0], "bbox.ymin": bbox[1],
                                      "bbox.xmax": bbox[2], "bbox.ymax": bbox[3],
                                      "srid": "EPSG_4326"})
    out = []
    for g in (d.get("gauges") or [])[:max_gauges]:
        obs = (g.get("status") or {}).get("observed") or {}
        fc = (g.get("status") or {}).get("forecast") or {}
        rec = {
            "lid": g.get("lid"), "name": g.get("name"),
            "latitude": g.get("latitude"), "longitude": g.get("longitude"),
            "observed_stage_ft": _valid(obs.get("primary")) if obs.get("primaryUnit") == "ft" else None,
            "observed_category": obs.get("floodCategory"),
            "observed_time": obs.get("validTime"),
            "forecast_stage_ft": _valid(fc.get("primary")) if fc.get("primaryUnit") == "ft" else None,
            "forecast_category": fc.get("floodCategory"),
            "forecast_time": fc.get("validTime"),
        }
        if include_detail:
            try:
                det = gauge_detail(rec["lid"])
                rec["usgs_id"] = det.get("usgsId")
                rec["thresholds_ft"] = thresholds(det)
                rec["datum_navd88_ft"], rec["datum_source"] = gauge_datum_navd88_ft(det)
            except requests.RequestException:
                pass
        out.append(rec)
    _MEM[key] = out
    return out


def event_gauge_peaks(bbox, start: str, end: str, max_gauges: int = 30) -> list[dict]:
    """Peak stage per gauge during a past event window, with flood category
    and water-surface elevation (NAVD88) where the datum is published."""
    out = []
    for g in gauges_in_bbox(bbox, include_detail=True, max_gauges=max_gauges):
        try:
            det = gauge_detail(g["lid"])
        except requests.RequestException:
            continue
        crest = (crests_in_window(det, start, end) or [None])[0]
        if crest is None and det.get("usgsId"):
            crest = nwis_peak_stage(det["usgsId"], start, end)
        thr = thresholds(det)
        datum, datum_src = gauge_datum_navd88_ft(det)
        peak = crest["stage_ft"] if crest else None
        out.append({
            "lid": g["lid"], "name": g["name"],
            "latitude": g["latitude"], "longitude": g["longitude"],
            "usgs_id": det.get("usgsId"),
            "peak_stage_ft": peak,
            "peak_time": crest["time"] if crest else None,
            "peak_source": crest["source"] if crest else None,
            "peak_category": classify_stage(peak, thr),
            "thresholds_ft": thr,
            "datum_navd88_ft": datum,
            "datum_source": datum_src,
            "peak_wse_navd88_ft": round(peak + datum, 2) if peak is not None and datum is not None else None,
        })
    return out


# ── Water-surface interpolation along a river ──────────────────────────────

def _to_xy(lat, lon, lat0):
    """Local equirectangular projection in metres (fine at reach scale)."""
    R = 6371000.0
    return (np.radians(lon) * R * math.cos(math.radians(lat0)), np.radians(lat) * R)


def river_wse_line(gauges: list[dict]) -> list[dict]:
    """Gauges with a WSE, ordered upstream -> downstream (descending WSE)."""
    pts = [g for g in gauges if g.get("peak_wse_navd88_ft") is not None]
    return sorted(pts, key=lambda g: -g["peak_wse_navd88_ft"])


def interpolate_wse(lat, lon, line: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """WSE (ft NAVD88) at points by projecting onto the gauge polyline.

    Returns (wse_ft, distance_to_line_m). Beyond either end of the line the
    nearest gauge's WSE is held flat (no extrapolated slope). With a single
    gauge, WSE is that gauge's value everywhere. Callers must restrict use to
    points near the river and hydraulically connected to it.
    """
    lat = np.atleast_1d(np.asarray(lat, float))
    lon = np.atleast_1d(np.asarray(lon, float))
    if not line:
        return np.full(lat.shape, np.nan), np.full(lat.shape, np.nan)
    lat0 = float(np.mean([g["latitude"] for g in line]))
    px, py = _to_xy(lat, lon, lat0)
    gx, gy = _to_xy(np.array([g["latitude"] for g in line]),
                    np.array([g["longitude"] for g in line]), lat0)
    gw = np.array([g["peak_wse_navd88_ft"] for g in line], float)
    if len(line) == 1:
        return np.full(lat.shape, gw[0]), np.hypot(px - gx[0], py - gy[0])

    best_d = np.full(lat.shape, np.inf)
    best_w = np.full(lat.shape, np.nan)
    for i in range(len(line) - 1):
        ax, ay, bx, by = gx[i], gy[i], gx[i + 1], gy[i + 1]
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy or 1e-9
        t = np.clip(((px - ax) * vx + (py - ay) * vy) / L2, 0.0, 1.0)
        cx, cy = ax + t * vx, ay + t * vy
        d = np.hypot(px - cx, py - cy)
        w = gw[i] + t * (gw[i + 1] - gw[i])
        better = d < best_d
        best_d[better], best_w[better] = d[better], w[better]
    return best_w, best_d


def live_status_summary(gauges: list[dict]) -> dict:
    """Worst observed and forecast categories across gauges."""
    def worst(field):
        ranks = [CATEGORY_ORDER.index(g[field]) for g in gauges if g.get(field) in CATEGORY_ORDER]
        return CATEGORY_ORDER[max(ranks)] if ranks else None
    flooding = [g for g in gauges if g.get("observed_category") in ("minor", "moderate", "major")]
    return {"n_gauges": len(gauges), "worst_observed": worst("observed_category"),
            "worst_forecast": worst("forecast_category"),
            "gauges_in_flood": [g["lid"] for g in flooding],
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


# ── Along-channel interpolation on the real river network (USGS NLDI) ──────

def mainstem_flowline(usgs_id: str, direction: str = "UM", distance_km: int = 250) -> list[list[float]]:
    """Main-stem river centreline from a USGS gauge, as [[lon, lat], ...]
    ordered downstream -> upstream for "UM" (upstream main stem)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"nldi_{usgs_id}_{direction}_{distance_km}.json"
    if path.exists():
        gj = json.loads(path.read_text())
    else:
        gj = _get_json(f"{NLDI}/nwissite/USGS-{usgs_id}/navigation/{direction}/flowlines",
                       {"distance": distance_km})
        path.write_text(json.dumps(gj))
    segs = []
    for f in gj.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") == "LineString":
            segs.append(g["coordinates"])
        elif g.get("type") == "MultiLineString":
            segs.extend(g["coordinates"])
    return _chain_segments(segs)


def _chain_segments(segs: list[list]) -> list[list[float]]:
    """Join NHD flowline pieces into one polyline by greedy end matching."""
    segs = [list(map(tuple, s)) for s in segs if len(s) >= 2]
    if not segs:
        return []
    chain = segs.pop(0)
    while segs:
        best, best_i, best_mode = None, None, None
        for i, s in enumerate(segs):
            for mode, a, b in (("tail", chain[-1], s[0]), ("tail_r", chain[-1], s[-1]),
                               ("head", chain[0], s[-1]), ("head_r", chain[0], s[0])):
                dd = (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
                if best is None or dd < best:
                    best, best_i, best_mode = dd, i, mode
        s = segs.pop(best_i)
        if best_mode == "tail":
            chain = chain + s[1:]
        elif best_mode == "tail_r":
            chain = chain + s[::-1][1:]
        elif best_mode == "head":
            chain = s[:-1] + chain
        else:
            chain = s[::-1][:-1] + chain
    return [list(p) for p in chain]


def _project_to_polyline(lat, lon, line_lonlat):
    """(chainage_m, distance_m) of points on a polyline."""
    pts = np.asarray(line_lonlat, float)
    lat0 = float(np.mean(pts[:, 1]))
    lx, ly = _to_xy(pts[:, 1], pts[:, 0], lat0)
    px, py = _to_xy(np.atleast_1d(lat), np.atleast_1d(lon), lat0)
    seg_len = np.hypot(np.diff(lx), np.diff(ly))
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    best_d = np.full(px.shape, np.inf)
    best_c = np.full(px.shape, np.nan)
    for i in range(len(lx) - 1):
        vx, vy = lx[i + 1] - lx[i], ly[i + 1] - ly[i]
        L2 = vx * vx + vy * vy or 1e-9
        t = np.clip(((px - lx[i]) * vx + (py - ly[i]) * vy) / L2, 0, 1)
        d = np.hypot(px - (lx[i] + t * vx), py - (ly[i] + t * vy))
        better = d < best_d
        best_d[better] = d[better]
        best_c[better] = cum[i] + t[better] * seg_len[i]
    return best_c, best_d


def interpolate_wse_along_channel(lat, lon, gauges: list[dict], line_lonlat):
    """WSE (ft NAVD88) by linear interpolation in along-channel distance
    between gauges on the same main stem. Held flat beyond the end gauges.
    Returns (wse_ft, distance_to_channel_m, chainage_m)."""
    gs = [g for g in gauges if g.get("peak_wse_navd88_ft") is not None]
    if not gs or not line_lonlat:
        n = np.atleast_1d(lat).shape
        return np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    gc, gd = _project_to_polyline([g["latitude"] for g in gs], [g["longitude"] for g in gs], line_lonlat)
    order = np.argsort(gc)
    gc = gc[order]
    gw = np.array([gs[i]["peak_wse_navd88_ft"] for i in order], float)
    pc, pd = _project_to_polyline(lat, lon, line_lonlat)
    wse = np.interp(pc, gc, gw)
    return wse, pd, pc


# ── Product: gauge-anchored depth for any set of points ────────────────────

import re as _re

RIVERINE_MAX_DIST_M = 3000   # beyond this from a gauged main stem the method is not applied


_POOL_WORDS = ("reservoir", "lake ", " lake", "pool", "dam ")


def is_pool_gauge(name: str) -> bool:
    n = f" {(name or '').lower()} "
    return any(w in n for w in _POOL_WORDS)


def river_name(gauge_name: str) -> str:
    """'Brazos River at Richmond' -> 'Brazos River'."""
    return _re.split(r"\s+(?:at|near|nr|above|below|abv|blw)\s+", gauge_name or "", maxsplit=1)[0].strip()


def gauge_depth_for_points(lat, lon, bbox, start: str, end: str, ground_ft=None,
                           pad_deg: float = 0.35, max_dist_m: float = RIVERINE_MAX_DIST_M,
                           max_gauges: int = 60) -> dict:
    """Peak water-surface elevation and depth from gauged main stems.

    ground_ft: ground elevation (ft NAVD88) per point; if None, only WSE and
    distance are returned. Each point is assigned the gauged river whose
    channel is nearest; points farther than max_dist_m get no estimate.
    """
    lat = np.atleast_1d(np.asarray(lat, float))
    lon = np.atleast_1d(np.asarray(lon, float))
    wide = [bbox[0] - pad_deg, bbox[1] - pad_deg, bbox[2] + pad_deg, bbox[3] + pad_deg]
    peaks = event_gauge_peaks(wide, start, end, max_gauges=max_gauges)
    rivers = {}
    for g in peaks:
        # Reservoir / lake pool gauges measure a level pool held behind a dam,
        # not a point on a sloping river: interpolating a channel through one
        # puts the pool level on everything downstream of the dam. Excluded.
        if g.get("peak_wse_navd88_ft") is not None and not is_pool_gauge(g.get("name")):
            rivers.setdefault(river_name(g["name"]), []).append(g)

    best_d = np.full(lat.shape, np.inf)
    wse = np.full(lat.shape, np.nan)
    ref = np.array([None] * len(lat), dtype=object)
    used = {}
    for name, gs in rivers.items():
        # The channel is traced upstream from the lowest gauge that has a USGS
        # id (NLDI is keyed on USGS sites); gauges without one still anchor WSE.
        with_id = [g for g in gs if g.get("usgs_id")]
        if not with_id:
            continue
        downstream = min(with_id, key=lambda g: g["peak_wse_navd88_ft"])
        try:
            line = mainstem_flowline(downstream["usgs_id"], "UM", 250)
        except requests.RequestException:
            continue
        if not line:
            continue
        w, d, _ = interpolate_wse_along_channel(lat, lon, gs, line)
        better = d < best_d
        best_d[better], wse[better] = d[better], w[better]
        ref[better] = name
        used[name] = [{"lid": g["lid"], "name": g["name"], "peak_stage_ft": g["peak_stage_ft"],
                       "peak_time": g["peak_time"], "peak_category": g["peak_category"],
                       "peak_wse_navd88_ft": g["peak_wse_navd88_ft"]} for g in gs]

    ok = best_d <= max_dist_m
    wse = np.where(ok, wse, np.nan)
    depth = None
    if ground_ft is not None:
        depth = np.where(ok & np.isfinite(ground_ft), np.maximum(0.0, wse - np.asarray(ground_ft, float)), np.nan)
    return {"wse_ft": wse, "depth_ft": depth, "dist_to_channel_m": best_d,
            "river": np.where(ok, ref, None), "rivers_used": used, "all_gauge_peaks": peaks}


# ── Live analysis hook ─────────────────────────────────────────────────────

def _in_conus_box(lat, lon) -> bool:
    return 17 <= lat <= 72 and -170 <= lon <= -65


def attach_gauge_depth_live(rows: list, windows: Optional[dict], conflict_min_ft: float = 1.0,
                            radar_dry_max_pct: float = 5.0) -> Optional[dict]:
    """Add gauge depth + conflict flag to live-analysis rows in place.

    US only (NOAA/USGS). Never raises: a gauge failure must not cost the user
    their satellite run, so problems are returned as a status note instead.
    """
    if not rows or not windows or not windows.get("post_start"):
        return None
    pts = [(i, r) for i, r in enumerate(rows)
           if r.get("latitude") is not None and r.get("longitude") is not None
           and r.get("impact_class") not in (None, "No Coverage")
           and _in_conus_box(float(r["latitude"]), float(r["longitude"]))]
    if not pts:
        return {"status": "not_applicable", "reason": "no US properties (NOAA gauges are US only)"}
    try:
        from datetime import date as _d, timedelta as _td
        start = (_d.fromisoformat(windows["post_start"][:10]) - _td(days=3)).isoformat()
        end = (windows.get("post_end") or windows["post_start"])[:10]
        lat = np.array([float(r["latitude"]) for _, r in pts])
        lon = np.array([float(r["longitude"]) for _, r in pts])
        bbox = [lon.min() - 0.01, lat.min() - 0.01, lon.max() + 0.01, lat.max() + 0.01]
        res = gauge_depth_for_points(lat, lon, bbox, start, end)
        near = np.isfinite(res["wse_ft"])
        ground = np.full(len(lat), np.nan)
        if near.any():
            idx = np.where(near)[0]
            gsub, _ = sample_ground_ft(lat[idx], lon[idx], bbox)
            ground[idx] = gsub
        n_conf = 0
        for k, (_, r) in enumerate(pts):
            if not near[k] or not np.isfinite(ground[k]):
                continue
            depth = max(0.0, float(res["wse_ft"][k] - ground[k]))
            r["gauge_depth_ft"] = round(depth, 2)
            r["gauge_wse_ft"] = round(float(res["wse_ft"][k]), 2)
            r["gauge_dist_m"] = round(float(res["dist_to_channel_m"][k]))
            r["gauge_river"] = res["river"][k]
            dry = float(r.get("pct_flooded") or 0) <= radar_dry_max_pct
            r["gauge_conflict"] = int(dry and depth >= conflict_min_ft)
            n_conf += r["gauge_conflict"]
        return {"status": "ok", "window": [start, end], "rivers": list(res["rivers_used"]),
                "n_with_gauge_depth": int(sum(1 for _, r in pts if "gauge_depth_ft" in r)),
                "n_conflicts": n_conf}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "reason": f"{type(e).__name__}: {e}"}



def sample_ground_ft(lat, lon, bbox, chunk: int = 2000):
    """Ground elevation (ft NAVD88) at points: USGS 3DEP 1 m lidar, 3DEP 10 m
    where 1 m has no coverage. Returns (ground_ft, source) arrays; NaN/None
    where neither exists (outside the US)."""
    from backend.live_pipeline import init_ee
    lat = np.atleast_1d(np.asarray(lat, float))
    lon = np.atleast_1d(np.asarray(lon, float))
    g = np.full(len(lat), np.nan)
    src = np.array([None] * len(lat), dtype=object)
    if not len(lat) or not init_ee():
        return g, src
    import ee
    geom = ee.Geometry.Rectangle([float(v) for v in bbox])
    one = ee.ImageCollection("USGS/3DEP/1m").filterBounds(geom).mosaic().select(0).rename("m1")
    ten = ee.Image("USGS/3DEP/10m").select(0).rename("m10")
    img = one.addBands(ten)
    for s0 in range(0, len(lat), chunk):
        idx = range(s0, min(s0 + chunk, len(lat)))
        fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([float(lon[i]), float(lat[i])]), {"i": i})
                                   for i in idx])
        for f in img.reduceRegions(fc, ee.Reducer.first(), 1).getInfo()["features"]:
            pr = f["properties"]
            if pr.get("m1") is not None:
                g[pr["i"]], src[pr["i"]] = pr["m1"] * 3.28084, "3DEP 1 m"
            elif pr.get("m10") is not None:
                g[pr["i"]], src[pr["i"]] = pr["m10"] * 3.28084, "3DEP 10 m"
    return g, src
