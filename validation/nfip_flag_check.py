#!/usr/bin/env python3
"""
nfip_flag_check.py — Does the TRANSIENT_MISS flag catch real flood losses?

Independent truth: FEMA NFIP redacted claims (OpenFEMA FimaNfipClaims) — every
paid-or-filed National Flood Insurance claim, with the reported zip code, the
date of loss and the water depth inside the building. Claims are released at
zip level (addresses are redacted), so this is a zip-level test:

  "flood-confirmed zip"  ≥ CONFIRMED_MIN_CLAIMS NFIP claims dated in the
                         event window
  "quiet zip"            ≤ QUIET_MAX_CLAIMS claims

and for the properties Sentinel-1 read as dry (the SAR-only Remote-Deny set):
  catch rate  = share held back by the flag in flood-confirmed zips
  quiet-hold  = share held back in quiet zips   (the false-alarm proxy)

A good flag has a high catch rate and a low quiet-hold rate. Limitations are
written into the report: NFIP take-up varies by zip, zip level hides
within-zip variation, and a claim count is not a flooded-home count.

Usage:  python validation/nfip_flag_check.py harvey ian
Writes: outputs/nfip_validation_{event}.json
"""
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = 'https://www.fema.gov/api/open/v2/FimaNfipClaims'
WINDOWS = {'harvey': ('2017-08-24', '2017-09-08'), 'ian': ('2022-09-26', '2022-10-06')}
CONFIRMED_MIN_CLAIMS = 25
QUIET_MAX_CLAIMS = 2


def zip_of(addr):
    m = re.findall(r'\b(\d{5})\b', str(addr or ''))
    return m[-1] if m else None


def nfip_zip(zipc, start, end, session=requests):
    base = (f"reportedZipCode eq '{zipc}' and dateOfLoss ge '{start}T00:00:00.000Z' "
            f"and dateOfLoss le '{end}T23:59:59.000Z'")
    for attempt in range(4):
        try:
            r = session.get(API, params={'$filter': base, '$inlinecount': 'allpages', '$top': 1000,
                                         '$select': 'waterDepth,amountPaidOnBuildingClaim'}, timeout=60)
            if r.status_code == 200:
                d = r.json()
                rows = d.get('FimaNfipClaims', [])
                depth = [float(x['waterDepth']) for x in rows if x.get('waterDepth') not in (None, '')]
                paid = [float(x['amountPaidOnBuildingClaim'] or 0) for x in rows]
                return {'claims': int(d['metadata'].get('count', len(rows))),
                        'median_water_depth_in': float(np.median(depth)) if depth else None,
                        'median_building_paid_usd': float(np.median(paid)) if paid else None}
        except (requests.RequestException, ValueError, KeyError):
            pass
        time.sleep(2 * (attempt + 1))
    return None


def run(event_id):
    from backend.database import load_event_data, load_event_intel
    df = pd.read_csv(ROOT / 'outputs' / f'{event_id}_final.csv')
    intel = load_event_intel(event_id) or {}
    by = intel.get('properties', {})
    df['zip'] = df['address'].apply(zip_of)
    df = df.dropna(subset=['zip'])
    start, end = WINDOWS[event_id]
    zips = {}
    for z in sorted(df['zip'].unique()):
        zips[z] = nfip_zip(z, start, end)
        print(f'  {z}: {zips[z]}')
    df['nfip_claims'] = df['zip'].map(lambda z: (zips.get(z) or {}).get('claims'))
    df['held'] = df['property_id'].map(lambda p: bool((by.get(str(p)) or {}).get('class_override')))
    df['transient_alert'] = df['property_id'].map(lambda p: any(
        f['code'] == 'TRANSIENT_MISS' and f['level'] == 'alert' for f in (by.get(str(p)) or {}).get('flags', [])))
    deny = df[df['impact_class'] == 'Remote-Deny']
    conf = deny[deny['nfip_claims'] >= CONFIRMED_MIN_CLAIMS]
    quiet = deny[deny['nfip_claims'] <= QUIET_MAX_CLAIMS]
    zdf = df.groupby('zip').agg(n=('property_id', 'count'), claims=('nfip_claims', 'first'),
                                held_share=('held', 'mean'), sar_flooded=('max_depth_ft', lambda s: float((s > 0.3).mean())))
    from scipy.stats import spearmanr
    rho = spearmanr(zdf['claims'], zdf['held_share']).correlation if len(zdf) >= 5 else None
    report = {
        'event_id': event_id, 'truth': 'FEMA NFIP redacted claims (OpenFEMA FimaNfipClaims), zip level',
        'window': [start, end], 'thresholds': {'confirmed_min_claims': CONFIRMED_MIN_CLAIMS,
                                               'quiet_max_claims': QUIET_MAX_CLAIMS},
        'zips': {z: v for z, v in zips.items()},
        'properties_with_zip': int(len(df)),
        'sar_only_remote_deny': int(len(deny)),
        'sar_only_deny_in_flood_confirmed_zips': int(len(conf)),
        'catch_rate': None if conf.empty else round(float(conf['held'].mean()), 3),
        'sar_only_deny_in_quiet_zips': int(len(quiet)),
        'quiet_hold_rate': None if quiet.empty else round(float(quiet['held'].mean()), 3),
        'nfip_claims_total_in_sample_zips': int(sum((v or {}).get('claims', 0) for v in zips.values())),
        'zip_spearman_claims_vs_held_share': None if rho is None or np.isnan(rho) else round(float(rho), 3),
        'zip_table': [{'zip': z, 'properties': int(r.n), 'nfip_claims': None if pd.isna(r.claims) else int(r.claims),
                       'held_share': round(float(r.held_share), 3), 'sar_flooded_share': round(float(r.sar_flooded), 3)}
                      for z, r in zdf.sort_values('claims', ascending=False).iterrows()],
        'limitations': [
            'Zip-level truth: NFIP claims are released without addresses.',
            'NFIP take-up differs by zip; a claim count is not a count of flooded homes.',
            'Quiet zips may still contain uninsured flooding; the quiet-hold rate is a proxy for false alarms.',
        ],
        'generated_at': pd.Timestamp.utcnow().isoformat(),
    }
    out = ROOT / 'outputs' / f'nfip_validation_{event_id}.json'
    out.write_text(json.dumps(report, indent=1, default=str))
    print(f'✓ {out.name}: SAR-only deny {len(deny)}, in flood-confirmed zips {len(conf)} → caught '
          f'{report["catch_rate"]}; quiet zips {len(quiet)} → held {report["quiet_hold_rate"]}')
    return report


if __name__ == '__main__':
    for ev in (sys.argv[1:] or ['harvey', 'ian']):
        run(ev)
