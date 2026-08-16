#!/usr/bin/env python3
"""
hwm_check.py — Validate detected depth against SURVEYED depth, point by point.

THE PROBLEM THIS EXISTS TO SOLVE
--------------------------------
Every accuracy number in this project has been capped by the same ceiling:
NFIP redacted claims are published at ZIP resolution, so the 3,980-row Brazos
validation set carries 14 independent bits of information. Three detector
improvements (Phases 4a, 4d, 4e) were built, measured, and shelved as "not
proven" — not because they were shown to fail, but because a 14-zip test has
almost no power to tell a good detector from a bad one.

USGS Short-Term Network high water marks break that ceiling. They are
GPS-tagged points where a survey crew measured how far the water rose above the
ground surface, in feet, at that spot. That is:

  - POINT-LEVEL, not zip-aggregated. Each mark is its own observation.
  - A MEASURED DEPTH, not a binary label. It validates `max_depth_ft`
    directly, with no threshold to argue about and no aggregation step.
  - Independent of us. Surveyed by USGS field crews in 2017, published since.

WHAT THIS CAN AND CANNOT MEASURE
--------------------------------
Read this before quoting any number this script prints.

  CAN: depth accuracy and bias at known-flooded locations; recall (did we see
       water where water demonstrably was); how error varies with surveyed
       depth, mark quality, and terrain.

  CANNOT: precision, or any false-positive rate. Every HWM is a place that
       flooded — the dataset contains no surveyed dry points, so it defines no
       negative class. A detector that returned "10 ft everywhere" would score
       perfect recall here. Precision still has to come from somewhere else.

  CAUTION: an HWM records the PEAK stage. Sentinel-1 observes an instant, every
       6-12 days. Where the pass missed the crest, a correct detector still
       reads low. Underestimate is therefore the EXPECTED result, not
       automatically an error — which is why bias is reported separately from
       scatter, and why the pass dates are printed alongside.

  CAUTION: marks cluster. Several can share one survey site, and points a few
       metres apart are not independent samples of the detector. Both the raw
       point count and the distinct-site count are reported; trust the latter.

ON `height_above_gnd == 0`
--------------------------
974 of the 2,364 Harvey marks carry exactly 0.0 here, and all 974 carry a
surveyed `elev_ft`. 670 of them are debris lines. A debris line deposited at
precisely 0.00 ft above ground is not a physical measurement, it is an unfilled
optional field, so those marks are treated as MISSING rather than as zero
depth. Counting them as zeros would manufacture ~900 fake "no flooding here"
ground-truth points out of a dataset that contains none — the exact kind of
fabricated negative class this project has refused elsewhere. The count of
marks dropped for this reason is printed on every run so the decision stays
visible and auditable rather than buried here.

Recovering them via elev_ft minus DEM ground elevation was considered and NOT
done: it would inherit metre-scale DEM error on a foot-scale quantity, and our
own depth product is derived from that same DEM, so the comparison would be
partly circular.

USAGE
-----
    python validation/hwm_check.py brazos
    python validation/hwm_check.py harvey --sweep
    python validation/hwm_check.py brazos --radius 30 --refresh

Writes outputs/hwm_check_<event>.csv (one row per mark, with the raw detector
columns so a result can be explained rather than only reported) and
outputs/hwm_check_<event>.json (the summary statistics).
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))

OUT = BASE / "outputs"

# USGS Short-Term Network, "2017 Harvey". Resolved from
# https://stn.wim.usgs.gov/STNServices/Events.json — event_name '2017 Harvey'.
STN_EVENT_ID = 180
STN_HWM_URL = "https://stn.wim.usgs.gov/STNServices/Events/{event}/HWMs.json"
HWM_CACHE = OUT / f"usgs_hwm_event{STN_EVENT_ID}.json"

# Both study areas are Hurricane Harvey, so both validate against STN event 180.
EVENTS = {'brazos': 'BRAZOS', 'harvey': 'HARVEY'}

# From https://stn.wim.usgs.gov/STNServices/HWMQualities.json — the surveyed
# vertical uncertainty of the mark itself. Worth carrying because it bounds how
# well ANY detector could agree: a "VP: > 0.40 ft" mark cannot adjudicate a
# tenth-of-a-foot difference.
HWM_QUALITY = {
    1: 'Excellent: +/- 0.05 ft', 2: 'Good: +/- 0.10 ft',
    3: 'Fair: +/- 0.20 ft',      4: 'Poor: +/- 0.40 ft',
    5: 'VP: > 0.40 ft',          6: 'Unknown/Historical',
}
# From https://stn.wim.usgs.gov/STNServices/HWMTypes.json
HWM_TYPE = {
    1: 'Mud', 2: 'Debris', 3: 'Clear water', 4: 'Vegetation line',
    5: 'Seed line', 6: 'Stain line', 7: 'Melted snow line',
    8: 'Present at peak (direct observation)', 9: 'Other', 11: 'Cut line',
    12: 'Wash line',
}

# A detected depth at or below this reads as "no water found here". Matches the
# 0.1 ft the batch pipeline uses to count a property as flooded, so recall
# measured here means the same thing as "flooded" everywhere else in the repo.
DETECT_FLOOR_FT = 0.1

# Radii swept by --sweep, metres. The default headline radius comes from the
# event's own exposure_radius_m so it matches the number the product ships;
# the sweep is printed IN FULL because a single flattering radius chosen after
# the fact is a result selected on its own outcome.
SWEEP_RADII_M = [10, 20, 30, 50, 100]
DEFAULT_RADIUS_M = 50


# ─── ground truth ────────────────────────────────────────────────────────────

def fetch_hwms(refresh=False):
    """Surveyed high water marks for the event, from cache unless --refresh."""
    if HWM_CACHE.exists() and not refresh:
        print(f"  HWMs from cache: {HWM_CACHE.name}")
        return json.loads(HWM_CACHE.read_text())

    url = STN_HWM_URL.format(event=STN_EVENT_ID)
    print(f"  Downloading {url} ...", flush=True)
    with urllib.request.urlopen(url, timeout=300) as r:
        data = json.loads(r.read().decode())
    HWM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    HWM_CACHE.write_text(json.dumps(data))
    print(f"  {len(data)} marks cached → {HWM_CACHE.name}")
    return data


def hwms_in_bbox(hwms, bbox):
    """
    Marks inside `bbox` that carry a usable surveyed depth.

    Returns (DataFrame, drop_report). The report is printed rather than
    discarded: how many marks were excluded, and for which reason, is part of
    the result — it is what stops a shrinking sample from passing as a clean one.
    """
    west, south, east, north = bbox
    inside = [h for h in hwms
              if h.get('longitude_dd') is not None
              and h.get('latitude_dd') is not None
              and west <= h['longitude_dd'] <= east
              and south <= h['latitude_dd'] <= north]

    n_null = sum(1 for h in inside if h.get('height_above_gnd') is None)
    n_zero = sum(1 for h in inside if h.get('height_above_gnd') == 0)
    usable = [h for h in inside
              if h.get('height_above_gnd') is not None
              and h['height_above_gnd'] > 0]

    df = pd.DataFrame([{
        'property_id':    str(h['hwm_id']),      # sample_properties keys on this
        'address':        f"HWM {h['hwm_id']}",
        'hwm_id':         h['hwm_id'],
        'site_id':        h.get('site_id'),
        'latitude':       h['latitude_dd'],
        'longitude':      h['longitude_dd'],
        'surveyed_ft':    float(h['height_above_gnd']),
        'hwm_quality':    HWM_QUALITY.get(h.get('hwm_quality_id'), 'unknown'),
        'hwm_type':       HWM_TYPE.get(h.get('hwm_type_id'), 'unknown'),
        'hwm_environment': h.get('hwm_environment'),
        'waterbody':      h.get('waterbody'),
        'flag_date':      h.get('flag_date'),
        'survey_date':    h.get('survey_date'),
    } for h in usable])

    report = {'in_bbox': len(inside), 'usable': len(usable),
              'dropped_null_height': n_null, 'dropped_zero_height': n_zero}
    return df, report


# ─── statistics ──────────────────────────────────────────────────────────────

def _corr(x, y):
    """Pearson and Spearman with p-values; None where undefined (n<3, no spread)."""
    out = {'pearson_r': None, 'pearson_p': None,
           'spearman_r': None, 'spearman_p': None}
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return out
    try:
        from scipy import stats
    except ImportError:  # pragma: no cover - scipy ships with the pipeline
        return out
    pr = stats.pearsonr(x, y)
    sr = stats.spearmanr(x, y)
    out.update(pearson_r=float(pr[0]), pearson_p=float(pr[1]),
               spearman_r=float(sr[0]), spearman_p=float(sr[1]))
    return out


def _bootstrap_ci(x, y, groups, n_boot=2000, seed=0):
    """
    Percentile CI for a correlation, resampled by SITE not by mark.

    Marks cluster: a survey crew records several at one site, and those are not
    independent draws. Resampling sites keeps the CI honest about how much
    information is actually present — the same grouped-resampling discipline
    the zip-level work already uses, applied at the resolution that matters here.
    """
    if len(x) < 3:
        return None, None
    rng = np.random.default_rng(seed)
    sites = np.asarray(groups)
    uniq = np.unique(sites)
    if len(uniq) < 3:
        return None, None
    vals = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([np.flatnonzero(sites == s) for s in pick])
        bx, by = x[idx], y[idx]
        if np.std(bx) == 0 or np.std(by) == 0:
            continue
        vals.append(np.corrcoef(bx, by)[0, 1])
    if len(vals) < 100:
        return None, None
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def summarize(df, label):
    """Accuracy of detected depth against surveyed depth for one sample."""
    n = len(df)
    if n == 0:
        return {'label': label, 'n_marks': 0}

    surveyed = df['surveyed_ft'].to_numpy(float)
    detected = df['max_depth_ft'].to_numpy(float)
    sites = df['site_id'].fillna(-1).to_numpy()
    found = detected > DETECT_FLOOR_FT

    s = {
        'label':            label,
        'n_marks':          int(n),
        'n_sites':          int(pd.Series(sites).nunique()),
        'surveyed_mean_ft': round(float(surveyed.mean()), 3),
        'surveyed_median_ft': round(float(np.median(surveyed)), 3),
        'detected_mean_ft': round(float(detected.mean()), 3),
        # Recall: of places we KNOW flooded, how many did we see any water at.
        'recall_at_0.1ft':  round(float(found.mean()), 4),
        'n_detected':       int(found.sum()),
        # Signed error over ALL marks, including the ones we missed as 0. This
        # is the number that answers "if we report a depth, how wrong is it on
        # average" for a portfolio; the detected-only variant below answers the
        # narrower "when we do see water, how wrong is the depth".
        'bias_ft':          round(float((detected - surveyed).mean()), 3),
        'mae_ft':           round(float(np.abs(detected - surveyed).mean()), 3),
        'rmse_ft':          round(float(np.sqrt(((detected - surveyed) ** 2).mean())), 3),
    }
    s.update({f'all_{k}': v for k, v in _corr(surveyed, detected).items()})
    lo, hi = _bootstrap_ci(surveyed, detected, sites)
    s['all_pearson_ci95'] = None if lo is None else [round(lo, 3), round(hi, 3)]

    if found.sum() >= 3:
        sub_s, sub_d = surveyed[found], detected[found]
        s.update({
            'detected_only_n':      int(found.sum()),
            'detected_only_bias_ft': round(float((sub_d - sub_s).mean()), 3),
            'detected_only_mae_ft': round(float(np.abs(sub_d - sub_s).mean()), 3),
        })
        s.update({f'detected_only_{k}': v for k, v in _corr(sub_s, sub_d).items()})
    return s


# ─── reporting ───────────────────────────────────────────────────────────────

def print_summary(s):
    if not s.get('n_marks'):
        print(f"  {s['label']}: no marks")
        return
    print(f"\n  ── {s['label']} ──")
    print(f"     marks {s['n_marks']} across {s['n_sites']} distinct survey sites "
          f"(sites are the independent unit, not marks)")
    print(f"     surveyed depth   mean {s['surveyed_mean_ft']:.2f} ft, "
          f"median {s['surveyed_median_ft']:.2f} ft")
    print(f"     detected depth   mean {s['detected_mean_ft']:.2f} ft")
    print(f"     recall @{DETECT_FLOOR_FT} ft  {s['recall_at_0.1ft'] * 100:.1f}% "
          f"({s['n_detected']}/{s['n_marks']} known-flooded points where we "
          f"found any water)")
    print(f"     bias {s['bias_ft']:+.2f} ft   MAE {s['mae_ft']:.2f} ft   "
          f"RMSE {s['rmse_ft']:.2f} ft")
    r, p = s.get('all_pearson_r'), s.get('all_pearson_p')
    if r is not None:
        ci = s.get('all_pearson_ci95')
        ci_s = f", 95% CI [{ci[0]:+.2f}, {ci[1]:+.2f}] (site-resampled)" if ci else ""
        print(f"     Pearson  r={r:+.3f} (p={p:.3g}){ci_s}")
        print(f"     Spearman r={s['all_spearman_r']:+.3f} "
              f"(p={s['all_spearman_p']:.3g})")
    if s.get('detected_only_n'):
        print(f"     where water WAS found (n={s['detected_only_n']}): "
              f"bias {s['detected_only_bias_ft']:+.2f} ft, "
              f"MAE {s['detected_only_mae_ft']:.2f} ft", end='')
        dr = s.get('detected_only_pearson_r')
        print(f", r={dr:+.3f}" if dr is not None else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('event', nargs='?', default='brazos', choices=sorted(EVENTS))
    ap.add_argument('--radius', type=float, default=None,
                    help='Sample buffer radius in metres (default: the event\'s '
                         'exposure_radius_m, else 50).')
    ap.add_argument('--sweep', action='store_true',
                    help=f'Also sample at {SWEEP_RADII_M} m and print every '
                         f'radius, not just the best one.')
    ap.add_argument('--refresh', action='store_true',
                    help='Re-download the USGS marks instead of using the cache.')
    ap.add_argument('--limit', type=int, default=None,
                    help='Cap the number of marks (smoke-testing only).')
    args = ap.parse_args()

    from backend.live_pipeline import init_ee
    init_ee()
    import config
    from event_image import build_event_image
    from flood_detect import sample_properties

    cfg = getattr(config, EVENTS[args.event])
    radius = args.radius or cfg.get('exposure_radius_m') or DEFAULT_RADIUS_M

    print(f"=== {args.event}: USGS high water mark validation ===")
    print(f"  study area: {cfg['study_name']}")
    print(f"  bbox {cfg['bbox']}  post window "
          f"{cfg['post_start']} → {cfg['post_end']}")

    hwms = fetch_hwms(refresh=args.refresh)
    df, report = hwms_in_bbox(hwms, cfg['bbox'])
    print(f"  {report['in_bbox']} marks inside the bbox; {report['usable']} carry "
          f"a surveyed height above ground")
    print(f"    dropped {report['dropped_zero_height']} recorded as exactly 0.0 ft "
          f"(unfilled optional field, see module docstring) and "
          f"{report['dropped_null_height']} with no value")
    if df.empty:
        sys.exit("  No usable marks in this bbox — nothing to validate against.")
    if args.limit:
        df = df.head(args.limit)
        print(f"  --limit {args.limit}: sampling {len(df)} marks")

    print(f"\n  Surveyed depths: min {df['surveyed_ft'].min():.2f}, "
          f"median {df['surveyed_ft'].median():.2f}, "
          f"max {df['surveyed_ft'].max():.2f} ft across "
          f"{df['site_id'].nunique()} sites")

    combined, meta = build_event_image(cfg)
    print(f"\n  Detector: orbit {meta['sar_orbit_pass']}, "
          f"{meta['post_event_scene_count']} post scenes, "
          f"baseline {meta['baseline_scene_count']} scenes "
          f"({'active' if meta['baseline_active'] else 'INACTIVE'}), "
          f"dual-pol {'active' if meta['dualpol_active'] else 'abstained'}")
    print("  NOTE: a high water mark is the PEAK stage; Sentinel-1 samples an "
          "instant. Where the pass missed the crest, underestimate is correct "
          "behaviour, not error.")

    radii = sorted(set(SWEEP_RADII_M + [radius])) if args.sweep else [radius]
    summaries, headline_df = [], None
    for r in radii:
        print(f"\n  Sampling {len(df)} marks at {r:.0f} m radius, 30 m scale ...",
              flush=True)
        sampled = sample_properties(
            combined, df, batch_size=50, scale=30, default_radius_m=r)
        merged = df.merge(
            sampled.drop(columns=['address'], errors='ignore'),
            on='property_id', how='left')
        merged['max_depth_ft'] = merged['max_depth_ft'].fillna(0.0)
        merged['pct_flooded'] = merged['pct_flooded'].fillna(0.0)
        merged['sample_radius_m'] = r
        s = summarize(merged, f"radius {r:.0f} m")
        summaries.append(s)
        print_summary(s)
        if r == radius:
            headline_df = merged

    if headline_df is None:
        headline_df = merged

    # Breakdowns on the headline radius. Reported unconditionally — a subgroup
    # that looks better is only meaningful next to the ones that do not.
    print(f"\n  ── breakdowns at the headline {radius:.0f} m radius ──")
    for col in ('hwm_environment', 'hwm_quality'):
        print(f"     by {col}:")
        for key, grp in headline_df.groupby(col, dropna=False):
            if len(grp) < 3:
                print(f"       {key}: n={len(grp)} — too few to read")
                continue
            g = summarize(grp, str(key))
            print(f"       {key}: n={g['n_marks']} ({g['n_sites']} sites), "
                  f"recall {g['recall_at_0.1ft'] * 100:.0f}%, "
                  f"bias {g['bias_ft']:+.2f} ft, MAE {g['mae_ft']:.2f} ft")

    # Does error grow with surveyed depth? Deep marks are where a claim gets
    # expensive, so a detector that only works in the shallows matters.
    print("     by surveyed depth:")
    bins = [(0, 1), (1, 2), (2, 4), (4, 100)]
    for lo, hi in bins:
        grp = headline_df[(headline_df['surveyed_ft'] >= lo) &
                          (headline_df['surveyed_ft'] < hi)]
        if len(grp) < 3:
            print(f"       {lo}-{hi} ft: n={len(grp)} — too few to read")
            continue
        g = summarize(grp, f"{lo}-{hi} ft")
        print(f"       {lo}-{hi} ft: n={g['n_marks']} ({g['n_sites']} sites), "
              f"recall {g['recall_at_0.1ft'] * 100:.0f}%, "
              f"bias {g['bias_ft']:+.2f} ft")

    csv_path = OUT / f"hwm_check_{args.event}.csv"
    json_path = OUT / f"hwm_check_{args.event}.json"
    headline_df.to_csv(csv_path, index=False)
    json_path.write_text(json.dumps({
        'event': args.event,
        'study_name': cfg['study_name'],
        'bbox': cfg['bbox'],
        'post_window': [cfg['post_start'], cfg['post_end']],
        'stn_event_id': STN_EVENT_ID,
        'ground_truth': 'USGS Short-Term Network high water marks '
                        '(height_above_gnd, surveyed feet above ground)',
        'headline_radius_m': radius,
        'mark_selection': report,
        'detector': meta,
        'summaries': summaries,
        'caveats': [
            'HWMs define no negative class — precision and false-positive rate '
            'are NOT measurable from this dataset.',
            'An HWM is the peak stage; Sentinel-1 samples an instant every '
            '6-12 days, so underestimate is expected where the pass missed '
            'the crest.',
            'Marks cluster by survey site; n_sites is the independent sample '
            'size, not n_marks.',
        ],
    }, indent=2, default=str))
    print(f"\n✓ {csv_path.relative_to(BASE)}  ({len(headline_df)} marks)")
    print(f"✓ {json_path.relative_to(BASE)}")


if __name__ == '__main__':
    main()
