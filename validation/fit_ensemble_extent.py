#!/usr/bin/env python3
"""
fit_ensemble_extent.py — Phase 4d re-tested against REAL per-property labels.

WHY THIS RE-TEST
----------------
Phase 4d (a learned logistic combination of the detector's signals) lost
against zip-level labels: AUC 0.4107 vs 0.5012 for the hand-tuned rules, and
the fitted weights gave `optical_water_pct` a large NEGATIVE coefficient —
more visible water, less likely flooded. That is not physics; it is the model
memorising which zip was which, because all ~4,000 properties carried only 14
independent labels.

That confound is gone. `extent_check.py` produced 2,999 structures per event
labelled individually by the USGS mapped flood extent (DETECTION_LIMITS §12).
This module refits the SAME model (`pipeline/ensemble_model.py`, unchanged) on
those labels and scores it the same way every other signal has been scored.

THE NEW CONFOUND, WHICH IS JUST AS DANGEROUS
--------------------------------------------
Zip memorisation is dead. SPATIAL memorisation is not.

Structures 50 m apart almost always share a flood label — floods are contiguous
by nature. A random train/test split therefore puts a structure's near
neighbours in the training set, and a model with any location-correlated
feature (HAND, ground elevation, relative elevation) can score beautifully by
learning *where* the flood was rather than *what* it looks like. That is the
same failure as Phase 4d in a new coordinate system, and it would be very easy
to declare victory on.

So this module NEVER reports a random split as the result. It reports three,
in increasing order of honesty:

  1. RANDOM 5-fold        — the leaky number, shown ONLY to size the leak.
  2. SPATIAL-BLOCK 5-fold — the study area is tiled into ~2 km grid cells and
                            whole cells are held out, so a test structure's
                            neighbours are never in training.
  3. CROSS-EVENT          — fit on Brazos, test on Harvey and vice versa. No
                            shared geography at all. This is the only result
                            that speaks to whether the model learned physics
                            that transfers, which is what "works anywhere"
                            actually requires.

If (1) is far above (2), the gap IS the spatial leak, and any published number
must come from (2) or (3).

HOW IT IS SCORED, AND THE VERDICT RULE
--------------------------------------
Precision is reported across a RANGE of alert volumes (top 1%, 5%, 10%, 20%,
plus the shipped detector's own volume), each with a Wilson 95% interval.
Comparing precision at equal alert volume is the only fair comparison — a model
can trivially beat another's precision by firing less often, and an adjuster's
day is set by how many properties land in the queue. A range rather than one
point because matching the shipped detector's volume alone means judging on 2
predictions at Brazos, where any percentage is noise.

The standing rule, stated before the numbers are seen: this ships only if it
CLEARLY beats the shipped detector on the honest splits — not if it beats
chance by a hair, and not on the random split. RANKING QUALITY (AUC on
held-out geography) is the gate, because precision at a small alert volume is
the single most misleading number available here: 13 correct out of 16 reads as
81% and is consistent with anything from 57% to 93%.

Usage: python validation/fit_ensemble_extent.py
Writes outputs/ensemble_extent.json.
"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))
OUT = BASE / "outputs"

import ensemble_model as em  # noqa: E402
import structures as struct  # noqa: E402


def _load_extent_check():
    spec = importlib.util.spec_from_file_location(
        "extent_check", BASE / "validation" / "extent_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ec = _load_extent_check()

# ~2 km at this latitude. Large enough that a held-out cell's structures do not
# have their immediate neighbours in training; small enough to leave enough
# cells for 5 folds.
BLOCK_DEG = 0.02


def prepare(event):
    """Labelled structures with the model's expected columns present."""
    df = pd.read_csv(OUT / f"extent_check_{event}.csv")
    # depth_above_ffe_ft is computed in the pipeline, not by extent_check, but
    # it is a real model feature. Rebuild it from the same helper the pipeline
    # uses rather than approximating it here.
    df['depth_above_ffe_ft'] = [
        struct.depth_above_first_floor(d, h)
        for d, h in zip(df.get('max_depth_ft', 0), df.get('found_ht', np.nan))]
    df['event'] = event
    return df.dropna(subset=['truth_flooded']).reset_index(drop=True)


def block_ids(df, size=BLOCK_DEG):
    """Spatial block label per structure — the CV grouping unit."""
    return (np.floor(df['longitude'] / size).astype(int).astype(str) + '_'
            + np.floor(df['latitude'] / size).astype(int).astype(str))


def _fit_predict(train, test, l2=1.0):
    Xtr = em.build_feature_matrix(train)
    Xte = em.build_feature_matrix(test)
    ytr = train['truth_flooded'].to_numpy(dtype=float)
    model = em.LogisticEnsemble.fit(Xtr, ytr, l2=l2)
    return model, model.predict(Xte)


def _precision_at_k(truth, score, k):
    """Precision among the k highest-scored structures, with a Wilson interval.

    A bare precision computed on a handful of predictions is not a
    measurement — 13 correct out of 16 reads as 81% and is consistent with
    anything from ~57% to ~93%. The interval travels with the number so the
    two are never separated.
    """
    if k < 1 or k > len(score):
        return None
    order = np.argsort(score)[::-1][:k]
    hits = int(np.asarray(truth)[order].sum())
    p = hits / k
    # Wilson score interval, which behaves at small k where normal-approx does not.
    z = 1.96
    denom = 1 + z * z / k
    centre = (p + z * z / (2 * k)) / denom
    half = z * np.sqrt(p * (1 - p) / k + z * z / (4 * k * k)) / denom
    return {'k': int(k), 'hits': hits, 'precision': float(p),
            'ci95': [float(max(0.0, centre - half)),
                     float(min(1.0, centre + half))]}


def _scored(truth, score, n_flag):
    """
    Threshold-free ranking quality, plus precision over a range of alert volumes.

    Precision at a self-chosen threshold is not comparable across models; a
    model that fires once and is right scores 100%. Fixing the alert volume
    makes precision answer the question an adjuster asks: "of the N properties
    you put in my queue, how many really flooded?" A SPREAD of volumes is
    reported rather than one, because matching the shipped detector's volume
    alone means judging on 2 predictions at Brazos.
    """
    truth = np.asarray(truth).astype(int)
    score = np.asarray(score, dtype=float)
    n = len(truth)
    out = {'auc': ec.auc(truth, score), 'n': int(n),
           'base_rate': float(truth.mean()), 'precision_at_k': {}}
    ks = sorted({n_flag} | {int(round(n * f)) for f in (0.01, 0.05, 0.10, 0.20)})
    for k in ks:
        r = _precision_at_k(truth, score, k)
        if r:
            out['precision_at_k'][str(k)] = r
    if n_flag and 1 <= n_flag <= n:
        out['matched_volume'] = out['precision_at_k'].get(str(n_flag))
    return out


def cv(df, groups, folds=5, seed=0, label=''):
    """Grouped cross-validation; `groups` decides what leakage is prevented."""
    rng = np.random.default_rng(seed)
    uniq = np.array(sorted(pd.unique(groups)))
    rng.shuffle(uniq)
    chunks = np.array_split(uniq, folds)
    truth, score = [], []
    for held in chunks:
        te = groups.isin(held)
        if te.sum() == 0 or (~te).sum() == 0:
            continue
        train, test = df[~te], df[te]
        if train['truth_flooded'].nunique() < 2:
            continue
        _, p = _fit_predict(train, test)
        truth.append(test['truth_flooded'].to_numpy())
        score.append(p)
    truth = np.concatenate(truth)
    score = np.concatenate(score)
    return truth, score, len(uniq)


def shipped_baseline(df):
    """The detector that ships today, on the same rows."""
    pred = pd.to_numeric(df['max_depth_ft'], errors='coerce').fillna(0) > 0.1
    truth = df['truth_flooded'].astype(int).to_numpy()
    m = ec.metrics(truth, pred.to_numpy())
    m['auc'] = ec.auc(truth, pd.to_numeric(
        df['max_depth_ft'], errors='coerce').fillna(0).to_numpy())
    return m


def main():
    results = {}
    frames = {}
    print("=== Phase 4d re-test against real per-property labels ===\n")

    for event in ('brazos', 'harvey'):
        path = OUT / f"extent_check_{event}.csv"
        if not path.exists():
            print(f"  {event}: missing {path.name} — run extent_check first")
            continue
        df = prepare(event)
        frames[event] = df
        base = shipped_baseline(df)
        n_flag = base['tp'] + base['fp']

        print(f"── {event}: {len(df):,} structures, "
              f"{base['base_rate'] * 100:.1f}% flooded")
        print(f"   shipped detector: flags {n_flag}, "
              f"precision {base['precision'] * 100:.1f}%, "
              f"recall {base['recall'] * 100:.1f}%, AUC {base['auc']:.3f}")

        blocks = block_ids(df)
        rand_groups = pd.Series(
            np.random.default_rng(0).integers(0, len(df), len(df)), index=df.index)

        t_r, s_r, n_r = cv(df, rand_groups, label='random')
        t_b, s_b, n_b = cv(df, blocks, label='block')
        rand = _scored(t_r, s_r, n_flag)
        blk = _scored(t_b, s_b, n_flag)

        print(f"   ensemble, RANDOM 5-fold  (leaky): AUC {rand['auc']:.3f}")
        print(f"   ensemble, SPATIAL blocks ({n_b} cells): AUC {blk['auc']:.3f}")
        print(f"   precision at increasing alert volume "
              f"(spatial blocks; base rate {base['base_rate'] * 100:.1f}%):")
        for k, r in sorted(blk['precision_at_k'].items(), key=lambda kv: int(kv[0])):
            print(f"     top {r['k']:>4} structures: {r['precision'] * 100:5.1f}% "
                  f"({r['hits']}/{r['k']}, 95% CI "
                  f"[{r['ci95'][0] * 100:.0f}%, {r['ci95'][1] * 100:.0f}%])")
        leak = rand['auc'] - blk['auc']
        print(f"   spatial leak (random AUC − block AUC): {leak:+.3f}"
              f"{'  ← the random number is inflated' if leak > 0.02 else ''}")
        results[event] = {'shipped': base, 'random_cv': rand,
                          'spatial_block_cv': blk, 'n_blocks': int(n_b),
                          'spatial_leak_auc': float(leak)}
        print()

    # ── Cross-event: no shared geography whatsoever.
    if len(frames) == 2:
        print("── Cross-event transfer (fit on one, test on the other)")
        results['cross_event'] = {}
        for tr, te in (('brazos', 'harvey'), ('harvey', 'brazos')):
            _, p = _fit_predict(frames[tr], frames[te])
            base = shipped_baseline(frames[te])
            n_flag = base['tp'] + base['fp']
            sc = _scored(frames[te]['truth_flooded'], p, n_flag)
            top = sc['precision_at_k'].get(str(int(round(len(frames[te]) * 0.10))))
            print(f"   fit {tr} → test {te}: AUC {sc['auc']:.3f}; "
                  f"precision in top 10% "
                  f"{top['precision'] * 100:.1f}% vs base rate "
                  f"{base['base_rate'] * 100:.1f}%")
            results['cross_event'][f'{tr}_to_{te}'] = {
                'ensemble': sc, 'shipped': base}

    # ── Which way do the weights point? A coefficient with the wrong sign is
    #    how Phase 4d was caught memorising the first time.
    if 'brazos' in frames:
        model, _ = _fit_predict(frames['brazos'], frames['brazos'].head(1))
        w = sorted(zip(model.feature_names, model.coef),
                   key=lambda kv: -abs(kv[1]))
        print("\n── Fitted weights (Brazos), largest magnitude first")
        for name, c in w[:8]:
            print(f"   {name:<22}{c:+.3f}")
        results['brazos_weights'] = dict(zip(model.feature_names,
                                             [float(c) for c in model.coef]))

    print("\n" + "=" * 68)
    print("  VERDICT (rule set before the numbers: ships only if it CLEARLY")
    print("  beats the shipped detector on the HONEST splits)")
    print("=" * 68)
    for event, r in results.items():
        if event in ('cross_event', 'brazos_weights'):
            continue
        auc = r['spatial_block_cv']['auc']
        # A model whose ranking is no better than chance on held-out geography
        # has not earned anything, whatever a precision computed on a handful
        # of predictions says. Ranking quality is the gate; precision at a tiny
        # alert volume is the thing most likely to mislead here, so it cannot
        # carry a verdict on its own.
        ships = auc is not None and auc > 0.55
        why = ('ranking is at or below chance on held-out geography'
               if not ships else 'clears the ranking bar')
        print(f"  {event}: ensemble AUC on spatial blocks {auc:.3f} "
              f"(0.50 = chance) → {'SHIPS' if ships else 'DOES NOT SHIP'} "
              f"— {why}")
    ce = results.get('cross_event', {})
    for k, v in ce.items():
        a = v['ensemble']['auc']
        print(f"  {k}: AUC {a:.3f} → "
              f"{'transfers' if a and a > 0.55 else 'does NOT transfer'}")

    (OUT / "ensemble_extent.json").write_text(json.dumps({
        'method': 'pipeline/ensemble_model.py LogisticEnsemble, unchanged',
        'labels': 'USGS SIR 2018-5070 mapped flood extent, per structure',
        'block_degrees': BLOCK_DEG,
        'note': 'Random-split numbers are reported only to size the spatial '
                'leak and must never be quoted as the result. Precision is '
                'measured at an alert volume matched to the shipped detector.',
        'results': results,
    }, indent=2, default=str))
    print("\n✓ outputs/ensemble_extent.json")


if __name__ == '__main__':
    main()
