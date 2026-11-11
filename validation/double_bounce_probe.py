#!/usr/bin/env python3
"""
double_bounce_probe.py — Phase 4e measurement.

THE QUESTION
------------
Phase 4c established that the detector is blind where the claims are:

    flooded-truth zips (5):  90.2% urban, mean detection 0.0993%
    dry-truth zips     (9):  80.0% urban, mean detection 0.1520%

Detection is LOWER where properties actually flooded. The diagnosis was that
the open-water detector only tests for DARKENING, while water standing against
a building wall forms a dihedral corner reflector and BRIGHTENS the return.

This measures whether `double_bounce_score` recovers that missing signal.

WHY THIS AND NOT THE FULL PIPELINE
-----------------------------------
A full 03_flood_pipeline run costs ~20-30 minutes, most of it NSI fetching and
structure-snapped sampling. Neither affects whether the double-bounce band
FIRES — the geometry snap moves a sample by tens of metres, it does not change
the physics. So this samples the band directly at the geocoded points and
answers the question in a few minutes.

It also carries the raw ingredients (z-score, urban flag, HAND, absolute VV)
so that a zero can be EXPLAINED — was it the urban gate, the HAND gate, the
absolute-return floor, or simply no brightening? — rather than merely reported.

Writes outputs/double_bounce_probe_<event>.csv and prints the verdict.

Usage: python validation/double_bounce_probe.py [event]
"""
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))

import ee  # noqa: E402
from backend.live_pipeline import init_ee  # noqa: E402

OUT = BASE / "outputs"

EVENTS = {'brazos': 'BRAZOS', 'harvey': 'HARVEY'}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else 'brazos'
    if event not in EVENTS:
        sys.exit(f"Unknown event '{event}'. Choose from {list(EVENTS)}.")

    init_ee()
    import config
    from config import BASELINE, DOUBLE_BOUNCE
    from flood_detect import (load_sar_composite, load_sar_baseline,
                              load_sar_orbits, load_hand, baseline_window,
                              double_bounce_score, urban_built_mask,
                              guarded_otsu, orbit_flood_mask)

    cfg = getattr(config, EVENTS[event])
    bbox = cfg['bbox']

    print(f"=== {event}: Phase 4e double-bounce probe ===")
    print(f"  gates: z>={DOUBLE_BOUNCE['z_threshold']}, "
          f"min_backscatter={DOUBLE_BOUNCE['min_backscatter_db']} dB, "
          f"max_hand={DOUBLE_BOUNCE.get('max_hand_ft') or 'HAND plausible_ft'}",
          flush=True)

    post, n_post, orbit = load_sar_composite(
        bbox, cfg['post_start'], cfg['post_end'])
    print(f"  post: {n_post} scenes, orbit {orbit}", flush=True)

    bs, be = baseline_window(cfg['post_start'])
    mean, std, n_base = load_sar_baseline(bbox, bs, be, orbit)
    if n_base < BASELINE['min_scenes']:
        sys.exit(f"Only {n_base} baseline scenes — double-bounce needs a "
                 f"multi-temporal baseline to measure a z-score against.")
    print(f"  baseline: {n_base} scenes", flush=True)

    hand, hand_src = load_hand(bbox)
    perm = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
            .select('seasonality').gte(8).unmask(0))
    slope_ok = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003")).lt(5)

    db = double_bounce_score(post, mean, std, hand=hand, permanent_water=perm)

    # The existing darkening detector, for a like-for-like comparison on the
    # SAME scenes. Without this the double-bounce numbers have nothing to beat.
    dark = orbit_flood_mask(post, guarded_otsu(post, bbox, evaluate=True),
                            slope_ok, perm, baseline_mean=mean, baseline_std=std)

    for other, (comp, n) in load_sar_orbits(
            bbox, cfg['post_start'], cfg['post_end']).items():
        if other == orbit:
            continue
        m2, s2, n2 = load_sar_baseline(bbox, bs, be, other)
        if n2 < BASELINE['min_scenes']:
            continue
        db = db.max(double_bounce_score(comp, m2, s2, hand=hand,
                                        permanent_water=perm))
        dark = dark.Or(orbit_flood_mask(
            comp, guarded_otsu(comp, bbox, evaluate=True), slope_ok, perm,
            baseline_mean=m2, baseline_std=s2))
        print(f"  cross-orbit {other}: {n} post, {n2} baseline", flush=True)

    z = post.subtract(mean).divide(std.max(BASELINE['min_std_db']))
    img = (db.rename('db')
           .addBands(dark.rename('dark').float())
           .addBands(z.rename('z'))
           .addBands(urban_built_mask().rename('urban'))
           .addBands((hand.unmask(-1) if hand is not None
                      else ee.Image(-1)).rename('hand'))
           .addBands(post.rename('vv')))

    props = pd.read_csv(OUT / f'{event}_properties.csv')
    props['property_id'] = props['property_id'].astype(str)
    zpath = OUT / f'{event}_zips.csv'
    if zpath.exists():
        zips = pd.read_csv(zpath)
        zips['property_id'] = zips['property_id'].astype(str)
        props = props.merge(zips[['property_id', 'zip']],
                            on='property_id', how='left')
    print(f"  {len(props)} properties", flush=True)

    radius = cfg.get('exposure_radius_m', 50)
    rows, B = [], 250
    for i in range(0, len(props), B):
        chunk = props.iloc[i:i + B]
        feats = [ee.Feature(
            ee.Geometry.Point([float(r.longitude), float(r.latitude)]).buffer(radius),
            {'pid': str(r.property_id)}) for r in chunk.itertuples()]
        try:
            got = img.reduceRegions(collection=ee.FeatureCollection(feats),
                                    reducer=ee.Reducer.mean(), scale=30).getInfo()
            for f in got.get('features', []):
                p = f['properties']
                rows.append({'property_id': p.get('pid'), 'db': p.get('db'),
                             'dark': p.get('dark'), 'z': p.get('z'),
                             'urban': p.get('urban'), 'hand': p.get('hand'),
                             'vv': p.get('vv')})
        except Exception as e:
            print(f"  batch {i} FAILED: {e}", flush=True)
        print(f"  sampled {min(i + B, len(props))}/{len(props)}", flush=True)

    s = pd.DataFrame(rows)
    dest = OUT / f'double_bounce_probe_{event}.csv'
    s.to_csv(dest, index=False)
    print(f"\n  written -> {dest}  ({len(s)} rows)", flush=True)

    report(s, props, event)


def report(s, props, event):
    """Verdict against NFIP truth, if labels exist."""
    lab_path = OUT / f'{event}_labels.csv'
    print(f"\nSIGNAL DENSITY")
    print(f"  darkening detector : {int((s['dark'] > 0).sum()):>5} of {len(s)} "
          f"({(s['dark'] > 0).mean() * 100:.2f}%)")
    print(f"  double-bounce      : {int((s['db'] > 0).sum()):>5} of {len(s)} "
          f"({(s['db'] > 0).mean() * 100:.2f}%)")
    either = ((s['dark'] > 0) | (s['db'] > 0))
    print(f"  either             : {int(either.sum()):>5} of {len(s)} "
          f"({either.mean() * 100:.2f}%)")

    if not lab_path.exists():
        print(f"\n  {lab_path.name} absent — run validation for the verdict.")
        return

    lab = pd.read_csv(lab_path)
    lab['property_id'] = lab['property_id'].astype(str)
    m = s.merge(lab[['property_id', 'zip', 'flooded_truth']],
                on='property_id', how='inner').dropna(subset=['flooded_truth'])
    y = m['flooded_truth'].astype(int)
    base = y.mean()
    print(f"\nLABELLED: {len(m)} properties, {int(y.sum())} flooded-truth "
          f"({base * 100:.1f}% base rate), {m['zip'].nunique()} zips")

    print(f"\n{'signal':>18} {'flagged':>8} {'true':>6} {'precision':>10} "
          f"{'lift':>6} {'recall':>8}")
    for name, flag in (('darkening', m['dark'] > 0),
                       ('double-bounce', m['db'] > 0),
                       ('either', (m['dark'] > 0) | (m['db'] > 0))):
        n = int(flag.sum())
        if n == 0:
            print(f"{name:>18} {0:>8} {'-':>6} {'-':>10} {'-':>6} {'-':>8}")
            continue
        k = int(y[flag].sum())
        print(f"{name:>18} {n:>8} {k:>6} {k / n * 100:>9.1f}% "
              f"{(k / n) / base:>6.2f} {k / y.sum() * 100:>7.1f}%")

    # The decisive table: does it fire in the urban zips that were blind?
    print(f"\nPER-ZIP (truth 1 = flooded; the urban zips were the blind spot)")
    zt = m.groupby('zip').agg(
        truth=('flooded_truth', 'first'), n=('db', 'size'),
        urban=('urban', 'mean'), dark=('dark', lambda v: (v > 0).mean() * 100),
        db=('db', lambda v: (v > 0).mean() * 100)).sort_values(
            ['truth', 'db'], ascending=[False, False])
    print(zt.to_string(float_format=lambda v: f"{v:.3f}"))

    for t in (1, 0):
        sub = zt[zt.truth == t]
        if len(sub):
            print(f"  truth={t}: {len(sub)} zips, mean urban {sub.urban.mean() * 100:.1f}%, "
                  f"darkening {sub.dark.mean():.3f}%, double-bounce {sub.db.mean():.3f}%")


if __name__ == '__main__':
    main()
