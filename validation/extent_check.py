#!/usr/bin/env python3
"""
extent_check.py — Per-property flood/no-flood accuracy, with a REAL negative class.

WHAT WAS MISSING UNTIL NOW
--------------------------
Every accuracy measurement in this project has been crippled in one of two ways:

  - NFIP claims are ZIP-redacted, so 3,980 Brazos properties carry 14
    independent bits (PROJECT_STATE §2).
  - USGS high water marks are point-level and carry measured depths, but every
    mark is a place that FLOODED. No negative class, so they can measure recall
    and depth error and can never measure PRECISION (DETECTION_LIMITS §10).

Precision is the half that matters commercially. "This house definitely flooded,
skip the inspection" is a claim about false positives. A detector that fires
everywhere scores perfectly on high water marks.

THE GROUND TRUTH THIS USES
--------------------------
USGS Scientific Investigations Report 2018-5070 (doi:10.5066/F7VH5N3N)
publishes, for each mapped Harvey river reach, TWO polygons:

  1. the flood inundation extent, and
  2. the MAPPED AREA BOUNDARY — the domain within which USGS delineated it.

That second polygon is what makes a negative class possible. Inside the
boundary and outside the inundation extent is not "unlabelled"; it is ground
USGS mapped and determined did not flood. Clipped to the BRAZOS study bbox that
is 194.4 km² flooded against 194.7 km² dry — very nearly balanced, and inside
the area this repo already analyses.

Labelling REAL STRUCTURES rather than random points is what makes it a
per-property test: every structure in the USACE National Structure Inventory
that falls inside the mapped boundary gets a label from which polygon contains
it, and the detector is then scored exactly as a carrier would score it.

HONEST LIMITS — read before quoting anything from this
------------------------------------------------------
1. The inundation polygon is INTERPOLATED from water-surface elevations
   surveyed at high water marks, not directly observed. It is a modelled
   surface, and it is the one FEMA used for response operations, but it is not
   a photograph. Structures near the boundary are where that model is least
   certain, which is why `--edge-buffer` exists: it drops structures within N
   metres of the flood edge and re-reports, so the reader can see how much of
   the result depends on the least reliable band of the truth.

2. It covers the mapped riverine corridor, not the whole study area. These
   numbers describe performance on mapped floodplain, which is a harder and
   more relevant population than "everywhere", but it is not the whole book.

3. The USGS extent and our depth product both use a DEM. They are not the same
   DEM (USGS interpolated survey points; we use 3DEP with buildings masked) and
   the extent is not derived from Sentinel-1 at all, so this is not circular —
   but it is not fully independent either, and a shared DEM bias would push
   both the same way.

Usage:
    python validation/extent_check.py                    # default detector
    python validation/extent_check.py --edge-buffer 100  # drop uncertain band
    python validation/extent_check.py --max-structures 4000
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))

OUT = BASE / "outputs"
EXTENT_GEOJSON = OUT / "usgs_brazos_extent.geojson"

# Only Brazos has a USGS mapped-boundary product overlapping a study area.
# Harvey's box (Addicks/Barker) sits in the San Jacinto mapping, which is a
# separate download; see PROJECT_STATE for the reach list.
EVENT = 'brazos'
EVENT_CFG = 'BRAZOS'


def load_extent():
    """The USGS mapped boundary and inundation extent, as shapely geometry."""
    from shapely.geometry import shape
    if not EXTENT_GEOJSON.exists():
        sys.exit(f"Missing {EXTENT_GEOJSON}. It is committed to the repo; "
                 f"restore it or regenerate from doi:10.5066/F7VH5N3N.")
    gj = json.loads(EXTENT_GEOJSON.read_text())
    layers = {f['properties']['layer']: shape(f['geometry'])
              for f in gj['features']}
    return layers['mapped_boundary'].buffer(0), layers['inundation'].buffer(0)


def label_structures(df, boundary, inundation, edge_buffer_m=0.0):
    """
    Label each structure flooded/dry by which USGS polygon contains it.

    Structures outside the mapped boundary are DROPPED, not called dry — that
    is the whole point of having a boundary layer. Treating unmapped ground as
    dry would invent a negative class out of absence of evidence, which is the
    error this module exists to avoid.
    """
    from shapely.geometry import Point
    from shapely.prepared import prep
    pb, pi = prep(boundary), prep(inundation)

    # Metres -> degrees at ~29.6°N, only used for the edge-uncertainty band.
    deg = edge_buffer_m / 111_320.0 if edge_buffer_m else 0.0
    edge = inundation.boundary.buffer(deg) if deg else None
    pe = prep(edge) if edge is not None else None

    labels, keep = [], []
    for lon, lat in zip(df['longitude'], df['latitude']):
        p = Point(float(lon), float(lat))
        if not pb.contains(p):
            labels.append(None); keep.append(False); continue
        if pe is not None and pe.contains(p):
            labels.append(None); keep.append(False); continue
        labels.append(1 if pi.contains(p) else 0)
        keep.append(True)
    out = df.copy()
    out['truth_flooded'] = labels
    return out[pd.Series(keep, index=out.index)].reset_index(drop=True)


def metrics(truth, pred):
    """Confusion matrix and the rates a carrier actually asks about."""
    truth = np.asarray(truth).astype(bool)
    pred = np.asarray(pred).astype(bool)
    tp = int((truth & pred).sum()); fp = int((~truth & pred).sum())
    fn = int((truth & ~pred).sum()); tn = int((~truth & ~pred).sum())
    prec = tp / (tp + fp) if (tp + fp) else float('nan')
    rec = tp / (tp + fn) if (tp + fn) else float('nan')
    spec = tn / (tn + fp) if (tn + fp) else float('nan')
    f1 = (2 * prec * rec / (prec + rec)
          if (prec == prec and rec == rec and (prec + rec) > 0) else float('nan'))
    return {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'precision': prec, 'recall': rec, 'specificity': spec, 'f1': f1,
            'base_rate': float(truth.mean()) if len(truth) else float('nan')}


def auc(truth, score):
    """Rank AUC; None when a class is absent or scipy is unavailable."""
    truth = np.asarray(truth).astype(int)
    if truth.min() == truth.max():
        return None
    try:
        from scipy import stats
    except ImportError:  # pragma: no cover
        return None
    r = stats.rankdata(np.asarray(score, dtype=float))
    n1 = int(truth.sum()); n0 = len(truth) - n1
    return float((r[truth == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def report(name, truth, pred, score=None):
    m = metrics(truth, pred)
    a = auc(truth, score) if score is not None else None
    print(f"\n  ── {name} ──")
    print(f"     n={len(truth)}  base rate {m['base_rate'] * 100:.1f}% flooded")
    print(f"     TP {m['tp']:>5}   FP {m['fp']:>5}")
    print(f"     FN {m['fn']:>5}   TN {m['tn']:>5}")
    print(f"     precision {m['precision'] * 100:>5.1f}%   "
          f"recall {m['recall'] * 100:>5.1f}%   "
          f"specificity {m['specificity'] * 100:>5.1f}%")
    print(f"     F1 {m['f1']:.3f}" + (f"   AUC {a:.3f}" if a is not None else ""))
    lift = (m['precision'] / m['base_rate']
            if m['base_rate'] and m['precision'] == m['precision'] else float('nan'))
    print(f"     precision lift over base rate: {lift:.2f}x "
          f"(1.0 = no better than guessing 'flooded' for everyone)")
    m['auc'] = a
    m['precision_lift'] = lift
    m['name'] = name
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--edge-buffer', type=float, default=0.0,
                    help='Drop structures within N metres of the flood edge, '
                         'where the interpolated extent is least certain.')
    ap.add_argument('--max-structures', type=int, default=6000)
    ap.add_argument('--radius', type=float, default=None)
    args = ap.parse_args()

    from backend.live_pipeline import init_ee
    init_ee()
    import config
    from event_image import build_event_image
    from flood_detect import sample_properties
    import structures as struct

    cfg = getattr(config, EVENT_CFG)
    radius = args.radius or cfg.get('exposure_radius_m') or 50

    print("=== Brazos: per-property accuracy vs USGS mapped flood extent ===")
    boundary, inundation = load_extent()
    print("  ground truth: USGS SIR 2018-5070 mapped boundary + inundation")

    print("\n  Fetching National Structure Inventory...", flush=True)
    nsi = struct.fetch_nsi_structures(cfg['bbox'])
    print(f"  {len(nsi):,} structures in bbox", flush=True)

    lat_col = 'nsi_lat' if 'nsi_lat' in nsi else 'latitude'
    lon_col = 'nsi_lon' if 'nsi_lon' in nsi else 'longitude'
    pts = nsi.rename(columns={lat_col: 'latitude', lon_col: 'longitude'})
    pts = pts.dropna(subset=['latitude', 'longitude'])

    print(f"  Labelling by USGS polygons"
          f"{f' (dropping a {args.edge_buffer:.0f} m band at the flood edge)' if args.edge_buffer else ''}...",
          flush=True)
    lab = label_structures(pts, boundary, inundation, args.edge_buffer)
    print(f"  {len(lab):,} structures inside the mapped boundary "
          f"({int(lab.truth_flooded.sum()):,} flooded, "
          f"{int((lab.truth_flooded == 0).sum()):,} dry)")
    if lab.empty:
        sys.exit("  No structures inside the mapped boundary.")

    # Stratified subsample: keep the class balance the truth actually has,
    # rather than letting whichever class is denser dominate the run cost.
    if len(lab) > args.max_structures:
        frac = args.max_structures / len(lab)
        lab = (lab.groupby('truth_flooded', group_keys=False)
               .apply(lambda g: g.sample(max(1, int(len(g) * frac)),
                                         random_state=0))
               .reset_index(drop=True))
        print(f"  subsampled to {len(lab):,} "
              f"({int(lab.truth_flooded.sum()):,} flooded, "
              f"{int((lab.truth_flooded == 0).sum()):,} dry)")

    lab['property_id'] = [f"s{i}" for i in range(len(lab))]
    lab['address'] = lab['property_id']

    combined, meta = build_event_image(cfg)
    print(f"\n  Detector: orbit {meta['sar_orbit_pass']}, "
          f"{meta['post_event_scene_count']} post scenes, "
          f"dual-pol {'active' if meta['dualpol_active'] else 'abstained'}",
          flush=True)

    print(f"\n  Sampling {len(lab):,} structures at {radius:.0f} m ...",
          flush=True)
    sampled = sample_properties(combined, lab, batch_size=50, scale=30,
                                default_radius_m=radius)
    df = lab.merge(sampled.drop(columns=['address'], errors='ignore'),
                   on='property_id', how='left')
    for c in ('pct_flooded', 'max_depth_ft', 'water_fraction', 'dpol_water',
              'double_bounce', 'optical_water_pct'):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)

    truth = df['truth_flooded'].astype(int).to_numpy()

    print("\n" + "=" * 66)
    print("  PER-PROPERTY ACCURACY — real positives AND real negatives")
    print("=" * 66)
    results = []
    results.append(report("shipped detector (max_depth_ft > 0.1)",
                          truth, df['max_depth_ft'] > 0.1, df['max_depth_ft']))
    if 'water_fraction' in df:
        results.append(report("sub-pixel water fraction > 0 (Phase 4a)",
                              truth, df['water_fraction'] > 0,
                              df['water_fraction']))
    if 'dpol_water' in df:
        results.append(report("dual-pol score > 0 (Phase 4b)",
                              truth, df['dpol_water'] > 0, df['dpol_water']))
    if 'double_bounce' in df:
        results.append(report("double-bounce > 0 (Phase 4e)",
                              truth, df['double_bounce'] > 0,
                              df['double_bounce']))
    if 'optical_water_pct' in df:
        results.append(report("Sentinel-2 optical water > 0",
                              truth, df['optical_water_pct'] > 0,
                              df['optical_water_pct']))
    # HAND is terrain alone — no satellite observation of THIS event at all.
    # It is the benchmark every SAR signal has to beat to be worth its cost.
    if 'hand_ft' in df:
        h = pd.to_numeric(df['hand_ft'], errors='coerce')
        ok = h.notna() & (h >= 0)
        if ok.sum() > 10:
            results.append(report(
                "HAND terrain only, <= 10 ft (NO event observation)",
                truth[ok.to_numpy()], (h[ok] <= 10).to_numpy(),
                (-h[ok]).to_numpy()))

    print("\n  The HAND row is the bar to beat: it uses terrain alone and never")
    print("  looks at this storm. Any SAR signal that cannot beat it is not")
    print("  earning its place in the product.")

    csv = OUT / "extent_check_brazos.csv"
    df.to_csv(csv, index=False)
    (OUT / "extent_check_brazos.json").write_text(json.dumps({
        'event': EVENT,
        'ground_truth': 'USGS SIR 2018-5070 (doi:10.5066/F7VH5N3N) mapped '
                        'boundary + flood inundation extent, upper Brazos',
        'edge_buffer_m': args.edge_buffer,
        'radius_m': radius,
        'n_structures': int(len(df)),
        'n_flooded': int(truth.sum()), 'n_dry': int((truth == 0).sum()),
        'detector': meta,
        'results': results,
        'caveats': [
            'The inundation polygon is interpolated from water-surface '
            'elevations surveyed at high water marks, not directly observed.',
            'Covers the mapped riverine corridor only, not the whole bbox.',
            'Structures outside the mapped boundary are dropped, never '
            'labelled dry.',
        ],
    }, indent=2, default=str))
    print(f"\n✓ {csv.relative_to(BASE)}")


if __name__ == '__main__':
    main()
