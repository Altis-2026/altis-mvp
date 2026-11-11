#!/usr/bin/env python3
"""
phase4_probe.py — Re-test every shelved detector against POINT ground truth.

WHY THIS EXISTS
---------------
Phases 4a (sub-pixel water fraction), 4b (dual-pol) and 4e (urban
double-bounce) were each built, measured against ZIP-level NFIP labels, and
shelved as "not proven". docs/DETECTION_LIMITS.md §10 then established that
those labels carry 14 independent bits and could not have distinguished a good
detector from a bad one. "Doesn't help against 14 bits" and "doesn't help" are
different findings, and only the first was ever demonstrated.

There is now a test with real power: 91 surveyed high water marks across 66
independent sites, each with a measured depth.

WHAT THE GATE PROBE FOUND, AND WHY IT AIMS THIS PROBE
------------------------------------------------------
`gate_probe.py` decomposed the zero at Brazos into the specific gate that
closes, and the answer was not the one anyone expected:

    SLOPE      28/28 marks pass        terrain is flat (mean 1.96°)
    PERMWATER  28/28 marks pass        not permanent water
    ABSOLUTE    0/28 marks pass        VV mean -10.19 dB vs -16.00 dB threshold
    CHANGE      1/28 marks pass        mean z = +0.39σ

The terrain gates are innocent — these marks sit on flat, floodable, non-water
ground, so §10's recall figure is NOT an artifact of where USGS surveys. The
radiometry rejects all 28.

And the direction matters enormously: **mean z is POSITIVE**. At surveyed flood
locations the C-band return is very slightly BRIGHTER than that pixel's own
12-month baseline, not darker. An open-water detector looks for darkening. It
is looking for the wrong sign.

That is precisely the mechanism Phase 4e was built for (water against a wall
forms a dihedral corner reflector and returns MORE energy), and it is the
confound Phase 4b was built to separate. Both were shelved on evidence that
could not have detected them. This probe measures all of them where the truth
is known.

HOW TO READ THE OUTPUT
----------------------
Every signal is reported with BOTH:

  - recall at the marks (how often it fires where water demonstrably was), and
  - the fraction of the whole study bbox it fires on (the dry-land control).

HWMs contain no negative class (§10), so recall alone cannot show a signal is
good — a detector that fires everywhere scores 100%. The bbox fraction is the
nearest available false-positive proxy: the study area is overwhelmingly dry
land. A signal is only interesting if recall rises MUCH faster than bbox
coverage. That ratio is reported as `lift`.

Usage: python validation/phase4_probe.py [event] [--radius M]
Writes outputs/phase4_probe_<event>.csv and _<event>.json.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))
sys.path.insert(0, str(BASE / "validation"))

OUT = BASE / "outputs"
EVENTS = {'brazos': 'BRAZOS', 'harvey': 'HARVEY'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('event', nargs='?', default='brazos', choices=sorted(EVENTS))
    ap.add_argument('--radius', type=float, default=None)
    args = ap.parse_args()

    from backend.live_pipeline import init_ee
    init_ee()
    import ee
    import config
    from config import BASELINE, DUALPOL, SUBPIXEL
    from flood_detect import (load_dem, load_sar_composite, load_sar_baseline,
                              baseline_window, guarded_otsu, load_hand,
                              load_sar_vh_composite, orbit_flood_mask,
                              water_fraction, dualpol_water_score,
                              double_bounce_score, load_optical_water_mask)
    import hwm_check

    cfg = getattr(config, EVENTS[args.event])
    bbox = cfg['bbox']
    radius = args.radius or cfg.get('exposure_radius_m') or 50

    print(f"=== {args.event}: every shelved detector vs surveyed points ===",
          flush=True)
    marks = hwm_check.fetch_hwms()
    gt, _ = hwm_check.hwms_in_bbox(marks, bbox)
    print(f"  {len(gt)} marks, {gt['site_id'].nunique()} sites", flush=True)

    dem, _ = load_dem(bbox)
    slope_mask = ee.Terrain.slope(dem).lt(5)
    permanent = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
                 .select('seasonality').gte(8).unmask(0))
    hand, _ = load_hand(bbox)
    base_start, base_end = baseline_window(cfg['post_start'])

    post, n_post, orbit = load_sar_composite(
        bbox, cfg['post_start'], cfg['post_end'])
    pre, _, _ = load_sar_composite(
        bbox, cfg['pre_start'], cfg['pre_end'], orbit_pass=orbit)
    bmean, bstd, n_base = load_sar_baseline(bbox, base_start, base_end, orbit)
    print(f"  orbit {orbit}: {n_post} post, {n_base} baseline scenes",
          flush=True)

    thr = guarded_otsu(post, bbox, evaluate=True)
    bands = {}

    # ── Baseline for comparison: the detector that ships today.
    bands['open_water'] = orbit_flood_mask(
        post, thr, slope_mask, permanent, pre_img=pre,
        baseline_mean=bmean, baseline_std=bstd)

    # ── Phase 4a: sub-pixel water fraction. Shelved after AUC 0.4862 (p=0.92)
    #    against zip labels, diagnosed as a wet-soil confound.
    bands['subpixel'] = water_fraction(
        post, bmean, baseline_std=bstd, slope_mask=slope_mask,
        permanent_water=permanent).gt(SUBPIXEL.get('min_fraction', 0.1))

    # ── Phase 4b: dual-pol. The only one that ever cleared chance.
    post_vh, n_vh = load_sar_vh_composite(
        bbox, cfg['post_start'], cfg['post_end'], orbit)
    if post_vh is not None:
        vh_mean, vh_std, n_vh_base = load_sar_baseline(
            bbox, base_start, base_end, orbit, band='VH')
        print(f"  VH: {n_vh} post, {n_vh_base} baseline scenes", flush=True)
        if n_vh_base >= DUALPOL['min_vh_baseline_scenes']:
            bands['dualpol'] = dualpol_water_score(
                post, post_vh, bmean, bstd, vh_mean, vh_std,
                slope_mask=slope_mask, permanent_water=permanent).gt(0)
            # The raw VH z-score, so a dual-pol zero can be explained.
            bands['vh_z'] = post_vh.subtract(vh_mean).divide(
                vh_std.max(BASELINE['min_std_db']))
    else:
        print("  VH unavailable — dual-pol abstains", flush=True)

    # ── Phase 4e: urban double-bounce. The direct answer to a POSITIVE z.
    bands['double_bounce'] = double_bounce_score(
        post, bmean, bstd, hand=hand, permanent_water=permanent).gt(0)

    # ── Independent sensor: Sentinel-2 MNDWI. Not a SAR variant at all, which
    #    is exactly why it is worth carrying — it is the one signal here that
    #    cannot share SAR's blind spot.
    ow, ovalid, n_s2 = load_optical_water_mask(
        bbox, cfg['post_start'], cfg['post_end'])
    print(f"  Sentinel-2: {n_s2} cloud-filtered scenes", flush=True)
    if n_s2 > 0:
        bands['optical'] = ow.gt(0)
        bands['optical_valid'] = ovalid

    # Raw z, carried so the sign of the anomaly is visible per mark.
    bands['z'] = post.subtract(bmean).divide(bstd.max(BASELINE['min_std_db']))

    combined = ee.Image.cat([b.rename(k) for k, b in bands.items()])

    # ── Dry-land control, same layers over the whole bbox.
    print("\n  Measuring bbox-wide firing rate (dry-land control)...",
          flush=True)
    cover = combined.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=ee.Geometry.Rectangle(bbox),
        scale=100, maxPixels=1e9, bestEffort=True).getInfo()

    print(f"\n  Sampling {len(gt)} marks at {radius:.0f} m ...", flush=True)
    feats = [ee.Feature(
        ee.Geometry.Point([float(r.longitude), float(r.latitude)]).buffer(radius),
        {'property_id': str(r.property_id)}) for r in gt.itertuples()]
    rows = []
    for i in range(0, len(feats), 50):
        got = combined.reduceRegions(
            collection=ee.FeatureCollection(feats[i:i + 50]),
            reducer=ee.Reducer.max().combine(ee.Reducer.mean(),
                                             sharedInputs=True),
            scale=30).getInfo()
        rows += [f.get('properties', {}) for f in got.get('features', [])]
        print(f"    batch {i // 50 + 1}", flush=True)

    df = gt.merge(pd.DataFrame(rows), on='property_id', how='left')

    signals = [k for k in bands if k not in ('z', 'vh_z', 'optical_valid')]
    results = {}
    print("\n" + "=" * 74)
    print(f"  PHASE-4 RE-TEST — {args.event}  ({len(df)} marks, "
          f"{df['site_id'].nunique()} sites)")
    print("=" * 74)
    print(f"  {'signal':<16}{'marks':>10}{'sites':>10}"
          f"{'bbox fired':>13}{'lift':>10}")
    print("  " + "-" * 57)
    for s in signals:
        col = f'{s}_max'
        if col not in df:
            continue
        v = pd.to_numeric(df[col], errors='coerce').fillna(0)
        hit_marks = int((v > 0).sum())
        hit_sites = int((df.assign(_h=v > 0).groupby(
            df['site_id'].fillna(-1))['_h'].max()).sum())
        n_sites = int(df['site_id'].nunique())
        frac = cover.get(s) or 0.0
        recall = hit_marks / len(df) if len(df) else 0.0
        lift = (recall / frac) if frac > 0 else float('nan')
        results[s] = {'marks': hit_marks, 'n_marks': len(df),
                      'sites': hit_sites, 'n_sites': n_sites,
                      'bbox_fraction': frac, 'recall': recall, 'lift': lift}
        print(f"  {s:<16}{f'{hit_marks}/{len(df)}':>10}"
              f"{f'{hit_sites}/{n_sites}':>10}{frac * 100:>12.2f}%"
              f"{lift:>10.2f}")
    print("\n  lift = recall / bbox-fired-fraction. 1.0 means the signal fires "
          "at surveyed\n  flood points no more often than it fires on the "
          "study area at large —\n  i.e. it carries no information about "
          "flooding. Higher is better; a high\n  recall with a lift near 1 is "
          "a signal firing everywhere, not a detector.")

    zmean = pd.to_numeric(df.get('z_mean', pd.Series(dtype=float)),
                          errors='coerce').mean()
    print(f"\n  Mean VV z-score at surveyed flood points: {zmean:+.3f}σ "
          f"({'BRIGHTER' if zmean > 0 else 'darker'} than baseline)")
    if 'vh_z_mean' in df:
        vz = pd.to_numeric(df['vh_z_mean'], errors='coerce').mean()
        print(f"  Mean VH z-score at surveyed flood points: {vz:+.3f}σ")

    csv = OUT / f"phase4_probe_{args.event}.csv"
    df.to_csv(csv, index=False)
    (OUT / f"phase4_probe_{args.event}.json").write_text(json.dumps({
        'event': args.event, 'orbit': orbit, 'radius_m': radius,
        'post_scenes': n_post, 'baseline_scenes': n_base,
        's2_scenes': n_s2,
        'marks': len(df), 'sites': int(df['site_id'].nunique()),
        'mean_vv_z_at_marks': None if pd.isna(zmean) else float(zmean),
        'signals': results,
        'caveat': 'HWMs define no negative class, so recall alone cannot rank '
                  'these. bbox_fraction is a dry-land false-positive proxy and '
                  'lift = recall / bbox_fraction is the comparable number.',
    }, indent=2, default=str))
    print(f"\n✓ {csv.relative_to(BASE)}")


if __name__ == '__main__':
    main()
