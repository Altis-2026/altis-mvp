#!/usr/bin/env python3
"""
per_pass_probe.py — Is the median composite hiding the flood?

THE HYPOTHESIS
--------------
`load_sar_composite` returns `collection.median()` over the whole post-event
window, and every flood mask in the pipeline is computed on that median.

Brazos has 3 DESCENDING scenes in its post window (30 Aug, 5 Sep, 11 Sep)
against a Brazos crest of roughly 1 September. A pixel that is flooded on ONE
of those three passes has a DRY median — the median picks the middle value, and
two dry observations outvote one wet one. The flood is then invisible to
everything downstream no matter how good the thresholding, the baseline, the
dual-pol corroboration or the terrain gating is.

That would explain the measurement in docs/DETECTION_LIMITS.md §10: zero
detections at 18 surveyed flood sites in open riverine floodplain, with real
terrain values sampling correctly at the same points.

THE TEST
--------
Hold everything else identical — same baseline, same slope mask, same permanent
water, same range-guarded Otsu, same z-threshold — and change only WHAT the
mask is computed on:

  MEDIAN   (current): one composite per orbit -> one threshold -> one mask,
                      union across orbits.
  PER-PASS (candidate): every individual scene -> its OWN Otsu threshold ->
                      its own mask, union across all scenes and orbits.

Both are sampled at the same USGS high water marks, so the comparison is
against surveyed depths rather than against each other.

It also carries ONE BAND PER SCENE, so a positive result can be explained
rather than merely reported: if the flood is visible on 30 August and gone by
11 September, that shows up directly as a per-date detection count, and the
median's behaviour follows from it arithmetically.

WHAT A POSITIVE RESULT WOULD AND WOULD NOT MEAN
-----------------------------------------------
Per-pass union is strictly more permissive than a median — it can only ADD
detections, never remove them. So a recall gain here is NOT self-evidently a
win: a detector that fires on every transient speckle would also "improve"
recall against a dataset made entirely of flooded points (see §10 — HWMs define
no negative class).

To keep that honest this probe also reports, for both variants, the flood
fraction over the ENTIRE study bbox. That is the closest thing to a
false-positive proxy available without dry ground truth: the bbox is mostly dry
land, so a variant that lights up a large fraction of it is buying recall with
indiscriminate firing. Read the two numbers together, never the recall alone.

Usage: python validation/per_pass_probe.py [event] [--radius M]
Writes outputs/per_pass_probe_<event>.csv and _<event>.json.
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


def post_scene_dates(bbox, start, end):
    """
    Every individual Sentinel-1 VV IW scene in the post window, with its date
    and orbit — the units the median currently collapses together.
    """
    import ee
    geom = ee.Geometry.Rectangle(bbox)
    coll = (ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(geom)
            .filterDate(start, end)
            .filter(ee.Filter.eq('instrumentMode', 'IW'))
            .filter(ee.Filter.listContains(
                'transmitterReceiverPolarisation', 'VV')))
    info = coll.aggregate_array('system:time_start').getInfo()
    orbits = coll.aggregate_array('orbitProperties_pass').getInfo()
    seen, out = set(), []
    for t, o in sorted(zip(info, orbits)):
        day = dt.datetime.utcfromtimestamp(t / 1000).strftime('%Y-%m-%d')
        if (day, o) in seen:
            continue
        seen.add((day, o))
        out.append((day, o))
    return out


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
                              baseline_window, guarded_otsu, orbit_flood_mask)
    import hwm_check

    cfg = getattr(config, EVENTS[args.event])
    bbox = cfg['bbox']
    radius = args.radius or cfg.get('exposure_radius_m') or 50

    print(f"=== {args.event}: median vs per-pass flood mask ===")
    print(f"  bbox {bbox}  post {cfg['post_start']} → {cfg['post_end']}",
          flush=True)

    # ── Ground truth: the same marks §10 measured against.
    marks = hwm_check.fetch_hwms()
    gt, report = hwm_check.hwms_in_bbox(marks, bbox)
    print(f"  {report['usable']} surveyed marks across "
          f"{gt['site_id'].nunique()} sites", flush=True)

    # ── Shared ingredients. Identical for both variants by construction:
    #    building them once is what makes this a controlled comparison rather
    #    than two pipelines that happen to differ in more than one place.
    dem, dem_res = load_dem(bbox)
    slope_mask = ee.Terrain.slope(dem).lt(5)
    permanent = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
                 .select('seasonality').gte(8).unmask(0))
    base_start, base_end = baseline_window(cfg['post_start'])

    scenes = post_scene_dates(bbox, cfg['post_start'], cfg['post_end'])
    print(f"\n  {len(scenes)} individual post-event scenes:")
    for day, orbit in scenes:
        print(f"    {day}  {orbit}")

    orbits = sorted({o for _, o in scenes})
    baselines = {}
    for orbit in orbits:
        mean, std, n = load_sar_baseline(bbox, base_start, base_end, orbit)
        baselines[orbit] = (mean, std, n) if n >= BASELINE['min_scenes'] else (None, None, n)
        print(f"  baseline {orbit}: {n} scenes"
              f"{'' if n >= BASELINE['min_scenes'] else ' — TOO FEW, falls back'}")

    # ── Variant A: MEDIAN, exactly what the pipeline does today.
    print("\n  Building MEDIAN masks (current behaviour)...", flush=True)
    median_masks = []
    for orbit in orbits:
        comp, n_scenes, _ = load_sar_composite(
            bbox, cfg['post_start'], cfg['post_end'], orbit_pass=orbit)
        try:
            pre, _, _ = load_sar_composite(
                bbox, cfg['pre_start'], cfg['pre_end'], orbit_pass=orbit)
        except ValueError:
            pre = None
        bm, bs, _ = baselines[orbit]
        median_masks.append(orbit_flood_mask(
            comp, guarded_otsu(comp, bbox, evaluate=True),
            slope_mask, permanent, pre_img=pre,
            baseline_mean=bm, baseline_std=bs))
        print(f"    {orbit}: median of {n_scenes} scenes", flush=True)

    median_mask = median_masks[0]
    for m in median_masks[1:]:
        median_mask = median_mask.Or(m)

    # ── Variant B: PER-PASS, one mask per scene, unioned.
    print("\n  Building PER-PASS masks (one per scene)...", flush=True)
    per_scene_bands, per_pass_mask = {}, None
    for day, orbit in scenes:
        nxt = (dt.datetime.strptime(day, '%Y-%m-%d')
               + dt.timedelta(days=1)).strftime('%Y-%m-%d')
        try:
            scene, n, _ = load_sar_composite(bbox, day, nxt, orbit_pass=orbit)
        except ValueError:
            print(f"    {day} {orbit}: no usable scene, skipped")
            continue
        try:
            pre, _, _ = load_sar_composite(
                bbox, cfg['pre_start'], cfg['pre_end'], orbit_pass=orbit)
        except ValueError:
            pre = None
        bm, bs, _ = baselines[orbit]
        m = orbit_flood_mask(
            scene, guarded_otsu(scene, bbox, evaluate=True),
            slope_mask, permanent, pre_img=pre,
            baseline_mean=bm, baseline_std=bs)
        label = f"f_{day.replace('-', '')}_{orbit[:4].lower()}"
        per_scene_bands[label] = m
        per_pass_mask = m if per_pass_mask is None else per_pass_mask.Or(m)
        print(f"    {day} {orbit}: mask built ({label})", flush=True)

    if per_pass_mask is None:
        sys.exit("  No per-scene masks could be built — nothing to compare.")

    # ── The dry-land control. Without a negative class this is the only guard
    #    against "recall improved because the mask fires everywhere".
    print("\n  Measuring bbox-wide flood fraction for both variants...",
          flush=True)
    geom = ee.Geometry.Rectangle(bbox)
    cover = ee.Image.cat([
        median_mask.rename('median'), per_pass_mask.rename('per_pass')
    ]).reduceRegion(reducer=ee.Reducer.mean(), geometry=geom,
                    scale=100, maxPixels=1e9, bestEffort=True).getInfo()
    print(f"    median   flags {cover.get('median', 0) * 100:.3f}% of the bbox")
    print(f"    per-pass flags {cover.get('per_pass', 0) * 100:.3f}% of the bbox")

    # ── Sample every variant and every individual scene at the marks.
    combined = ee.Image.cat(
        [median_mask.rename('flood_median'),
         per_pass_mask.rename('flood_per_pass')]
        + [b.rename(k) for k, b in per_scene_bands.items()])

    print(f"\n  Sampling {len(gt)} marks at {radius:.0f} m ...", flush=True)
    rows = []
    feats = [ee.Feature(
        ee.Geometry.Point([float(r.longitude), float(r.latitude)]).buffer(radius),
        {'property_id': str(r.property_id)}) for r in gt.itertuples()]
    for i in range(0, len(feats), 50):
        got = combined.reduceRegions(
            collection=ee.FeatureCollection(feats[i:i + 50]),
            reducer=ee.Reducer.max(), scale=30).getInfo()
        for f in got.get('features', []):
            rows.append(f.get('properties', {}))
        print(f"    batch {i // 50 + 1} done", flush=True)

    got_df = pd.DataFrame(rows)
    df = gt.merge(got_df, on='property_id', how='left')
    band_cols = ['flood_median', 'flood_per_pass'] + list(per_scene_bands)
    for c in band_cols:
        if c not in df:
            df[c] = 0.0
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)

    def site_recall(col):
        hit = df.groupby(df['site_id'].fillna(-1))[col].max() > 0
        return int(hit.sum()), int(len(hit))

    print("\n" + "=" * 62)
    print(f"  RESULT — {args.event}")
    print("=" * 62)
    km, nm = site_recall('flood_median')
    kp, np_ = site_recall('flood_per_pass')
    print(f"  MEDIAN   (current):  {int((df.flood_median > 0).sum())}/{len(df)} marks, "
          f"{km}/{nm} sites, {cover.get('median', 0) * 100:.3f}% of bbox flagged")
    print(f"  PER-PASS (candidate):{int((df.flood_per_pass > 0).sum())}/{len(df)} marks, "
          f"{kp}/{np_} sites, {cover.get('per_pass', 0) * 100:.3f}% of bbox flagged")

    print("\n  Detections per individual scene (which pass carried the flood):")
    for label in per_scene_bands:
        k, n = site_recall(label)
        print(f"    {label}: {int((df[label] > 0).sum()):>3}/{len(df)} marks, "
              f"{k}/{n} sites")

    csv = OUT / f"per_pass_probe_{args.event}.csv"
    df.to_csv(csv, index=False)
    (OUT / f"per_pass_probe_{args.event}.json").write_text(json.dumps({
        'event': args.event,
        'bbox': bbox,
        'post_window': [cfg['post_start'], cfg['post_end']],
        'scenes': [{'date': d, 'orbit': o} for d, o in scenes],
        'radius_m': radius,
        'marks': int(len(df)), 'sites': int(df['site_id'].nunique()),
        'median': {'marks': int((df.flood_median > 0).sum()),
                   'sites': km, 'bbox_fraction': cover.get('median')},
        'per_pass': {'marks': int((df.flood_per_pass > 0).sum()),
                     'sites': kp, 'bbox_fraction': cover.get('per_pass')},
        'per_scene': {k: {'marks': int((df[k] > 0).sum()),
                          'sites': site_recall(k)[0]} for k in per_scene_bands},
        'caveat': 'Per-pass union is strictly more permissive than a median and '
                  'can only add detections. Read recall together with the '
                  'bbox_fraction dry-land control — HWMs contain no negative '
                  'class, so recall alone cannot show a variant is better.',
    }, indent=2, default=str))
    print(f"\n✓ {csv.relative_to(BASE)}")


if __name__ == '__main__':
    main()
