"""
Satellite pass tracker: when did Sentinel-1 (radar) and Sentinel-2 (optical)
last image an area, and when is the next radar pass expected?

Why this exists: "results within hours" is only true relative to a satellite
pass. Sentinel-1 does not image every place every day, so the honest speed
claim is "N hours after the next pass", and the product should show the user
when that pass is. This module answers that from the free Copernicus Data
Space STAC catalogue (no account needed for search).

Prediction method: each Sentinel-1 satellite repeats the same ground track
(relative orbit) every 12 days. For every (platform, relative orbit, pass
direction) track seen over the area in the lookback window, the next expected
acquisition is the last one plus whole 12-day cycles. This is a *schedule
expectation*, not a guarantee: ESA's acquisition plan can skip or add passes
(e.g. emergency tasking, conflicts, anomalies), so the UI labels it "expected".

Only IW (Interferometric Wide swath) GRD scenes are counted as usable for the
flood pipeline; other modes (e.g. SM stripmap) are reported but flagged.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests

STAC_SEARCH = "https://catalogue.dataspace.copernicus.eu/stac/search"
S1_REPEAT_DAYS = 12                 # per-satellite exact-repeat cycle
USABLE_S1_MODES = {"IW"}            # what pipeline/flood_detect.py processes
_TIMEOUT = 25
_CACHE_TTL_S = 30 * 60
_cache: dict = {}


def _parse_dt(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def normalize_s1(features: Iterable[dict]) -> list[dict]:
    """STAC features -> compact acquisition records, one per satellite overpass.

    A single overpass is delivered as several consecutive ~25 s slices; they
    share platform, relative orbit and date, so they are merged.
    """
    seen = {}
    for f in features:
        p = f.get("properties", {})
        dt_s = p.get("datetime") or p.get("start_datetime")
        if not dt_s:
            continue
        dt = _parse_dt(dt_s)
        platform = (p.get("platform") or "").lower()
        rel = p.get("sat:relative_orbit")
        state = p.get("sat:orbit_state")
        mode = p.get("sar:instrument_mode") or ""
        key = (platform, rel, state, mode, dt.date())
        rec = seen.get(key)
        if rec is None or dt < rec["_dt"]:
            seen[key] = {
                "_dt": dt,
                "datetime": _iso(dt),
                "platform": platform,
                "relative_orbit": rel,
                "orbit_state": state,
                "mode": mode,
                "usable": mode in USABLE_S1_MODES,
                "scene_id": f.get("id"),
            }
    return sorted(seen.values(), key=lambda r: r["_dt"], reverse=True)


def predict_next_passes(acqs: list[dict], now: datetime,
                        repeat_days: int = S1_REPEAT_DAYS,
                        horizon_days: int = 24) -> list[dict]:
    """Expected future passes from observed tracks (usable modes only)."""
    tracks = {}
    for a in acqs:
        if not a.get("usable"):
            continue
        k = (a["platform"], a["relative_orbit"], a["orbit_state"])
        if k not in tracks or a["_dt"] > tracks[k]:
            tracks[k] = a["_dt"]
    out = []
    step = timedelta(days=repeat_days)
    horizon = now + timedelta(days=horizon_days)
    for (platform, rel, state), last in tracks.items():
        nxt = last + step
        while nxt <= now:
            nxt += step
        while nxt <= horizon:
            out.append({"expected": _iso(nxt), "_dt": nxt, "platform": platform,
                        "relative_orbit": rel, "orbit_state": state,
                        "based_on_last": _iso(last)})
            nxt += step
    out.sort(key=lambda r: r["_dt"])
    return out


def _strip(rows):
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def summarize(acqs: list[dict], predictions: list[dict], now: datetime) -> dict:
    usable = [a for a in acqs if a["usable"]]
    last = usable[0] if usable else None
    nxt = predictions[0] if predictions else None
    gaps = []
    for a, b in zip(usable, usable[1:]):
        gaps.append((a["_dt"] - b["_dt"]).total_seconds() / 86400)
    return {
        "last_usable_pass": last["datetime"] if last else None,
        "hours_since_last_pass": round((now - last["_dt"]).total_seconds() / 3600, 1) if last else None,
        "next_expected_pass": nxt["expected"] if nxt else None,
        "hours_until_next_pass": round((nxt["_dt"] - now).total_seconds() / 3600, 1) if nxt else None,
        "median_revisit_days": round(sorted(gaps)[len(gaps) // 2], 1) if gaps else None,
        "usable_passes_in_window": len(usable),
    }


def _stac_items(collection: str, bbox, start: datetime, end: datetime,
                limit: int = 200) -> list[dict]:
    params = {
        "collections": collection,
        "bbox": ",".join(f"{v:.5f}" for v in bbox),
        "datetime": f"{_iso(start)}/{_iso(end)}",
        "limit": limit,
    }
    r = requests.get(STAC_SEARCH, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json().get("features", [])


def normalize_s2(features: Iterable[dict]) -> list[dict]:
    seen = {}
    for f in features:
        p = f.get("properties", {})
        dt_s = p.get("datetime")
        if not dt_s:
            continue
        dt = _parse_dt(dt_s)
        key = (p.get("platform"), dt.date())
        cc = p.get("eo:cloud_cover")
        rec = seen.get(key)
        if rec is None or (cc is not None and (rec["cloud_cover"] is None or cc < rec["cloud_cover"])):
            seen[key] = {"_dt": dt, "datetime": _iso(dt), "platform": p.get("platform"),
                         "cloud_cover": cc}
    return sorted(seen.values(), key=lambda r: r["_dt"], reverse=True)


def pass_report(bbox, lookback_days: int = 40, now: Optional[datetime] = None,
                include_optical: bool = True, check_engine: bool = False) -> dict:
    """Live report for a [minlon, minlat, maxlon, maxlat] box. Cached 30 min."""
    now = now or datetime.now(timezone.utc)
    key = (tuple(round(v, 3) for v in bbox), lookback_days, include_optical, check_engine,
           int(time.time() // _CACHE_TTL_S))
    if key in _cache:
        return _cache[key]

    start = now - timedelta(days=lookback_days)
    s1 = normalize_s1(_stac_items("sentinel-1-grd", bbox, start, now))
    preds = predict_next_passes(s1, now)
    report = {
        "bbox": list(bbox),
        "generated": _iso(now),
        "source": "Copernicus Data Space Ecosystem STAC catalogue",
        "sentinel1": {
            "summary": summarize(s1, preds, now),
            "recent": _strip(s1[:20]),
            "expected": _strip(preds[:8]),
            "note": ("Expected passes assume the 12-day exact-repeat cycle of each "
                     "Sentinel-1 satellite seen over this area. ESA's acquisition "
                     "plan can change, so treat these as expectations."),
        },
    }
    if check_engine:
        latest = engine_latest_pass(bbox, lookback_days)
        report["sentinel1"]["engine_latest_pass"] = latest
        last = report["sentinel1"]["summary"]["last_usable_pass"]
        report["sentinel1"]["engine_has_last_pass"] = (
            None if latest is None or last is None else latest[:10] >= last[:10])
    if include_optical:
        try:
            s2 = normalize_s2(_stac_items("sentinel-2-l2a", bbox, start, now))
            clear = [a for a in s2 if a["cloud_cover"] is not None and a["cloud_cover"] <= 30]
            report["sentinel2"] = {
                "last_pass": s2[0]["datetime"] if s2 else None,
                "last_mostly_clear_pass": clear[0]["datetime"] if clear else None,
                "recent": _strip(s2[:10]),
                "note": "Optical imagery cannot see through cloud; storms are usually cloudy.",
            }
        except requests.RequestException as e:
            report["sentinel2"] = {"error": f"optical catalogue unavailable: {e}"}
    _cache[key] = report
    return report


def engine_latest_pass(bbox, lookback_days: int = 40) -> Optional[str]:
    """Most recent IW scene over the box that Earth Engine has ingested.

    The live pipeline reads Sentinel-1 through Earth Engine, so a pass only
    counts as processable once it appears there. Returns None if Earth Engine
    is not configured or the query fails (the caller reports that plainly).
    """
    try:
        from backend.live_pipeline import init_ee
        if not init_ee():
            return None
        import ee
        now = datetime.now(timezone.utc)
        col = (ee.ImageCollection("COPERNICUS/S1_GRD")
               .filterBounds(ee.Geometry.Rectangle(list(bbox)))
               .filterDate(_iso(now - timedelta(days=lookback_days)), _iso(now + timedelta(days=1)))
               .filter(ee.Filter.eq("instrumentMode", "IW")))
        t = col.aggregate_max("system:time_start").getInfo()
        if not t:
            return None
        return _iso(datetime.fromtimestamp(t / 1000, tz=timezone.utc))
    except Exception as e:  # noqa: BLE001 — availability probe must never break the report
        print(f"  [passes] Earth Engine probe failed: {e}")
        return None
