#!/usr/bin/env python3
"""
dualpol_ablation.py — Phase 4c. Does the dual-pol score actually help, or was
the 0.0005 Brier move noise from one lucky/unlucky split?

Phase 4b left this genuinely unresolved. Standalone, the dual-pol score cleared
chance (AUC 0.5078, p=0.017) and quadrupled recall over the binary mask. Folded
into the calibrated pipeline it made the held-out Brier marginally WORSE
(0.1714 -> 0.1719). Those two facts are not contradictory, but with a single
grouped split — 4-5 of 15 zips deciding the test fold — there was no way to
tell a real regression from split noise.

This runs the comparison the way it should have been run: many grouped splits,
with every candidate scored on the SAME splits, so per-split noise cancels in
the paired difference.

No Earth Engine calls. Reads the CSVs already on disk.

Usage:  python validation/dualpol_ablation.py [event] [--repeats N]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "pipeline"))

import calibration as calib  # noqa: E402

OUT = BASE / "outputs"


def build_candidates(raw: pd.DataFrame) -> dict:
    """
    The score variants under test, built exactly as the production coverage
    term builds them (validation/accuracy_check.py::derive_property_labels).

    Keeping this in lockstep matters: an ablation that scores a differently
    constructed signal answers a question nobody asked.
    """
    pct = pd.to_numeric(raw['pct_flooded'], errors='coerce').fillna(0.0) / 100.0
    depth = pd.to_numeric(raw['max_depth_ft'], errors='coerce').fillna(0.0)

    dpol = pd.to_numeric(raw.get('dpol_water', 0.0), errors='coerce').fillna(0.0)
    if 'dpol_available' in raw.columns:
        avail = pd.to_numeric(raw['dpol_available'], errors='coerce').fillna(0)
        dpol = dpol.where(avail > 0, 0.0)

    binary_cov = pct
    union_cov = pd.concat([pct, dpol], axis=1).max(axis=1)

    return {
        'binary_only': np.array([calib.raw_flood_score(c, d)
                                 for c, d in zip(binary_cov, depth)]),
        'binary_plus_dualpol': np.array([calib.raw_flood_score(c, d)
                                         for c, d in zip(union_cov, depth)]),
        # Dual-pol on its own, to separate "adds information" from "adds
        # information the union actually exposes". A max() can hide a good
        # signal behind a louder one.
        'dualpol_only': np.array([calib.raw_flood_score(c, d)
                                  for c, d in zip(dpol, depth)]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('event', nargs='?', default='brazos')
    ap.add_argument('--repeats', type=int, default=300)
    ap.add_argument('--test-fraction', type=float, default=0.3)
    args = ap.parse_args()

    raw_path = OUT / f'{args.event}_raw.csv'
    lab_path = OUT / f'{args.event}_labels.csv'
    for p in (raw_path, lab_path):
        if not p.exists():
            sys.exit(f"Missing {p} — run the pipeline and validation first.")

    raw = pd.read_csv(raw_path)
    lab = pd.read_csv(lab_path)
    if 'dpol_water' not in raw.columns:
        sys.exit(f"{raw_path.name} has no dpol_water column — that run predates "
                 f"Phase 4b. Re-run 03_flood_pipeline.py.")

    raw['property_id'] = raw['property_id'].astype(str)
    lab['property_id'] = lab['property_id'].astype(str)

    cands = build_candidates(raw)
    merged = raw[['property_id']].assign(**cands).merge(
        lab[['property_id', 'zip', 'flooded_truth']], on='property_id', how='inner')
    merged = merged.dropna(subset=['flooded_truth'])

    labels = merged['flooded_truth'].astype(float).values
    groups = merged['zip'].astype(str).values
    candidates = {k: merged[k].values for k in cands}

    print(f"=== {args.event}: dual-pol ablation ===")
    print(f"  {len(merged):,} labelled properties, {int(labels.sum()):,} flooded-truth "
          f"({labels.mean() * 100:.1f}% base rate), {len(set(groups))} zips")
    print(f"  {args.repeats} grouped splits, test fraction {args.test_fraction}\n")

    res = calib.paired_candidate_comparison(
        candidates, labels, groups,
        n_repeats=args.repeats, test_fraction=args.test_fraction)

    print(f"  Paired over {res['n_paired_splits']} splits "
          f"(splits where any candidate had a single-class fold are dropped)\n")

    print(f"{'candidate':>22} {'Brier':>17} {'skill':>17} {'AUC':>17}")
    for name, r in res['per_candidate'].items():
        def f(m):
            v = r[m]
            return f"{v['mean']:.4f}±{v['std']:.4f}" if v else "n/a"
        print(f"{name:>22} {f('brier'):>17} {f('brier_skill_score'):>17} {f('auc'):>17}")

    print(f"\nPAIRED DIFFERENCES vs {res['baseline']} "
          f"(same splits; effect smaller than its own SE = noise)")
    for name, cmps in res['comparisons'].items():
        print(f"\n  {name}:")
        for metric, c in cmps.items():
            if not c:
                print(f"    {metric:>18}: n/a")
                continue
            print(f"    {metric:>18}: {c['mean_difference']:+.5f} "
                  f"(SE {c['standard_error']:.5f}, wins {c['win_rate'] * 100:.0f}% "
                  f"of {c['n_paired']}) -> {c['verdict'].upper()}")

    dest = OUT / f'ablation_dualpol_{args.event}.json'
    slim = {'baseline': res['baseline'],
            'n_paired_splits': res['n_paired_splits'],
            'comparisons': res['comparisons'],
            'per_candidate': {k: {m: v[m] for m in
                                  ('brier', 'ece', 'brier_skill_score', 'auc',
                                   'n_splits_used')}
                              for k, v in res['per_candidate'].items()}}
    dest.write_text(json.dumps(slim, indent=2))
    print(f"\n  Written -> {dest}")


if __name__ == '__main__':
    main()
