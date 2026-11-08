#!/usr/bin/env python3
"""
Attach real insured-loss outcomes to every US event in the flood library,
from the free OpenFEMA NFIP claims API (v3, redacted: lat/lon rounded to
0.1 degree). Claims are matched by date of loss inside the event window
(start - 2 days .. end + 7 days) and location inside the satellite-mapped
footprint's bounding box.

Writes outputs/library/nfip_outcomes.json keyed by GFD id. Match is by box
and date, not by FEMA's own event name, so neighbouring storms in the same
window can leak in; floodEvent names seen are recorded so that is auditable.
"""
import json
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "outputs" / "library"
API = "https://www.fema.gov/api/open/v3/NfipClaims"
FIELDS = ("dateOfLoss,amountPaidOnBuildingClaim,amountPaidOnContentsClaim,buildingDamageAmount,"
          "waterDepth,interiorWaterDepth,floodWaterDuration,floodEvent,causeOfDamage")
MAX_ROWS = 40000


def get(params, tries=5):
    for k in range(tries):
        try:
            r = requests.get(API, params=params, timeout=120)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(5 * (k + 1))
    raise RuntimeError(f"OpenFEMA failed: {params.get('$filter')}")


def outcomes(ev):
    (x0, y0), (x1, y1) = ev["bounds"][0][0], ev["bounds"][0][2]
    w, e = min(x0, x1), max(x0, x1)
    s, n = min(y0, y1), max(y0, y1)
    d0 = (date.fromisoformat(ev["start"]) - timedelta(days=2)).isoformat()
    d1 = (date.fromisoformat(ev["end"]) + timedelta(days=7)).isoformat()
    flt = (f"dateOfLoss ge '{d0}' and dateOfLoss le '{d1}' and latitude ge {s:.2f} and "
           f"latitude le {n:.2f} and longitude ge {w:.2f} and longitude le {e:.2f}")
    first = get({"$filter": flt, "$top": 1, "$count": "true", "$select": FIELDS})
    total = first["metadata"]["count"]
    rows, skip = [], 0
    while skip < min(total, MAX_ROWS):
        d = get({"$filter": flt, "$top": 10000, "$skip": skip, "$select": FIELDS})
        rows += d["NfipClaims"]
        skip += 10000
    paid = np.array([(r.get("amountPaidOnBuildingClaim") or 0) + (r.get("amountPaidOnContentsClaim") or 0)
                     for r in rows], float)
    depth = np.array([r["waterDepth"] for r in rows if r.get("waterDepth") not in (None, 0)], float)
    dur = np.array([r["floodWaterDuration"] for r in rows if r.get("floodWaterDuration") not in (None, 0)], float)
    paid_pos = paid[paid > 0]
    return {
        "window": [d0, d1], "bbox": [round(w, 2), round(s, 2), round(e, 2), round(n, 2)],
        "n_claims": int(total), "n_rows_used": len(rows),
        "share_paid": round(float((paid > 0).mean()), 3) if len(paid) else None,
        "median_paid_usd": round(float(np.median(paid_pos))) if len(paid_pos) else None,
        "mean_paid_usd": round(float(paid_pos.mean())) if len(paid_pos) else None,
        "total_paid_usd_in_rows": round(float(paid.sum())),
        "median_water_depth_in": round(float(np.median(depth)), 1) if len(depth) else None,
        "median_flood_duration_h": round(float(np.median(dur)), 1) if len(dur) else None,
        "fema_event_names": Counter((r.get("floodEvent") or "unnamed") for r in rows).most_common(4),
    }


def main():
    events = json.loads((LIB / "gfd_events.json").read_text())
    path = LIB / "nfip_outcomes.json"
    done = json.loads(path.read_text()) if path.exists() else {}
    us = [e for e in events if e.get("cc") == "USA" or "United States" in (e.get("countries") or "")]
    print(f"{len(us)} US events", flush=True)
    for k, ev in enumerate(us):
        key = str(ev["id"])
        if key in done:
            continue
        try:
            done[key] = outcomes(ev)
        except Exception as e:  # noqa: BLE001
            done[key] = {"error": str(e)}
        path.write_text(json.dumps(done))
        o = done[key]
        print(f"  {k + 1}/{len(us)} {ev['start']} {ev.get('dfo_main_cause')}: {o.get('n_claims')} claims", flush=True)


if __name__ == "__main__":
    main()
