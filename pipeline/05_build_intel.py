"""
05_build_intel.py — Bake the intelligence layer for the demo events.

    python pipeline/05_build_intel.py [event_id ...] [--no-router]

Reads outputs/{event}_final.csv (+ coordinates), runs backend.intel over it
using only free, key-less sources (AWS Terrain Tiles, JRC GSW, CHIRPS,
Copernicus STAC, OSM, NHC HURDAT2), and writes outputs/{event}_intel.json,
which the backend merges into the event's properties at load time.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.intel import compute_intel  # noqa: E402
from pipeline.config import HARVEY, IAN, LISMORE  # noqa: E402

CONFIGS = {c['event_id']: c for c in (HARVEY, IAN, LISMORE)}


def load_props(event_id):
    out = ROOT / 'outputs'
    df = pd.read_csv(out / f'{event_id}_final.csv')
    if 'latitude' not in df.columns:
        coords = pd.read_csv(out / f'{event_id}_properties.csv')[['property_id', 'latitude', 'longitude']]
        df = df.merge(coords, on='property_id', how='left')
    df = df.dropna(subset=['latitude', 'longitude'])
    return df.to_dict('records')


def main(argv):
    run_router = '--no-router' not in argv
    events = [a for a in argv if not a.startswith('--')] or list(CONFIGS)
    for eid in events:
        cfg = CONFIGS[eid]
        props = load_props(eid)
        print(f'▶ {eid}: {len(props)} properties')
        ctx = {'event_id': eid, 'label': cfg['label'], 'bbox': cfg['bbox'],
               'post_start': cfg['post_start'], 'post_end': cfg['post_end']}
        intel = compute_intel(props, ctx, run_router=run_router)
        path = ROOT / 'outputs' / f'{eid}_intel.json'
        path.write_text(json.dumps(intel, separators=(',', ':'), default=str, allow_nan=False))
        print(f'  ✓ {path.name}  {path.stat().st_size / 1e6:.2f} MB  in {intel["seconds"]}s')
        print('   sources:', {k: v.get('ok') for k, v in intel['sources'].items()})
        print('   summary:', intel['summary'])


if __name__ == '__main__':
    main(sys.argv[1:])
