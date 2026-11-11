#!/usr/bin/env python3
"""
hand_arch_probe.py — Should HAND be the primary signal, with SAR confirming?

THE PROPOSAL BEING TESTED
-------------------------
The first per-property precision run (extent_check.py, Brazos) produced a
result that looks, at a glance, like an argument for restructuring the
pipeline: a static terrain layer that never observes the storm scored the
highest recall (91.2%) and the highest AUC (0.552) of anything tested,
beating every SAR signal.

The proposed architecture that follows from it: let HAND select the candidate
set (everything below a plausibility threshold), then use SAR / optical /
dual-pol to CONFIRM or DOWNGRADE within that set, instead of SAR being the
primary gate.

This module measures that proposal against the same real ground truth, before
anyone changes the pipeline.

WHY THE HEADLINE NUMBER IS MISLEADING, AND WHY THIS STILL RUNS
--------------------------------------------------------------
91.2% recall is not evidence of skill here. HAND ≤ 10 ft fires on 2,603 of
2,999 structures — 86.8% of them. Recall is trivially high because the rule
says "yes" to almost everyone. The number that matters is precision against
the base rate: 38.4% against a 36.5% base rate is a lift of 1.05x. A rule that
labels every structure flooded would score 36.5% precision and 100% recall.
HAND ≤ 10 ft is barely distinguishable from that rule.

So the honest framing is not "HAND wins". It is "at Brazos, NOTHING achieved
per-property discrimination — every AUC landed between 0.499 and 0.552 — and
HAND is the least-bad of a uniformly poor field."

That is still worth measuring properly rather than dismissing, for two reasons:

  1. A threshold sweep might find a HAND cut that is genuinely selective, even
     if ≤10 ft is not. That is a cheap thing to check and it has never been
     checked.
  2. If SAR carries ANY independent information, intersecting it with a HAND
     candidate set should raise precision above HAND alone — even when SAR is
     useless on its own. Conversely, if the intersection does not beat HAND
     alone, SAR is contributing nothing to the combination and the proposed
     architecture cannot work regardless of how the gate is arranged.

Point 2 is the actual decision this module exists to inform.

INPUT
-----
Reads outputs/extent_check_<event>.csv, which extent_check.py already wrote —
the structures, their USGS labels, and every sampled detector band. No Earth
Engine calls, so this is seconds rather than half an hour, and it is scored on
exactly the same rows as the headline table.

Usage: python validation/hand_arch_probe.py [event]
Writes outputs/hand_arch_<event>.json.
"""
import argparse
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


def _load_extent_check():
    """Reuse extent_check's metrics/auc rather than reimplementing them.

    Two copies of a confusion matrix in one repo is how two different
    'precisions' end up in two different documents.
    """
    spec = importlib.util.spec_from_file_location(
        "extent_check", BASE / "validation" / "extent_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ec = _load_extent_check()

# SAR/optical evidence available per structure. Each is "did this signal fire".
CONFIRMERS = ['max_depth_ft', 'water_fraction', 'dpol_water', 'double_bounce',
              'optical_water_pct']


def row(name, truth, pred, score=None):
    m = ec.metrics(truth, pred)
    m['name'] = name
    m['auc'] = ec.auc(truth, score) if score is not None else None
    m['flagged_frac'] = float(np.asarray(pred).astype(bool).mean())
    m['precision_lift'] = (m['precision'] / m['base_rate']
                           if m['base_rate'] else float('nan'))
    return m


def show(rows, title):
    print(f"\n  {title}")
    print(f"  {'rule':<42}{'flag%':>7}{'prec':>7}{'rec':>7}{'spec':>7}"
          f"{'lift':>7}")
    print("  " + "-" * 77)
    for m in rows:
        p = '  n/a' if m['precision'] != m['precision'] else f"{m['precision'] * 100:5.1f}"
        s = '  n/a' if m['specificity'] != m['specificity'] else f"{m['specificity'] * 100:5.1f}"
        lf = '  n/a' if m['precision_lift'] != m['precision_lift'] else f"{m['precision_lift']:5.2f}"
        print(f"  {m['name']:<42}{m['flagged_frac'] * 100:6.1f}%{p:>7}"
              f"{m['recall'] * 100:6.1f}%{s:>7}{lf:>7}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('event', nargs='?', default='brazos',
                    choices=sorted(ec.EXTENTS))
    args = ap.parse_args()

    src = OUT / f"extent_check_{args.event}.csv"
    if not src.exists():
        sys.exit(f"Missing {src}. Run: python validation/extent_check.py "
                 f"{args.event}")
    df = pd.read_csv(src)
    truth = df['truth_flooded'].astype(int).to_numpy()
    base = truth.mean()

    print(f"=== {args.event}: is HAND-primary a better architecture? ===")
    print(f"  {len(df):,} structures, {truth.sum():,} flooded "
          f"({base * 100:.1f}% base rate), USGS-labelled")

    hand = pd.to_numeric(df.get('hand_ft'), errors='coerce')
    valid = hand.notna() & (hand >= 0)
    print(f"  HAND available at {int(valid.sum()):,} of {len(df):,} structures")
    if valid.sum() < 50:
        sys.exit("  Too few HAND values to assess.")

    t = truth[valid.to_numpy()]
    h = hand[valid].to_numpy()
    sub = df[valid].reset_index(drop=True)

    # ── The reference points every rule below has to beat.
    refs = [
        row("label EVERY structure flooded", t, np.ones(len(t), bool)),
        row("HAND <= 10 ft (the reported result)", t, h <= 10, -h),
    ]
    show(refs, "Reference rules")
    print("\n  Read the first two rows together: 'label everything flooded' is")
    print("  the null model. A rule barely above its precision is not a")
    print("  detector, whatever its recall says.")

    # ── 1. Does ANY HAND threshold discriminate?
    sweep = []
    for thr in (1, 2, 3, 5, 7.5, 10, 15, 20, 30):
        sweep.append(row(f"HAND <= {thr} ft", t, h <= thr, -h))
    show(sweep, "HAND threshold sweep (the whole sweep, not its best point)")
    best = max((m for m in sweep if m['precision'] == m['precision']),
               key=lambda m: m['precision'])
    print(f"\n  Best precision in sweep: {best['name']} at "
          f"{best['precision'] * 100:.1f}% vs a {base * 100:.1f}% base rate "
          f"(lift {best['precision_lift']:.2f}x).")
    print(f"  HAND AUC over all structures: {ec.auc(t, -h):.3f}  "
          f"(0.5 = no information)")

    # ── 2. The actual decision: does SAR add anything INSIDE a HAND candidate set?
    present = [c for c in CONFIRMERS if c in sub.columns]
    fired = {c: pd.to_numeric(sub[c], errors='coerce').fillna(0).to_numpy() > 0
             for c in present}
    any_sar = np.zeros(len(sub), bool)
    for c in present:
        any_sar |= fired[c]

    cand = h <= 10
    combo = [
        row("HAND <= 10 ft ALONE (candidate set)", t, cand, -h),
        row("HAND <= 10 ft AND any SAR/optical fires", t, cand & any_sar),
    ]
    for c in present:
        combo.append(row(f"HAND <= 10 ft AND {c} fires", t, cand & fired[c]))
    show(combo, "Does SAR confirmation improve the HAND candidate set?")

    hand_only = combo[0]
    confirmed = combo[1]
    print("\n  THE DECISION THIS MODULE EXISTS FOR:")
    if confirmed['tp'] + confirmed['fp'] == 0:
        print("    SAR/optical fires on ZERO structures inside the HAND")
        print("    candidate set. A confirm-or-downgrade stage built on it")
        print("    would reject every candidate, or change nothing at all")
        print("    depending on which way the gate points. The proposed")
        print("    architecture cannot work with these signals.")
    else:
        d = confirmed['precision'] - hand_only['precision']
        print(f"    precision, HAND alone      {hand_only['precision'] * 100:.1f}%")
        print(f"    precision, HAND + SAR      {confirmed['precision'] * 100:.1f}%")
        print(f"    change                     {d * 100:+.1f} points, "
              f"at {confirmed['recall'] * 100:.1f}% recall "
              f"(from {hand_only['recall'] * 100:.1f}%)")
        if d <= 0.02:
            print("    SAR adds no meaningful precision inside the candidate")
            print("    set, so the architecture change is not justified.")
        else:
            print("    SAR does add precision inside the candidate set. Worth")
            print("    pursuing — but check the recall cost above first.")

    out = {'event': args.event, 'n': int(len(sub)), 'base_rate': float(base),
           'hand_auc': ec.auc(t, -h),
           'reference_rules': refs, 'hand_sweep': sweep, 'combined': combo,
           'verdict_note': 'Recall alone cannot justify a rule here; compare '
                           'precision against the base rate. HAND <= 10 ft '
                           'flags most structures, so its high recall is a '
                           'property of its permissiveness, not its skill.'}
    (OUT / f"hand_arch_{args.event}.json").write_text(
        json.dumps(out, indent=2, default=str))
    print(f"\n✓ outputs/hand_arch_{args.event}.json")


if __name__ == '__main__':
    main()
