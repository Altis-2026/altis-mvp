#!/usr/bin/env python3
"""
fit_ensemble.py — Phase 4d. Fit the learned ensemble and prove (or disprove)
that it beats the hand-tuned score on identical zip-grouped splits.

THE BAR THIS HAS TO CLEAR
-------------------------
A learned model that scores well in-sample is worthless here: labels are
zip-resolution, and 14 zips is few enough that a model can memorise them. So
every number below comes from zip-GROUPED cross-validation — the model never
sees a test zip during fitting — and is compared against the existing
hand-tuned score on the SAME splits, the paired design Phase 4c established.

If the learned model does not beat the hand-tuned baseline on held-out zips,
that is the finding and it gets recorded, exactly as Phase 4a's sub-pixel
fraction was. Nothing here ships on the strength of an in-sample number.

No Earth Engine calls.

Usage: python validation/fit_ensemble.py [event] [--repeats N] [--l2 X]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "pipeline"))

import calibration as calib          # noqa: E402
from ensemble_model import (         # noqa: E402
    LogisticEnsemble, build_feature_matrix, FEATURE_NAMES)

OUT = BASE / "outputs"


def load_event(event: str):
    raw_p, lab_p = OUT / f'{event}_raw.csv', OUT / f'{event}_labels.csv'
    for p in (raw_p, lab_p):
        if not p.exists():
            sys.exit(f"Missing {p} — run the pipeline and validation first.")
    raw, lab = pd.read_csv(raw_p), pd.read_csv(lab_p)
    raw['property_id'] = raw['property_id'].astype(str)
    lab['property_id'] = lab['property_id'].astype(str)
    m = raw.merge(lab[['property_id', 'zip', 'flooded_truth']],
                  on='property_id', how='inner').dropna(subset=['flooded_truth'])
    return m


def hand_tuned_score(df: pd.DataFrame) -> np.ndarray:
    """The production coverage term, reproduced exactly (accuracy_check.py)."""
    pct = pd.to_numeric(df['pct_flooded'], errors='coerce').fillna(0.0) / 100.0
    depth = pd.to_numeric(df['max_depth_ft'], errors='coerce').fillna(0.0)
    cov = pct
    if 'water_fraction' in df.columns:
        wf = pd.to_numeric(df['water_fraction'], errors='coerce').fillna(0.0)
        cov = pd.concat([cov, wf], axis=1).max(axis=1)
    if 'dpol_water' in df.columns:
        dp = pd.to_numeric(df['dpol_water'], errors='coerce').fillna(0.0)
        if 'dpol_available' in df.columns:
            av = pd.to_numeric(df['dpol_available'], errors='coerce').fillna(0)
            dp = dp.where(av > 0, 0.0)
        cov = pd.concat([cov, dp], axis=1).max(axis=1)
    return np.array([calib.raw_flood_score(c, d) for c, d in zip(cov, depth)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('event', nargs='?', default='brazos')
    ap.add_argument('--repeats', type=int, default=300)
    ap.add_argument('--l2', type=float, default=1.0)
    ap.add_argument('--test-fraction', type=float, default=0.3)
    args = ap.parse_args()

    m = load_event(args.event)
    X = build_feature_matrix(m)
    y = m['flooded_truth'].astype(float).values
    groups = m['zip'].astype(str).values
    hand = hand_tuned_score(m)

    print(f"=== {args.event}: Phase 4d learned ensemble ===")
    print(f"  {len(m):,} labelled properties, {int(y.sum()):,} flooded-truth "
          f"({y.mean()*100:.1f}%), {len(set(groups))} zips")
    print(f"  {X.shape[1]} features, L2={args.l2}, {args.repeats} grouped splits\n")

    # ── Out-of-fold predictions: every property scored by a model that never
    #    saw its zip. This is the only honest way to compare a fitted model
    #    against a fixed formula on the same footing.
    briers_l, briers_h, aucs_l, aucs_h, skills_l, skills_h = [], [], [], [], [], []
    eces_l, eces_h = [], []
    used = 0
    for i in range(args.repeats):
        tr, te = calib.group_train_test_split(groups, args.test_fraction, i)
        if len(tr) == 0 or len(te) == 0:
            continue
        if not (0 < y[tr].sum() < len(tr)) or not (0 < y[te].sum() < len(te)):
            continue
        used += 1

        model = LogisticEnsemble.fit(X[tr], y[tr], l2=args.l2)
        p_l = model.predict(X[te])

        cal = calib.fit_calibrator(hand[tr], y[tr], "auto")
        p_h = cal.predict(hand[te])

        base = float(y[tr].mean())
        b_const = calib.brier_score(np.full(len(te), base), y[te])

        for arr, p in ((briers_l, p_l), (briers_h, p_h)):
            arr.append(calib.brier_score(p, y[te]))
        for arr, p in ((eces_l, p_l), (eces_h, p_h)):
            arr.append(calib.expected_calibration_error(p, y[te]))
        skills_l.append(1 - briers_l[-1] / b_const if b_const > 0 else np.nan)
        skills_h.append(1 - briers_h[-1] / b_const if b_const > 0 else np.nan)
        aucs_l.append(calib._auc(p_l, y[te]))
        aucs_h.append(calib._auc(hand[te], y[te]))

    def rep(name, a):
        a = np.asarray([v for v in a if np.isfinite(v)], float)
        return f"{a.mean():.4f}±{a.std(ddof=1):.4f}"

    print(f"  Paired over {used} grouped splits\n")
    print(f"{'metric':>20} {'hand-tuned':>18} {'learned':>18} {'paired diff':>22}")
    verdicts = {}
    for metric, h, l, better in (('brier', briers_h, briers_l, 'lower'),
                                 ('ece', eces_h, eces_l, 'lower'),
                                 ('brier_skill_score', skills_h, skills_l, 'higher'),
                                 ('auc', aucs_h, aucs_l, 'higher')):
        ha, la = np.asarray(h, float), np.asarray(l, float)
        ok = np.isfinite(ha) & np.isfinite(la)
        d = la[ok] - ha[ok]
        se = d.std(ddof=1) / np.sqrt(len(d)) if len(d) > 1 else 0.0
        win = (d < 0).mean() if better == 'lower' else (d > 0).mean()
        verdict = ("noise" if se == 0 or abs(d.mean()) < se
                   else ("BETTER" if (d.mean() < 0) == (better == 'lower') else "WORSE"))
        verdicts[metric] = {'mean_difference': round(float(d.mean()), 6),
                            'standard_error': round(float(se), 6),
                            'win_rate': round(float(win), 4),
                            'verdict': verdict}
        print(f"{metric:>20} {rep(metric, h):>18} {rep(metric, l):>18} "
              f"{d.mean():+.5f}±{se:.5f} {verdict:>8}")

    # ── Production model: refit on everything, after the honest number above.
    final = LogisticEnsemble.fit(X, y, l2=args.l2)
    print("\nLEARNED WEIGHTS (standardised; sign is the physical claim)")
    for k, v in final.weights().items():
        print(f"    {k:>20}: {v:+.4f}")

    dest = OUT / f'ensemble_{args.event}.json'
    dest.write_text(json.dumps({
        'event': args.event,
        'model': final.to_dict(),
        'feature_names': FEATURE_NAMES,
        'evaluation': {
            'n_paired_splits': used,
            'test_fraction': args.test_fraction,
            'split_kind': 'grouped_by_zip',
            'comparisons_vs_hand_tuned': verdicts,
        },
    }, indent=2))
    print(f"\n  Written -> {dest}")


if __name__ == '__main__':
    main()
