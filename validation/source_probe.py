#!/usr/bin/env python3
"""
source_probe.py — Check every registered free data source is reachable from
this machine. HTTP sources get a GET; Earth Engine sources an asset lookup.
Writes outputs/source_probe.json. Run before relying on a deployment.

Usage: python validation/source_probe.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.data_sources import SOURCES  # noqa: E402


def main():
    ee = None
    try:
        from backend.live_pipeline import init_ee
        if init_ee():
            import ee as _ee
            ee = _ee
    except Exception:  # noqa: BLE001
        ee = None
    out = []
    for s in SOURCES:
        a = s["access"]
        if a.startswith("ee:"):
            if ee is None:
                ok, detail = None, "Earth Engine not configured"
            else:
                try:
                    ee.data.getAsset(a[3:])
                    ok, detail = True, "asset found"
                except Exception as e:  # noqa: BLE001
                    ok, detail = False, str(e)[:120]
        else:
            try:
                r = requests.get(a, timeout=30, headers={"User-Agent": "altis-source-probe"})
                ok, detail = r.status_code < 400, f"HTTP {r.status_code}"
            except requests.RequestException as e:
                ok, detail = False, type(e).__name__
        out.append({"name": s["name"], "access": a, "status": s["status"], "reachable": ok, "detail": detail})
        print(f"{'OK ' if ok else ('-- ' if ok is None else 'NO ')} {s['name']}: {detail}", flush=True)
    res = {"checked": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "results": out,
           "n_ok": sum(1 for r in out if r["reachable"]), "n": len(out)}
    (ROOT / "outputs" / "source_probe.json").write_text(json.dumps(res, indent=1))
    print(f"{res['n_ok']}/{res['n']} reachable")


if __name__ == "__main__":
    main()
