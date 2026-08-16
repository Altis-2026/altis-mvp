#!/usr/bin/env python3
"""
gate_probe.py — WHY is the flood mask zero at surveyed flood points?

BACKGROUND
----------
docs/DETECTION_LIMITS.md §10 measured zero detections at 18 surveyed flood
sites in Brazos open floodplain. per_pass_probe.py then ruled out the leading
explanation: scoring every individual Sentinel-1 pass separately and unioning
them flags 8.8x more of the bbox (3.06% vs 0.35%) and still detects 0 of 28
marks. The median composite is not hiding the flood — no single pass sees it
either.

So the zero is not about temporal compositing. This probe decomposes it into
the specific gate that produces it.

`orbit_flood_mask` requires a pixel to pass ALL of:

  1. CHANGE    z = (post - baseline_mean) / max(baseline_std, min_std)
               must be <= -z_threshold  (default -2.0σ)
  2. ABSOLUTE  post VV must be < the range-guarded Otsu threshold
  3. SLOPE     terrain slope < 5°
  4. NOT PERMANENT WATER  (JRC seasonality >= 8 months is excluded)

A zero can come from any of them, and the fix is completely different in each
case. Reporting "we detect nothing" without saying which gate closed is the
difference between a diagnosis and a symptom.

THE CONFOUND THIS IS ALSO DESIGNED TO EXPOSE
--------------------------------------------
USGS crews survey high water marks where a mark SURVIVES and can be reached —
bridge abutments, culverts, channel banks, building walls near water. That is
not a random sample of insured residential parcels. Two gates could therefore
be rejecting these points for reasons that would NOT apply to a suburban lot:

  - SLOPE: a channel bank routinely exceeds 5°, and the slope gate then
    excludes the pixel by design.
  - PERMANENT WATER: a mark surveyed at a river's edge sits on or beside
    JRC permanent water, which the detector removes on purpose.

If the zeros are concentrated in those two gates, then §10's recall figure is
partly an artifact of WHERE high water marks are, and it understates recall on
the population we actually sell against. That would not make the detector good
— but it would change what the number means, and it has to be checked before
the number is quoted again.

Conversely, if the marks pass slope and permanent-water cleanly and fail on
CHANGE or ABSOLUTE, the detector is genuinely blind on flooded ground and the
recall figure stands as written.

Usage: python validation/gate_probe.py [event] [--radius M]
Writes outputs/gate_probe_<event>.csv and _<event>.json.
"""
import argparse
import datetime as dt
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
    from config import BASELINE, SAR
    from flood_detect import (load_dem, load_sar_composite, load_sar_baseline,
                              baseline_window, guarded_otsu, load_hand)
    import hwm_check

    cfg = getattr(config, EVENTS[args.event])
    bbox = cfg['bbox']
    radius = args.radius or cfg.get('exposure_radius_m') or 50

    print(f"=== {args.event}: which gate closes at surveyed flood points? ===",
          flush=True)

    marks = hwm_check.fetch_hwms()
    gt, report = hwm_check.hwms_in_bbox(marks, bbox)
    print(f"  {len(gt)} surveyed marks, {gt['site_id'].nunique()} sites",
          flush=True)

    dem, dem_res = load_dem(bbox)
    slope = ee.Terrain.slope(dem).rename('slope_deg')
    permanent = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
                 .select('seasonality').gte(8).unmask(0).rename('perm_water'))
    hand, hand_src = load_hand(bbox)

    base_start, base_end = baseline_window(cfg['post_start'])

    # The primary orbit, chosen exactly as the pipeline chooses it.
    post, n_post, orbit = load_sar_composite(
        bbox, cfg['post_start'], cfg['post_end'])
    bmean, bstd, n_base = load_sar_baseline(bbox, base_start, base_end, orbit)
    print(f"  orbit {orbit}: {n_post} post scenes, {n_base} baseline scenes",
          flush=True)
    if n_base < BASELINE['min_scenes']:
        sys.exit("  Baseline too thin — the z-score gate is not the one under "
                 "test here, so this probe cannot decompose the result.")

    thr = float(guarded_otsu(post, bbox, evaluate=True).getInfo())
    raw_thr = float(ee.Number(
        __import__('flood_detect').otsu_threshold_gee(post, bbox)).getInfo())
    guarded = abs(thr - raw_thr) > 1e-9
    print(f"  Otsu threshold: {thr:.2f} dB "
          f"(raw {raw_thr:.2f} dB{' — RANGE GUARD FIRED, fallback used' if guarded else ''})",
          flush=True)
    print(f"  z-threshold {BASELINE['z_threshold']}σ, "
          f"min_std {BASELINE['min_std_db']} dB, "
          f"require_absolute={BASELINE['require_absolute']}", flush=True)

    std = bstd.max(BASELINE['min_std_db'])
    z = post.subtract(bmean).divide(std).rename('z')

    layers = [post.rename('vv_db'), bmean.rename('base_mean_db'),
              bstd.rename('base_std_db'), z, slope, permanent,
              hand.rename('hand_ft')]

    # Each individual scene's own VV and its own z, so a mark that was flooded
    # on exactly one date can still be seen here.
    scenes = []
    coll = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(
        ee.Geometry.Rectangle(bbox)).filterDate(
        cfg['post_start'], cfg['post_end'])
        .filter(ee.Filter.eq('instrumentMode', 'IW'))
        .filter(ee.Filter.eq('orbitProperties_pass', orbit))
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')))
    for t in sorted(set(coll.aggregate_array('system:time_start').getInfo())):
        day = dt.datetime.utcfromtimestamp(t / 1000).strftime('%Y-%m-%d')
        if day in scenes:
            continue
        scenes.append(day)
        nxt = (dt.datetime.strptime(day, '%Y-%m-%d')
               + dt.timedelta(days=1)).strftime('%Y-%m-%d')
        img, _, _ = load_sar_composite(bbox, day, nxt, orbit_pass=orbit)
        tag = day.replace('-', '')
        layers.append(img.rename(f'vv_{tag}'))
        layers.append(img.subtract(bmean).divide(std).rename(f'z_{tag}'))
    print(f"  per-scene layers for {scenes}", flush=True)

    combined = ee.Image.cat(layers)

    print(f"\n  Sampling {len(gt)} marks at {radius:.0f} m ...", flush=True)
    feats = [ee.Feature(
        ee.Geometry.Point([float(r.longitude), float(r.latitude)]).buffer(radius),
        {'property_id': str(r.property_id)}) for r in gt.itertuples()]
    rows = []
    for i in range(0, len(feats), 50):
        got = combined.reduceRegions(
            collection=ee.FeatureCollection(feats[i:i + 50]),
            reducer=ee.Reducer.mean().combine(ee.Reducer.min(),
                                              sharedInputs=True),
            scale=30).getInfo()
        rows += [f.get('properties', {}) for f in got.get('features', [])]
        print(f"    batch {i // 50 + 1}", flush=True)

    df = gt.merge(pd.DataFrame(rows), on='property_id', how='left')

    # ── Decompose. `_min` is the most generous reading inside the buffer: the
    #    darkest pixel and the most negative z are the best chance the mark had
    #    of clearing each gate, so a failure on the min is a real failure.
    df['pass_absolute'] = df.get('vv_db_min', pd.Series(dtype=float)) < thr
    df['pass_change'] = df.get('z_min', pd.Series(dtype=float)) <= -BASELINE['z_threshold']
    df['pass_slope'] = df.get('slope_deg_min', pd.Series(dtype=float)) < 5
    df['pass_not_perm'] = df.get('perm_water_min', pd.Series(dtype=float)) < 1
    df['pass_all'] = (df.pass_absolute & df.pass_change
                      & df.pass_slope & df.pass_not_perm)

    n = len(df)
    print("\n" + "=" * 64)
    print(f"  GATE DECOMPOSITION — {args.event}  ({n} marks, "
          f"{df['site_id'].nunique()} sites)")
    print("=" * 64)
    for gate, label in [
        ('pass_absolute', f'ABSOLUTE  darkest pixel < {thr:.2f} dB Otsu'),
        ('pass_change',   f'CHANGE    min z <= -{BASELINE["z_threshold"]}σ'),
        ('pass_slope',    'SLOPE     terrain < 5°'),
        ('pass_not_perm', 'PERMWATER not JRC permanent water'),
    ]:
        k = int(df[gate].sum())
        print(f"  {label:<44} {k:>3}/{n} pass ({k / n * 100:.0f}%)")
    print(f"  {'ALL FOUR (would be flagged flooded)':<44} "
          f"{int(df.pass_all.sum()):>3}/{n}")

    print("\n  Of the marks that FAIL, the single gate responsible:")
    fails = df[~df.pass_all]
    only = {}
    for _, r in fails.iterrows():
        bad = [g for g in ('pass_absolute', 'pass_change', 'pass_slope',
                           'pass_not_perm') if not r[g]]
        only['+'.join(b.replace('pass_', '') for b in bad)] = \
            only.get('+'.join(b.replace('pass_', '') for b in bad), 0) + 1
    for k, v in sorted(only.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<40} {v:>3}")

    # The confound check the docstring promises.
    terrain_only = int(((~df.pass_slope) | (~df.pass_not_perm)).sum())
    print(f"\n  Marks excluded by a TERRAIN gate (slope or permanent water): "
          f"{terrain_only}/{n}")
    print("    These are the marks whose rejection may say more about where "
          "USGS surveys than about the detector.")
    radiometry_only = int((df.pass_slope & df.pass_not_perm
                           & ~(df.pass_absolute & df.pass_change)).sum())
    print(f"  Marks on VALID terrain that the RADIOMETRY still rejects: "
          f"{radiometry_only}/{n}")
    print("    These are genuine detector blindness on floodable ground.")

    print("\n  Backscatter reality check (mean over marks):")
    for c in ('vv_db_mean', 'base_mean_db_mean', 'z_mean', 'slope_deg_mean',
              'hand_ft_mean'):
        if c in df:
            print(f"    {c:<20} {pd.to_numeric(df[c], errors='coerce').mean():.2f}")
    print(f"    Otsu threshold        {thr:.2f} dB  <- VV must fall BELOW this")

    csv = OUT / f"gate_probe_{args.event}.csv"
    df.to_csv(csv, index=False)
    (OUT / f"gate_probe_{args.event}.json").write_text(json.dumps({
        'event': args.event, 'orbit': orbit, 'otsu_db': thr,
        'otsu_raw_db': raw_thr, 'otsu_range_guard_fired': guarded,
        'z_threshold': BASELINE['z_threshold'],
        'baseline_scenes': n_base, 'post_scenes': n_post, 'scenes': scenes,
        'hand_source': hand_src, 'radius_m': radius,
        'marks': n, 'sites': int(df['site_id'].nunique()),
        'gate_pass_counts': {g: int(df[g].sum()) for g in
                             ('pass_absolute', 'pass_change', 'pass_slope',
                              'pass_not_perm', 'pass_all')},
        'failure_combinations': only,
        'excluded_by_terrain_gate': terrain_only,
        'rejected_by_radiometry_on_valid_terrain': radiometry_only,
    }, indent=2, default=str))
    print(f"\n✓ {csv.relative_to(BASE)}")


if __name__ == '__main__':
    main()
