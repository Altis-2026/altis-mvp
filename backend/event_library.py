"""
Flood event library and analog retrieval ("this looks like ...").

The library unifies three free sources into one record shape:
  gfd    Global Flood Database (Tellman et al. 2021): 913 satellite-mapped
         floods worldwide 2000-2018, with Dartmouth Flood Observatory cause,
         severity, deaths and displacement; US events carry NFIP outcomes.
  fema   OpenFEMA major-disaster declarations (flood, hurricane, tropical and
         coastal storm) since 2000 with NFIP claim outcomes in the declared
         counties: claim count, share paid, median payout, median water depth
         and flood duration.
  altis  Every live analysis Altis runs is appended (record_altis_run), so the
         library grows with use. That is the "builds on itself" loop; it only
         becomes predictive once those runs are joined to real outcomes.

Analog search scores similarity on what is known early in an event (cause,
location, season, and, when available, mapped area and duration) and returns
the top matches with the reasons they matched. Priors are summarised only from
analogs that have real NFIP outcomes, and are labelled as history, not a
forecast: a similar past event is evidence, not a model of this one.
"""
from __future__ import annotations

import json
import math
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

LIB_DIR = Path(__file__).resolve().parent.parent / "outputs" / "library"
_lock = threading.Lock()
_cache: dict = {}

CAUSE_GROUPS = ("tropical_cyclone", "heavy_rain", "monsoon", "snowmelt", "dam_break", "coastal", "other")
RELATED = {frozenset(("heavy_rain", "monsoon")): 0.5, frozenset(("tropical_cyclone", "coastal")): 0.6,
           frozenset(("tropical_cyclone", "heavy_rain")): 0.3}
WEIGHTS = {"cause": 3.0, "distance": 2.0, "season": 1.0, "size": 1.0, "duration": 1.0}


def cause_group(text: Optional[str]) -> str:
    t = (text or "").lower()
    if any(w in t for w in ("hurricane", "tropical", "typhoon", "cyclone")):
        return "tropical_cyclone"
    if "coastal" in t or "surge" in t or "nor'easter" in t:
        return "coastal"
    if "dam" in t or "levee" in t or "levy" in t:
        return "dam_break"
    if "snow" in t or "ice jam" in t or "ice" in t:
        return "snowmelt"
    if "monsoon" in t:
        return "monsoon"
    if "rain" in t or "flood" in t or "storm" in t:
        return "heavy_rain"
    return "other"


def _days(a: str, b: str) -> Optional[float]:
    try:
        return max(1.0, (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days + 1.0)
    except (TypeError, ValueError):
        return None


def _from_gfd(e: dict, nfip: dict) -> dict:
    b = e.get("bounds") or []
    centroid = [e.get("dfo_centroid_x"), e.get("dfo_centroid_y")]
    if (centroid[0] is None or centroid[1] is None) and b:
        xs = [p[0] for p in b[0]]; ys = [p[1] for p in b[0]]
        centroid = [sum(xs) / len(xs), sum(ys) / len(ys)]
    o = nfip.get(str(e.get("id")))
    return {
        "id": f"gfd-{e.get('id')}", "source": "gfd",
        "name": f"{e.get('dfo_main_cause') or 'Flood'}, {e.get('countries') or e.get('dfo_country')}",
        "start": e.get("start"), "end": e.get("end"), "duration_days": _days(e.get("start"), e.get("end")),
        "cause": e.get("dfo_main_cause"), "cause_group": cause_group(e.get("dfo_main_cause")),
        "country": e.get("countries") or e.get("dfo_country"), "centroid": centroid,
        "flooded_km2": round(e["flooded_km2"], 1) if e.get("flooded_km2") else None,
        "severity": e.get("dfo_severity"), "dead": e.get("dfo_dead"), "displaced": e.get("dfo_displaced"),
        "nfip": o if o and "error" not in o else None,
    }


def _from_fema(e: dict) -> dict:
    n = e.get("nfip") or {}
    return {
        "id": f"fema-{e['disaster_number']}", "source": "fema",
        "name": f"{e['title'].title()} ({', '.join(e.get('states', []))})",
        "start": e["start"], "end": e["end"], "duration_days": _days(e["start"], e["end"]),
        "cause": e.get("incident_type"), "cause_group": cause_group(f"{e.get('incident_type')} {e['title']}"),
        "country": "United States", "centroid": n.get("claims_centroid"),
        "flooded_km2": None, "severity": None, "dead": None, "displaced": None,
        "nfip": n if n.get("n_claims") is not None and "error" not in n else None,
        "states": e.get("states"),
    }


def load_library(refresh: bool = False) -> list[dict]:
    stamp = tuple((f.name, f.stat().st_mtime) for f in sorted(LIB_DIR.glob("*.json"))) if LIB_DIR.exists() else ()
    if _cache.get("lib") is not None and _cache.get("stamp") == stamp and not refresh:
        return _cache["lib"]
    recs = []
    gfd_p, nfip_p = LIB_DIR / "gfd_events.json", LIB_DIR / "nfip_outcomes.json"
    nfip = json.loads(nfip_p.read_text()) if nfip_p.exists() else {}
    if gfd_p.exists():
        recs += [_from_gfd(e, nfip) for e in json.loads(gfd_p.read_text())]
    fema_p = LIB_DIR / "fema_events.json"
    if fema_p.exists():
        recs += [_from_fema(e) for e in json.loads(fema_p.read_text())]
    runs_p = LIB_DIR / "altis_runs.json"
    if runs_p.exists():
        recs += json.loads(runs_p.read_text())
    recs = [r for r in recs if r.get("centroid") and None not in r["centroid"]]
    _cache["lib"] = recs
    _cache["stamp"] = stamp
    return recs


def library_stats() -> dict:
    lib = load_library()
    by_src = {}
    for r in lib:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    with_outcomes = sum(1 for r in lib if r.get("nfip") and r["nfip"].get("n_claims"))
    return {"n_events": len(lib), "by_source": by_src, "with_nfip_outcomes": with_outcomes,
            "span": [min((r["start"] for r in lib if r.get("start")), default=None),
                     max((r["start"] for r in lib if r.get("start")), default=None)]}


def _haversine_km(a, b) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(min(1.0, math.sqrt(h)))


def _season_sim(d1: str, d2: str) -> Optional[float]:
    try:
        a = date.fromisoformat(d1[:10]).timetuple().tm_yday
        b = date.fromisoformat(d2[:10]).timetuple().tm_yday
    except (TypeError, ValueError):
        return None
    return 0.5 * (1 + math.cos(2 * math.pi * (a - b) / 365.25))


def _ratio_sim(x, y) -> Optional[float]:
    if not x or not y or x <= 0 or y <= 0:
        return None
    return math.exp(-abs(math.log(x / y)))


def find_analogs(lat: float, lon: float, event_date: Optional[str] = None, cause: Optional[str] = None,
                 flooded_km2: Optional[float] = None, duration_days: Optional[float] = None,
                 k: int = 8, exclude_ids: tuple = (), us_only: bool = False,
                 exclude_same_event: bool = True) -> dict:
    lib = load_library()
    q_cause = cause if cause in CAUSE_GROUPS else (cause_group(cause) if cause else None)
    scored = []
    for r in lib:
        if r["id"] in exclude_ids or (us_only and r.get("country") != "United States"
                                      and "United States" not in (r.get("country") or "")):
            continue
        dkm = _haversine_km((lon, lat), r["centroid"])
        if exclude_same_event and event_date and r.get("start") and dkm < 500:
            try:
                gap = abs((date.fromisoformat(r["start"][:10]) - date.fromisoformat(event_date[:10])).days)
            except ValueError:
                gap = 999
            if gap <= 14:
                continue   # this IS the queried event (or its twin record), not an analog of it
        parts, why = {}, []
        if q_cause:
            if r["cause_group"] == q_cause:
                parts["cause"] = 1.0; why.append(f"same cause ({q_cause.replace('_', ' ')})")
            else:
                parts["cause"] = RELATED.get(frozenset((q_cause, r["cause_group"])), 0.0)
        parts["distance"] = math.exp(-dkm / 1500.0)
        if dkm < 300:
            why.append(f"{dkm:.0f} km away")
        if event_date and r.get("start"):
            s = _season_sim(event_date, r["start"])
            if s is not None:
                parts["season"] = s
                if s > 0.9:
                    why.append("same season")
        s = _ratio_sim(flooded_km2, r.get("flooded_km2"))
        if s is not None:
            parts["size"] = s
        s = _ratio_sim(duration_days, r.get("duration_days"))
        if s is not None:
            parts["duration"] = s
        wsum = sum(WEIGHTS[p] for p in parts)
        score = sum(WEIGHTS[p] * v for p, v in parts.items()) / wsum if wsum else 0.0
        scored.append((score, dkm, r, why))
    scored.sort(key=lambda t: -t[0])
    top = scored[:k]
    analogs = [{**{kk: r.get(kk) for kk in ("id", "source", "name", "start", "end", "duration_days", "cause",
                                             "cause_group", "country", "flooded_km2", "severity", "dead",
                                             "displaced", "nfip")},
                "similarity": round(s, 3), "distance_km": round(d), "why": why}
               for s, d, r, why in top]
    return {"query": {"lat": lat, "lon": lon, "date": event_date, "cause_group": q_cause,
                      "flooded_km2": flooded_km2, "duration_days": duration_days},
            "analogs": analogs, "priors": priors_from(analogs), "library": library_stats(),
            "note": ("Historical analogs ranked by cause, location, season and (when known) size and "
                     "duration. They are evidence about events like this one, not a forecast of it.")}


def priors_from(analogs: list[dict]) -> Optional[dict]:
    """Similarity-weighted medians over analogs that have real NFIP outcomes."""
    rows = [a for a in analogs if a.get("nfip") and (a["nfip"].get("n_claims") or 0) >= 25]
    if not rows:
        return None

    def wmed(key):
        vals = [(a["nfip"].get(key), a["similarity"]) for a in rows if a["nfip"].get(key) is not None]
        if not vals:
            return None
        vals.sort()
        tot = sum(w for _, w in vals); acc = 0.0
        for v, w in vals:
            acc += w
            if acc >= tot / 2:
                return v
        return vals[-1][0]

    return {"based_on": len(rows), "events": [a["name"] for a in rows],
            "median_paid_per_claim_usd": wmed("median_paid_usd"),
            "share_of_claims_paid": wmed("share_paid"),
            "median_water_depth_in": wmed("median_water_depth_in"),
            "median_flood_duration_h": wmed("median_flood_duration_h"),
            "source": "OpenFEMA NFIP claims in the analog events (similarity-weighted medians)"}


def record_altis_run(name: str, lat: float, lon: float, event_date: Optional[str], cause: Optional[str],
                     summary: dict) -> None:
    """Append a live analysis to the library so later searches can find it."""
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    path = LIB_DIR / "altis_runs.json"
    with _lock:
        runs = json.loads(path.read_text()) if path.exists() else []
        runs.append({
            "id": f"altis-{len(runs) + 1}", "source": "altis", "name": name,
            "start": event_date, "end": event_date, "duration_days": None,
            "cause": cause, "cause_group": cause_group(cause) if cause else "other",
            "country": None, "centroid": [round(lon, 4), round(lat, 4)],
            "flooded_km2": None, "severity": None, "dead": None, "displaced": None, "nfip": None,
            "altis_summary": summary,
            "recorded": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        path.write_text(json.dumps(runs, indent=1))
        _cache.pop("lib", None)
