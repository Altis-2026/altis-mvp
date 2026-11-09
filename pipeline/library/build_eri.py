#!/usr/bin/env python3
"""
Catalogue of NOAA National Geodetic Survey Emergency Response Imagery: the
free post-storm aerial photographs (about 30 cm) flown after major US storms.
NOAA's own viewers load plain XYZ tiles, one layer per flight; this reads each
storm viewer's script to list every layer and its flight date.

Writes outputs/library/eri_layers.json: [{storm, code, date, host}, ...].
Public domain (NOAA). Resumable: re-run to refresh after a new storm.
"""
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "library" / "eri_layers.json"
BASE = "https://storms.ngs.noaa.gov"
UA = {"User-Agent": "altis-catalog/1.0 (flood research)"}
TILE_RE = re.compile(r"https?://([A-Za-z0-9.\-]*noaa\.gov)/([A-Za-z0-9_\-]+)/\{z\}/\{x\}/\{y\}")


def get(url):
    r = requests.get(url, headers=UA, timeout=40, allow_redirects=True)
    return r.text if r.status_code == 200 else None


def layers_from_index(storm):
    """Newer storm viewers publish services/index.json with each flight's
    bounds and max zoom (so coverage needs no probing)."""
    txt = get(f"{BASE}/storms/{storm}/services/index.json")
    if not txt:
        return []
    try:
        data = json.loads(txt)
    except ValueError:
        return []
    out = []
    for lyr in data if isinstance(data, list) else []:
        for url in lyr.get("tiles") or []:
            m = TILE_RE.search(url)
            if m:
                out.append({"host": m.group(1), "code": m.group(2), "bounds": lyr.get("bounds"),
                            "maxzoom": lyr.get("maxzoom")})
    return out


def layers_from_script(storm):
    """Older viewers hard-code layers in js/app.js (no bounds published)."""
    for path in ("js/app.js", "js/main.js", "app.js"):
        js = get(f"{BASE}/storms/{storm}/{path}")
        if js and TILE_RE.search(js):
            return [{"host": h, "code": c, "bounds": None, "maxzoom": None} for h, c in TILE_RE.findall(js)]
    return []


def main():
    idx = get(BASE + "/") or ""
    storms = sorted(set(re.findall(r"storms\.ngs\.noaa\.gov/storms/([a-z0-9_]+)/index\.html", idx)))
    print(len(storms), "storms on the index", flush=True)
    out, seen = [], set()
    for s in storms:
        found = layers_from_index(s) or layers_from_script(s)
        n = 0
        for l in found:
            m = re.match(r"(\d{8})", l["code"])
            if not m or (s, l["code"]) in seen:
                continue
            seen.add((s, l["code"]))
            d = m.group(1)
            out.append({"storm": s, "code": l["code"], "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "host": l["host"],
                        "bounds": l["bounds"], "maxzoom": l["maxzoom"]})
            n += 1
        print(f"  {s}: {n} flight layers" + (" (with bounds)" if found and found[0]["bounds"] else ""), flush=True)
    out.sort(key=lambda r: (r["date"], r["code"]))
    OUT.write_text(json.dumps(out))
    print("wrote", OUT, len(out), "layers", out[0]["date"], "->", out[-1]["date"])


if __name__ == "__main__":
    main()
