"""
07_build_susceptibility.py — Pre-landfall hindcast + back-test for the demo events.

    python pipeline/07_build_susceptibility.py [event_id ...]

For each event: score every property with the trained susceptibility model
at every rainfall scenario, read off the probability at the rainfall that
actually fell (CHIRPS, the model's training product), then back-test against
what happened:

  * vs the SAR observation at each property (AUC) — meaningful where SAR saw
    the flood (Lismore); stated as unreliable where it did not (Harvey/Ian,
    where the pass came after the water drained);
  * vs FEMA NFIP flood-insurance claims per zip (Spearman) — independent
    truth for the US events (validation/nfip_flag_check.py).

Also writes outputs/opendata_{event}.json (Umbra/ICEYE overlap search).
Output: outputs/{event}_susceptibility.json
"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend import geodata  # noqa: E402
from backend.susceptibility_service import model, score_properties  # noqa: E402
from pipeline.config import HARVEY, IAN, LISMORE  # noqa: E402
from pipeline.gbdt import auc  # noqa: E402
from pipeline.susceptibility import SCENARIOS_MM, interp_curve, risk_band  # noqa: E402

CONFIGS = {c['event_id']: c for c in (HARVEY, IAN, LISMORE)}


def event_rain(cfg, lons, lats):
    """CHIRPS heaviest 3-day rainfall per property in the week to the first post day."""
    ps = date.fromisoformat(cfg['post_start'])
    days = [ps - timedelta(days=k) for k in range(7, -3, -1)]
    w, s, e, n = cfg['bbox']
    grids = [geodata.chirps_day(d, [w - 0.1, s - 0.1, e + 0.1, n + 0.1]) for d in days]
    M = np.column_stack([np.nan_to_num(g.sample(lons, lats, 'nearest'), nan=0.0) for g in grids])
    roll = np.stack([M[:, i:i + 3].sum(axis=1) for i in range(M.shape[1] - 2)], axis=1)
    return roll.max(axis=1)


def main(events):
    for eid in events:
        cfg = CONFIGS[eid]
        df = pd.read_csv(ROOT / 'outputs' / f'{eid}_final.csv')
        if 'latitude' not in df.columns:
            df = df.merge(pd.read_csv(ROOT / 'outputs' / f'{eid}_properties.csv')[
                ['property_id', 'latitude', 'longitude']], on='property_id', how='left')
        df = df.dropna(subset=['latitude', 'longitude'])
        props = df[['property_id', 'latitude', 'longitude']].to_dict('records')
        print(f'▶ {eid}: {len(props)} properties')
        res = score_properties(props)
        lons, lats = df['longitude'].to_numpy(float), df['latitude'].to_numpy(float)
        r3 = event_rain(cfg, lons, lats)
        pid = df['property_id'].astype(str).tolist()
        p_obs = np.array([interp_curve(res['properties'][k]['curve'], SCENARIOS_MM, r) for k, r in zip(pid, r3)])
        for k, r, pv in zip(pid, r3, p_obs):
            res['properties'][k]['hindcast_rain_3day_mm'] = round(float(r), 1)
            res['properties'][k]['p_hindcast'] = round(float(pv), 4)
            res['properties'][k]['band_hindcast'] = risk_band(float(pv))
        sar_wet = (df['max_depth_ft'].to_numpy(float) >= 0.3)
        sar_dry = (df['max_depth_ft'].to_numpy(float) <= 0.05) & (df['pct_flooded'].to_numpy(float) < 5)
        lab = sar_wet | sar_dry
        back = {'vs_sar': {'auc': None if sar_wet.sum() < 10 else round(auc(sar_wet[lab], p_obs[lab]), 3),
                           'sar_wet': int(sar_wet.sum()), 'sar_dry': int(sar_dry.sum()),
                           'note': ('SAR saw the flood here: a fair label.' if sar_wet.sum() >= 50 else
                                    'SAR passed after the water drained — SAR-dry is not ground truth here, '
                                    'so this AUC is not meaningful; see the NFIP comparison.')}}
        nfip_path = ROOT / 'outputs' / f'nfip_validation_{eid}.json'
        if nfip_path.exists():
            nf = json.loads(nfip_path.read_text())
            zips = {z: (v or {}).get('claims') for z, v in nf['zips'].items()}
            df['zip'] = df['address'].map(lambda a: (re.findall(r'\b(\d{5})\b', str(a)) or [None])[-1])
            df['p'] = p_obs
            g = df.dropna(subset=['zip']).groupby('zip')['p'].mean()
            common = [z for z in g.index if zips.get(z) is not None]
            if len(common) >= 5:
                from scipy.stats import spearmanr
                rho = spearmanr([g[z] for z in common], [zips[z] for z in common]).correlation
                back['vs_nfip_claims'] = {'zips': len(common), 'spearman': round(float(rho), 3),
                                          'truth': 'FEMA NFIP claims per zip in the event window'}
        bands = {}
        for v in res['properties'].values():
            bands[v['band_hindcast']] = bands.get(v['band_hindcast'], 0) + 1
        out = {'event_id': eid, 'model': {k: v for k, v in model().blob.items() if k in
                                         ('training', 'validation', 'feature_importance', 'created_at')},
               'scenarios_mm': SCENARIOS_MM, 'hindcast': {
                   'rain_product': 'CHIRPS v2.0 (model training product)',
                   'rain_3day_mm_median': round(float(np.median(r3)), 1),
                   'rain_3day_mm_max': round(float(r3.max()), 1), 'bands': bands,
                   'mode': 'hindcast — observed event rainfall stands in for the forecast'},
               'backtest': back, 'properties': res['properties']}
        path = ROOT / 'outputs' / f'{eid}_susceptibility.json'
        from backend.intel import json_safe
        path.write_text(json.dumps(json_safe(out), separators=(',', ':'), allow_nan=False))
        print(f'  ✓ {path.name} {path.stat().st_size / 1e6:.2f} MB  backtest {back}  bands {bands}')
        # Open-data overlap search, baked alongside.
        try:
            from backend.opendata import search
            od = search(cfg['bbox'], date.fromisoformat(cfg['post_start']) - timedelta(days=3),
                        date.fromisoformat(cfg['post_end']))
            (ROOT / 'outputs' / f'opendata_{eid}.json').write_text(json.dumps(od, indent=1))
        except Exception as ex:  # noqa: BLE001
            print(f'  open-data search failed: {ex}')


if __name__ == '__main__':
    main(sys.argv[1:] or list(CONFIGS))
