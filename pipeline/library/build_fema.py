#!/usr/bin/env python3
"""
US flood-type disasters since 2000 from OpenFEMA declarations (major
disasters: Flood, Hurricane, Tropical Storm, Coastal Storm), each with real
NFIP insured-loss outcomes from claims in its declared counties during its
incident window (begin - 2 days .. end + 7 days).

Writes outputs/library/fema_events.json. Resumable. Claim statistics use up
to MAX_ROWS claims per event in API order (not a random sample); the full
claim count is exact.
"""
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "outputs" / "library"
DECL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
CLAIMS = "https://www.fema.gov/api/open/v3/NfipClaims"
TYPES = ("Flood", "Hurricane", "Tropical Storm", "Coastal Storm")
FIELDS = ("amountPaidOnBuildingClaim,amountPaidOnContentsClaim,waterDepth,floodWaterDuration,"
          "latitude,longitude,floodEvent")
MAX_ROWS = 10000


def get(url, params, tries=6):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=180)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(5 * (k + 1))
    raise RuntimeError(f"OpenFEMA failed: {params.get('$filter', '')[:120]}")


def declarations():
    flt = ("declarationType eq 'DR' and incidentBeginDate ge '2000-01-01' and (" +
           " or ".join(f"incidentType eq '{t}'" for t in TYPES) + ")")
    rows, skip = [], 0
    while True:
        d = get(DECL, {"$filter": flt, "$top": 10000, "$skip": skip})
        batch = d["DisasterDeclarationsSummaries"]
        rows += batch
        if len(batch) < 10000:
            break
        skip += 10000
    ev = defaultdict(lambda: {"counties": set(), "states": set()})
    for r in rows:
        e = ev[r["disasterNumber"]]
        e.update({"disaster_number": r["disasterNumber"], "title": r["declarationTitle"],
                  "incident_type": r["incidentType"],
                  "start": r["incidentBeginDate"][:10],
                  "end": (r.get("incidentEndDate") or r["incidentBeginDate"])[:10]})
        e["states"].add(r["state"])
        if r.get("fipsCountyCode") and r["fipsCountyCode"] != "000":
            e["counties"].add(r["fipsStateCode"] + r["fipsCountyCode"])
    out = []
    for e in ev.values():
        e["counties"] = sorted(e["counties"]); e["states"] = sorted(e["states"])
        out.append(e)
    return sorted(out, key=lambda e: e["start"])


def claims_for(ev):
    d0 = (date.fromisoformat(ev["start"]) - timedelta(days=2)).isoformat()
    d1 = (date.fromisoformat(ev["end"]) + timedelta(days=7)).isoformat()
    total, rows = 0, []
    cs = ev["counties"]
    for i in range(0, len(cs), 40):
        chunk = ",".join(f"'{c}'" for c in cs[i:i + 40])
        flt = f"countyCode in ({chunk}) and dateOfLoss ge '{d0}' and dateOfLoss le '{d1}'"
        first = get(CLAIMS, {"$filter": flt, "$top": 1, "$count": "true", "$select": FIELDS})
        n = first["metadata"]["count"]
        total += n
        if n and len(rows) < MAX_ROWS:
            d = get(CLAIMS, {"$filter": flt, "$top": min(MAX_ROWS - len(rows), 10000), "$select": FIELDS})
            rows += d["NfipClaims"]
    paid = np.array([(r.get("amountPaidOnBuildingClaim") or 0) + (r.get("amountPaidOnContentsClaim") or 0)
                     for r in rows], float)
    pos = paid[paid > 0]
    depth = np.array([r["waterDepth"] for r in rows if r.get("waterDepth") not in (None, 0)], float)
    dur = np.array([r["floodWaterDuration"] for r in rows if r.get("floodWaterDuration") not in (None, 0)], float)
    lat = np.array([r["latitude"] for r in rows if r.get("latitude") is not None], float)
    lon = np.array([r["longitude"] for r in rows if r.get("longitude") is not None], float)
    return {
        "window": [d0, d1], "n_claims": int(total), "n_rows_used": len(rows),
        "share_paid": round(float((paid > 0).mean()), 3) if len(paid) else None,
        "median_paid_usd": round(float(np.median(pos))) if len(pos) else None,
        "mean_paid_usd": round(float(pos.mean())) if len(pos) else None,
        "median_water_depth_in": round(float(np.median(depth)), 1) if len(depth) else None,
        "median_flood_duration_h": round(float(np.median(dur)), 1) if len(dur) else None,
        "claims_centroid": [round(float(lon.mean()), 3), round(float(lat.mean()), 3)] if len(lat) else None,
        "fema_event_names": Counter((r.get("floodEvent") or "unnamed") for r in rows).most_common(3),
    }


def main():
    LIB.mkdir(parents=True, exist_ok=True)
    path = LIB / "fema_events.json"
    done = {str(e["disaster_number"]): e for e in json.loads(path.read_text())} if path.exists() else {}
    evs = declarations()
    print(f"{len(evs)} flood-type major disasters since 2000", flush=True)
    for k, ev in enumerate(evs):
        key = str(ev["disaster_number"])
        if key in done and "nfip" in done[key]:
            continue
        try:
            ev["nfip"] = claims_for(ev) if ev["counties"] else {"n_claims": None, "note": "no county designations"}
        except Exception as e:  # noqa: BLE001
            ev["nfip"] = {"error": str(e)}
        done[key] = ev
        if k % 10 == 0 or k == len(evs) - 1:
            path.write_text(json.dumps(list(done.values())))
        print(f"  {k + 1}/{len(evs)} {ev['start']} {ev['title'][:40]} ({','.join(ev['states'])}): "
              f"{ev['nfip'].get('n_claims')} claims", flush=True)
    path.write_text(json.dumps(list(done.values())))


if __name__ == "__main__":
    main()
