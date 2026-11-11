"""
USGS Short-Term Network (STN) high-water marks, from the current API.

USGS moved STN in 2026: every https://stn.wim.usgs.gov/STNServices/... URL
now 301-redirects to a web app (apps.usgs.gov/stnweb). The data service is
https://api.waterdata.usgs.gov/stnservices/ with lowercase paths. Its field
names also changed slightly; this module normalises them back to the legacy
names that hwm_check.py and gauge_depth_check.py were written against, so
cached files from either API look the same.
"""
import json
from pathlib import Path

import requests

API = "https://api.waterdata.usgs.gov/stnservices"
# The service rejects urllib's default agent (HTTP 403); identify ourselves.
HEADERS = {"User-Agent": "altis-validation/1.0 (flood research)", "Accept": "application/json"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalise(rec: dict) -> dict:
    """New-API record -> legacy field names (idempotent on legacy records)."""
    if "hwm_id" in rec and "height_above_gnd" in rec:
        return rec
    return {
        "hwm_id": rec.get("id"),
        "site_id": rec.get("site"),
        "event_id": rec.get("event"),
        "latitude_dd": _f(rec.get("latitude_dd")),
        "longitude_dd": _f(rec.get("longitude_dd")),
        "elev_ft": _f(rec.get("elev_ft")),
        "height_above_gnd": _f(rec.get("height_above_ground")),
        "vdatum_id": rec.get("vdatum"),
        "hwm_quality_id": rec.get("hwm_quality"),
        "hwm_type_id": rec.get("hwm_type"),
        "hwm_environment": rec.get("environment"),
        "waterbody": rec.get("waterbody"),
        "flag_date": rec.get("flag_date"),
        "survey_date": rec.get("survey_date"),
        "stillwater": rec.get("stillwater"),
    }


def events() -> list:
    r = requests.get(f"{API}/events/", headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_event_hwms(event_id: int, cache: Path, refresh: bool = False) -> list:
    if cache.exists() and not refresh:
        return [normalise(r) for r in json.loads(cache.read_text())]
    r = requests.get(f"{API}/hwms/", params={"event": int(event_id)}, headers=HEADERS, timeout=300)
    r.raise_for_status()
    data = [normalise(x) for x in r.json()]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return data
