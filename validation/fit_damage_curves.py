#!/usr/bin/env python3
"""
fit_damage_curves.py — Fit depth-damage curves to REAL paid claims.

WHY
---
`pipeline/severity.py` turns a detected depth into a dollar range using
HAZUS-style depth-damage curves in `config.SEVERITY_CURVES`. Those curves are
**published national shapes**. They have never been checked against a single
claim this project holds, and the whole severity product rests on them.

We hold 25,011 real Hurricane Harvey NFIP claims across the 59 study-area zip
codes, with reported water depth, amount paid on the building claim, and
building property value. That is enough to ask the only question that matters:
**do the shipped curves predict what was actually paid?**

WHAT IS BEING FITTED, PRECISELY
-------------------------------
    damage_ratio = amountPaidOnBuildingClaim / buildingPropertyValue

against `waterDepth`, per structure segment. This is the same quantity
SEVERITY_CURVES encodes (percent of structure value lost at a given depth), so
a fitted curve is a drop-in replacement for a shipped one.

FIVE THINGS THAT WOULD MAKE THIS DISHONEST IF NOT HANDLED
----------------------------------------------------------
1. **RIGHT-CENSORING AT THE NFIP CAP.** Building coverage caps at $250,000.
   13.9% of usable claims sit at that cap, and for those the true loss is
   *at least* the paid amount, not equal to it. Including them unweighted
   drags every deep-water bin downward — which is visible in the raw data as a
   damage ratio that RISES to ~50% at 4 ft and then FALLS at 6 ft+, which is
   not physical. Every fit is therefore reported twice: with capped claims and
   with them excluded.

2. **`waterDepth` UNITS ARE AMBIGUOUS.** FEMA documents the field as "Depth of
   flood water in inches. Note: there are instances where measurements were
   provided in feet." `nfip_claims.normalize_water_depth` resolves this by
   reading values <= 15 as feet and larger values as inches, and reports which
   assumption it made per claim. 90.6% of claims are read as feet. This is an
   assumption, not a fact, and it scales the depth axis.

3. **`waterDepth` IS NOT DOCUMENTED AS FLOOR-RELATIVE.** SEVERITY_CURVES is
   indexed on depth above the FIRST FLOOR. FEMA's description says only "depth
   of flood water". Negative values exist (34 claims), which implies some
   floor-relative recording, but this is not stated. The fitted curve is
   therefore indexed on REPORTED DEPTH, and swapping it in assumes the two
   agree. That assumption is flagged in the output rather than buried.

4. **THE PREDICTOR IS COARSE.** 47% of claims report exactly 1 ft and 26%
   report exactly 0 ft. The depth axis is heavily rounded, so bins below ~1 ft
   cannot be resolved and the curve should not be read as smooth.

5. **SURVIVORSHIP — and this one changes how the curve may be USED.** These
   are NFIP-insured properties that FILED and were PAID. A property that took
   two inches of water and never filed is absent. So the fitted curve is
   `P(damage | a claim was filed)`, NOT `P(damage | the property flooded)`.
   That is why it does not start near zero: the shallowest filed claims still
   show ~38% of value paid, because trivial damage does not generate a claim.
   Applying it to every detected property — including ones that would never
   file — will OVERSTATE portfolio loss. It is a reserving curve for known
   claims, not an exposure curve for a whole book.

6. **`waterDepth == 0` MEANS "NOT RECORDED", NOT "NO WATER".** Measured
   directly: 1,001 single-family claims report depth 0, and their median
   payout is 33.5% of building value — 725 of them paid more than 10% of
   value, and exactly 6 paid nothing. A genuine zero-depth flood does not pay
   a third of a house. Those rows are therefore DROPPED rather than pinned to
   the curve's origin. Leaving them in anchors the left end at ~33%, which
   would make the pipeline predict a third of a home's value destroyed at zero
   detected depth. `--include-zero-depth` reproduces that behaviour for
   comparison.

VALIDATION
----------
Zip-grouped holdout: train zips and test zips are disjoint, so a curve cannot
score well by memorising a zip's average payout. Metric is mean absolute error
in damage ratio on held-out claims. The rule, set before the numbers:
**a fitted curve replaces the shipped one only if it beats it on held-out MAE.**

Usage: python validation/fit_damage_curves.py
Writes outputs/fitted_damage_curves.json and _report.md
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))
OUT = BASE / "outputs"

CLAIMS_CSV = OUT / "nfip_claims_harvey_raw.csv"

# NFIP building coverage limit for 1-4 family residential. Claims at or just
# below it are right-censored: the true loss is >= the amount paid.
NFIP_BUILDING_CAP = 250_000
CAP_TOLERANCE = 1_000

# Depth knots the fitted curve is reported on. Chosen to match the shipped
# curves' knots where possible so the two are directly comparable, and to keep
# every bin populated given how coarse the depth field is.
DEPTH_KNOTS = [0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0]

# A bin with fewer than this many claims is not reported as a fitted value —
# it is interpolated from its neighbours and flagged. Prevents a curve knot
# that rests on nine claims from being read as a measurement.
MIN_BIN_N = 30

# occupancyType, from FEMA's field dictionary (verified, not assumed):
#   1, 11 = single-family residence (11 is the Risk Rating 2.0 recoding)
#   2, 12 = 2-4 unit residential      3, 13 = 5+ unit residential
#   14    = residential mobile home   4, 6, 17, 18 = non-residential
SINGLE_FAMILY = {1, 11}
MOBILE_HOME = {14}
MULTI_FAMILY = {2, 3, 12, 13, 15, 16}


def load_claims():
    if not CLAIMS_CSV.exists():
        sys.exit(f"Missing {CLAIMS_CSV}. Fetch it with "
                 f"validation/nfip_claims.fetch_event_claims(extended_fields=True).")
    d = pd.read_csv(CLAIMS_CSV)
    d['ratio'] = d['paid_building'] / d['property_value']
    d['censored'] = d['paid_building'] >= (NFIP_BUILDING_CAP - CAP_TOLERANCE)
    return d


def clean(d, include_zero_depth=False):
    """
    Rows usable for a curve fit, with every exclusion counted.

    A shrinking sample that passes silently is how a fit on an unrepresentative
    subset gets published as if it were the book.
    """
    n0 = len(d)
    steps = []

    def drop(mask, why):
        nonlocal d
        before = len(d)
        d = d[mask]
        steps.append({'reason': why, 'dropped': int(before - len(d)),
                      'remaining': int(len(d))})

    drop(d['depth_ft'].notna(), 'no usable waterDepth')
    drop(d['property_value'] > 10_000, 'building property value missing or <= $10k')
    drop(d['paid_building'].notna() & (d['paid_building'] >= 0), 'no building payment')
    # A paid amount above the insured value is a data error for curve-fitting
    # purposes — the ratio is meant to be a share of value lost.
    drop(d['ratio'] <= 1.5, 'paid exceeds 150% of stated value (data error)')
    drop(d['depth_ft'].between(-10, 30), 'depth outside a physically plausible range')
    if not include_zero_depth:
        drop(d['depth_ft'] != 0, 'waterDepth == 0, which means unrecorded '
                                 '(median payout there is 33.5% of value)')
    return d.reset_index(drop=True), {'start': int(n0), 'steps': steps,
                                      'end': int(len(d))}


def segment(d):
    """Split into the structure classes the shipped curves distinguish."""
    occ = pd.to_numeric(d['occupancy_type'], errors='coerce')
    floors = pd.to_numeric(d['n_floors'], errors='coerce')
    segs = {
        'RES1-1S': d[occ.isin(SINGLE_FAMILY) & (floors == 1)],
        'RES1-2S': d[occ.isin(SINGLE_FAMILY) & (floors >= 2)],
        'RES2-mobile': d[occ.isin(MOBILE_HOME)],
        'RES3-multi': d[occ.isin(MULTI_FAMILY)],
        'NONRES': d[occ.isin({4, 6, 17, 18})],
        'ALL': d,
    }
    return {k: v for k, v in segs.items() if len(v) >= MIN_BIN_N}


def empirical_curve(d, knots=DEPTH_KNOTS, exclude_censored=False):
    """
    Median damage ratio at each depth knot, as [(depth_ft, pct)].

    Median rather than mean: payouts are right-skewed and a handful of large
    settlements would drag a mean well above what a typical claim looks like.

    Returns (curve, per_knot_detail).
    """
    src = d[~d['censored']] if exclude_censored else d
    detail, pts = [], []
    edges = list(zip([-np.inf] + knots[:-1], knots))
    for lo, hi in edges:
        # Assign each knot the claims closest to it: (previous knot, this knot]
        sel = src[(src['depth_ft'] > lo) & (src['depth_ft'] <= hi)]
        n = len(sel)
        if n >= MIN_BIN_N:
            pct = float(sel['ratio'].median() * 100)
            pts.append((float(hi), round(pct, 1)))
            detail.append({'depth_ft': hi, 'n': n, 'median_pct': round(pct, 1),
                           'mean_pct': round(float(sel['ratio'].mean() * 100), 1),
                           'censored_share': round(float(sel['censored'].mean()), 3),
                           'interpolated': False})
        else:
            detail.append({'depth_ft': hi, 'n': n, 'median_pct': None,
                           'interpolated': True})
    return pts, detail


def predict(curve, depth):
    """Piecewise-linear lookup, clamped — same semantics as severity._interpolate."""
    if not curve:
        return np.nan
    xs = np.array([c[0] for c in curve], dtype=float)
    ys = np.array([c[1] for c in curve], dtype=float)
    return np.interp(np.asarray(depth, dtype=float), xs, ys)


def shipped_curve(key):
    """The curve severity.py would use today, for the same segment."""
    from config import SEVERITY, SEVERITY_CURVES
    mapping = {
        'RES1-1S': 'RES1-1S-NB', 'RES1-2S': 'RES1-2S-NB',
        'RES2-mobile': 'RES2',
    }
    k = mapping.get(key)
    if k and k in SEVERITY_CURVES:
        return SEVERITY_CURVES[k], k
    return SEVERITY['depth_damage_curve'], 'generic'


def zip_grouped_eval(d, key, folds=5, seed=0, exclude_censored=False):
    """
    Held-out MAE for the fitted curve vs the shipped curve.

    Zips are the grouping unit so a curve cannot score by memorising a zip's
    average payout — the same discipline the detection work uses.
    """
    rng = np.random.default_rng(seed)
    zips = np.array(sorted(d['zip'].astype(str).unique()), dtype=object)
    rng.shuffle(zips)
    chunks = np.array_split(zips, min(folds, len(zips)))
    fit_err, ship_err, n_test = [], [], 0
    ship, _ = shipped_curve(key)
    for held in chunks:
        te = d['zip'].astype(str).isin(held)
        train, test = d[~te], d[te]
        if len(test) < 20 or len(train) < 100:
            continue
        curve, _ = empirical_curve(train, exclude_censored=exclude_censored)
        if not curve:
            continue
        truth = test['ratio'].to_numpy() * 100
        fit_err.append(np.abs(predict(curve, test['depth_ft']) - truth))
        ship_err.append(np.abs(predict(ship, test['depth_ft']) - truth))
        n_test += len(test)
    if not fit_err:
        return None
    fe, se = np.concatenate(fit_err), np.concatenate(ship_err)
    return {'n_test': int(n_test),
            'fitted_mae_pct': round(float(fe.mean()), 2),
            'shipped_mae_pct': round(float(se.mean()), 2),
            'improvement_pct_points': round(float(se.mean() - fe.mean()), 2),
            'fitted_bias_pct': round(float(
                (predict(curve, test['depth_ft']) - truth).mean()), 2)}


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--include-zero-depth', action='store_true',
                    help='Keep waterDepth == 0 rows. They mean "unrecorded", '
                         'not "no water" — see the docstring. For comparison '
                         'only.')
    args = ap.parse_args()

    raw = load_claims()
    d, drops = clean(raw, include_zero_depth=args.include_zero_depth)

    print("=== Depth-damage curves fitted to real NFIP paid claims ===")
    print(f"  {drops['start']:,} claims fetched → {drops['end']:,} usable")
    for s in drops['steps']:
        if s['dropped']:
            print(f"    −{s['dropped']:,} {s['reason']}")
    cap_share = float(d['censored'].mean())
    print(f"  right-censored at the ${NFIP_BUILDING_CAP:,} NFIP cap: "
          f"{int(d['censored'].sum()):,} ({cap_share * 100:.1f}%)")
    units = raw['depth_unit_assumed'].value_counts().to_dict()
    print(f"  waterDepth unit assumption: {units}")

    results = {}
    for key, seg in segment(d).items():
        ship, ship_key = shipped_curve(key)
        curve_all, detail_all = empirical_curve(seg)
        curve_unc, _ = empirical_curve(seg, exclude_censored=True)
        ev_all = zip_grouped_eval(seg, key)
        ev_unc = zip_grouped_eval(seg, key, exclude_censored=True)

        print(f"\n── {key}  (n={len(seg):,}, "
              f"censored {seg['censored'].mean() * 100:.1f}%, "
              f"shipped curve '{ship_key}')")
        print(f"   {'depth':>7}{'n':>7}{'FITTED %':>10}{'SHIPPED %':>11}"
              f"{'diff':>8}")
        for row in detail_all:
            if row['median_pct'] is None:
                print(f"   {row['depth_ft']:>7.1f}{row['n']:>7}"
                      f"{'  too few':>10}")
                continue
            sp = float(predict(ship, row['depth_ft']))
            print(f"   {row['depth_ft']:>7.1f}{row['n']:>7}"
                  f"{row['median_pct']:>10.1f}{sp:>11.1f}"
                  f"{row['median_pct'] - sp:>+8.1f}")
        if ev_all:
            print(f"   held-out MAE (zip-grouped, n={ev_all['n_test']:,}): "
                  f"fitted {ev_all['fitted_mae_pct']}pp vs "
                  f"shipped {ev_all['shipped_mae_pct']}pp "
                  f"→ {ev_all['improvement_pct_points']:+.2f}pp")
        if ev_unc:
            print(f"   same, excluding capped claims: "
                  f"fitted {ev_unc['fitted_mae_pct']}pp vs "
                  f"shipped {ev_unc['shipped_mae_pct']}pp "
                  f"→ {ev_unc['improvement_pct_points']:+.2f}pp")
        results[key] = {
            'n': int(len(seg)),
            'censored_share': round(float(seg['censored'].mean()), 3),
            'shipped_curve_key': ship_key,
            'fitted_curve': curve_all,
            'fitted_curve_excluding_capped': curve_unc,
            'knot_detail': detail_all,
            'holdout': ev_all,
            'holdout_excluding_capped': ev_unc,
        }

    print("\n" + "=" * 70)
    print("  VERDICT (rule set before the numbers: a fitted curve replaces the")
    print("  shipped one only if it beats it on held-out MAE)")
    print("=" * 70)
    for key, r in results.items():
        ev = r['holdout']
        if not ev:
            print(f"  {key}: no held-out evaluation possible")
            continue
        better = ev['improvement_pct_points'] > 0
        print(f"  {key:<14} {'REPLACE' if better else 'KEEP SHIPPED':<14}"
              f" fitted {ev['fitted_mae_pct']}pp vs shipped "
              f"{ev['shipped_mae_pct']}pp ({ev['improvement_pct_points']:+.2f}pp)")

    (OUT / "fitted_damage_curves.json").write_text(json.dumps({
        'source': 'FEMA OpenFEMA NfipClaims v3, 59 study-area zips, '
                  'dateOfLoss 2017-08-25 to 2017-10-15',
        'fitted_quantity': 'median(amountPaidOnBuildingClaim / '
                           'buildingPropertyValue) x 100, by depth bin',
        'n_claims_fetched': int(drops['start']),
        'n_usable': int(drops['end']),
        'exclusions': drops['steps'],
        'censored_share_at_nfip_cap': round(cap_share, 4),
        'depth_unit_assumption': units,
        'include_zero_depth': bool(args.include_zero_depth),
        'usage_warning': 'This is P(damage | a claim was filed), not '
                         'P(damage | flooded). It does not start near zero '
                         'because trivial damage does not generate a claim. '
                         'Applying it to every detected property will '
                         'OVERSTATE portfolio loss.',
        'caveats': [
            'waterDepth == 0 means unrecorded, not no water: median payout at '
            'depth 0 is 33.5% of building value. Those rows are dropped.',
            'waterDepth is documented by FEMA as inches with some instances in '
            'feet; values <= 15 are read as feet. This scales the depth axis.',
            'waterDepth is NOT documented as first-floor-relative, but '
            'SEVERITY_CURVES is indexed on depth above first floor. Swapping a '
            'fitted curve in assumes the two agree.',
            'The depth field is heavily rounded: 47% report exactly 1 ft and '
            '26% exactly 0 ft. Sub-foot structure is not resolvable.',
            'Right-censoring at the $250,000 NFIP building cap affects '
            f'{cap_share * 100:.1f}% of usable claims; curves are reported '
            'both with and without them.',
            'NFIP-insured properties that filed and were paid only. Not a '
            'sample of physical damage.',
        ],
        'segments': results,
    }, indent=2, default=str))
    print("\n✓ outputs/fitted_damage_curves.json")


if __name__ == '__main__':
    main()
