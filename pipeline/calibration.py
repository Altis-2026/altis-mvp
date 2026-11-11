"""
calibration.py — Turn hand-tuned scores into calibrated probabilities.

The triage confidence score (04_triage_notes.py) is a hand-tuned 30-97 number.
It ranks properties sensibly but it is NOT a probability — a "75" does not mean
"75% chance this property actually flooded." For a defensible, sales-grade
accuracy claim we need a *calibrated* probability: one where, among all
properties assigned p=0.80, about 80% truly flooded.

This module fits a calibration map (raw score -> calibrated probability) against
ground-truth labels, using a proper held-out split so the reported numbers are
honest and not memorised. Two standard, well-understood methods are provided:

  - Isotonic regression (Pool Adjacent Violators): non-parametric, monotone.
    Flexible; preferred when there are enough labels.
  - Platt scaling (logistic): 2-parameter sigmoid. Robust on small samples,
    where isotonic would overfit.

Everything here is pure numpy/scipy — no scikit-learn dependency — so the fitted
calibrator serialises to a small JSON blob that can be committed and replayed
deterministically at inference time.

IMPORTANT — honesty of the number:
  The quality of any calibration is bounded by the quality of the labels. Our
  available ground truth (FEMA Individual Assistance) is released at ZIP-code
  resolution, so labels are zip-level, and the held-out split MUST be grouped
  by zip (train and test zips disjoint) — otherwise properties from the same
  zip leak their shared label across the split and the metrics look better than
  reality. group_train_test_split() enforces this.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# RAW FLOOD-LIKELIHOOD SCORE (the quantity we calibrate)
# ─────────────────────────────────────────────────────────────────────────────

def raw_flood_score(pct_flooded_frac, max_depth_ft, depth_ref_ft: float = 3.0) -> float:
    """
    A monotonic, model-side flood-evidence score in [0, 1], blending coverage
    and depth. This — not the hand-tuned triage confidence — is what we map to a
    calibrated probability of flooding, since it is a direct measure of flood
    evidence rather than decision certainty.

    pct_flooded_frac is a 0-1 fraction (caller divides by 100 if working from
    the final CSV's percentage column).
    """
    try:
        pct = min(max(float(pct_flooded_frac), 0.0), 1.0)
    except (TypeError, ValueError):
        pct = 0.0
    try:
        depth_term = min(max(float(max_depth_ft), 0.0) / depth_ref_ft, 1.0)
    except (TypeError, ValueError):
        depth_term = 0.0
    return round(0.5 * pct + 0.5 * depth_term, 4)


# ─────────────────────────────────────────────────────────────────────────────
# ISOTONIC REGRESSION (Pool Adjacent Violators)
# ─────────────────────────────────────────────────────────────────────────────

def _pav(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """
    Pool Adjacent Violators on values y (with weights w), already sorted by x.
    Returns the fitted non-decreasing values aligned to the input order.
    """
    y = y.astype(float).copy()
    w = w.astype(float).copy()
    n = len(y)

    # Each "block" is a contiguous run pooled to a common value.
    # Stacks of (value, weight, start_index).
    vals = []
    wts = []
    idxs = []

    for i in range(n):
        v = y[i]
        cw = w[i]
        start = i
        # Merge with previous block while it violates monotonicity.
        while vals and vals[-1] >= v:
            pv, pw = vals.pop(), wts.pop()
            start = idxs.pop()
            v = (pv * pw + v * cw) / (pw + cw)
            cw = pw + cw
        vals.append(v)
        wts.append(cw)
        idxs.append(start)

    # Expand blocks back to per-sample fitted values.
    fitted = np.empty(n, dtype=float)
    for b in range(len(vals)):
        start = idxs[b]
        end = idxs[b + 1] if b + 1 < len(idxs) else n
        fitted[start:end] = vals[b]
    return fitted


@dataclass
class IsotonicCalibrator:
    """Monotone calibration map stored as interpolation knots."""
    x_knots: list
    y_knots: list
    method: str = "isotonic"

    @classmethod
    def fit(cls, scores: np.ndarray, labels: np.ndarray) -> "IsotonicCalibrator":
        scores = np.asarray(scores, dtype=float)
        labels = np.asarray(labels, dtype=float)
        order = np.argsort(scores, kind="mergesort")
        xs = scores[order]
        ys = labels[order]
        w = np.ones_like(ys)
        fitted = _pav(ys, w)

        # Collapse duplicate x values to a single knot (mean of fitted), keeping
        # the map a clean step/linear function for np.interp at predict time.
        uniq_x, inv = np.unique(xs, return_inverse=True)
        knot_y = np.zeros_like(uniq_x, dtype=float)
        counts = np.zeros_like(uniq_x, dtype=float)
        np.add.at(knot_y, inv, fitted)
        np.add.at(counts, inv, 1.0)
        knot_y = knot_y / np.maximum(counts, 1.0)
        # Enforce monotonicity defensively after averaging.
        knot_y = np.maximum.accumulate(knot_y)
        return cls(x_knots=uniq_x.tolist(), y_knots=knot_y.tolist())

    def predict(self, scores) -> np.ndarray:
        scores = np.asarray(scores, dtype=float)
        x = np.asarray(self.x_knots, dtype=float)
        y = np.asarray(self.y_knots, dtype=float)
        if len(x) == 1:
            return np.clip(np.full_like(scores, y[0]), 0.0, 1.0)
        # Linear interpolation, clamped to the fitted range at the ends.
        out = np.interp(scores, x, y, left=y[0], right=y[-1])
        return np.clip(out, 0.0, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# PLATT SCALING (logistic / sigmoid)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlattCalibrator:
    """2-parameter sigmoid calibration: p = 1 / (1 + exp(A*x + B))."""
    a: float
    b: float
    x_mean: float
    x_std: float
    method: str = "platt"

    @classmethod
    def fit(cls, scores: np.ndarray, labels: np.ndarray) -> "PlattCalibrator":
        from scipy.optimize import minimize

        scores = np.asarray(scores, dtype=float)
        labels = np.asarray(labels, dtype=float)

        # Standardise the score for numerical stability of the optimiser.
        x_mean = float(scores.mean())
        x_std = float(scores.std()) or 1.0
        x = (scores - x_mean) / x_std

        # Platt's label smoothing targets — avoids overfit on small samples and
        # the degenerate all-0/all-1 case (Platt 1999).
        n_pos = float(labels.sum())
        n_neg = float(len(labels) - n_pos)
        hi = (n_pos + 1.0) / (n_pos + 2.0)
        lo = 1.0 / (n_neg + 2.0)
        t = np.where(labels > 0.5, hi, lo)

        def nll(params):
            a, b = params
            z = a * x + b
            # log(1+exp(z)) computed stably
            log1pexp = np.where(z > 0, z + np.log1p(np.exp(-z)), np.log1p(np.exp(z)))
            # p = sigmoid(-z); cross-entropy against smoothed target t
            # loss = -[ t*log(p) + (1-t)*log(1-p) ]
            #      = t*log1pexp_pos ... easier: p = 1/(1+exp(z))
            log_p = -log1pexp                 # log(sigmoid(-z))
            log_1mp = z - log1pexp            # log(1 - sigmoid(-z)) = log(sigmoid(z)) = z - log1pexp
            return float(-np.sum(t * log_p + (1.0 - t) * log_1mp))

        res = minimize(nll, x0=np.array([1.0, 0.0]), method="BFGS")
        a, b = float(res.x[0]), float(res.x[1])
        return cls(a=a, b=b, x_mean=x_mean, x_std=x_std)

    def predict(self, scores) -> np.ndarray:
        scores = np.asarray(scores, dtype=float)
        x = (scores - self.x_mean) / (self.x_std or 1.0)
        z = self.a * x + self.b
        return np.clip(1.0 / (1.0 + np.exp(z)), 0.0, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# CALIBRATION QUALITY METRICS
# ─────────────────────────────────────────────────────────────────────────────

def brier_score(probs, labels) -> float:
    """Mean squared error between predicted probability and outcome. Lower=better."""
    probs = np.asarray(probs, dtype=float)
    labels = np.asarray(labels, dtype=float)
    return float(np.mean((probs - labels) ** 2))


def reliability_curve(probs, labels, n_bins: int = 10):
    """
    Bin predictions and return per-bin (mean_predicted, observed_frequency, count).
    A perfectly calibrated model lies on the diagonal mean_predicted==observed.
    """
    probs = np.asarray(probs, dtype=float)
    labels = np.asarray(labels, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (probs >= lo) & (probs < hi if i < n_bins - 1 else probs <= hi)
        cnt = int(mask.sum())
        if cnt == 0:
            continue
        out.append({
            "bin_lo": round(float(lo), 3),
            "bin_hi": round(float(hi), 3),
            "mean_predicted": round(float(probs[mask].mean()), 4),
            "observed_frequency": round(float(labels[mask].mean()), 4),
            "count": cnt,
        })
    return out


def expected_calibration_error(probs, labels, n_bins: int = 10) -> float:
    """Weighted average gap between confidence and accuracy across bins. Lower=better."""
    probs = np.asarray(probs, dtype=float)
    labels = np.asarray(labels, dtype=float)
    n = len(probs)
    if n == 0:
        return float("nan")
    curve = reliability_curve(probs, labels, n_bins)
    ece = 0.0
    for b in curve:
        ece += (b["count"] / n) * abs(b["mean_predicted"] - b["observed_frequency"])
    return float(ece)


def classification_metrics(predictions, labels) -> dict:
    """Precision / recall / F1 / accuracy for binary predictions vs binary labels."""
    pred = np.asarray(predictions, dtype=float) > 0.5
    lab = np.asarray(labels, dtype=float) > 0.5
    tp = int(np.sum(pred & lab))
    fp = int(np.sum(pred & ~lab))
    fn = int(np.sum(~pred & lab))
    tn = int(np.sum(~pred & ~lab))
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)
    total = tp + fp + fn + tn
    accuracy = (tp + tn) / total if total else None
    return {
        "true_positive": tp, "false_positive": fp,
        "false_negative": fn, "true_negative": tn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "accuracy": round(accuracy, 4) if accuracy is not None else None,
        "support": total,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HONEST HOLD-OUT SPLIT (grouped by zip)
# ─────────────────────────────────────────────────────────────────────────────

def group_train_test_split(groups, test_fraction: float = 0.3, seed: int = 42):
    """
    Split row indices into train/test such that no GROUP (e.g. zip code) appears
    in both. This is the honest split for zip-resolution labels: random
    per-property splitting would leak a zip's shared label across train and test
    and inflate the reported accuracy.

    Returns (train_idx, test_idx) as numpy arrays of row positions.
    """
    groups = np.asarray(groups)
    unique = np.array(sorted(set(groups.tolist())))
    rng = np.random.default_rng(seed)
    rng.shuffle(unique)
    n_test = max(1, int(round(len(unique) * test_fraction)))
    test_groups = set(unique[:n_test].tolist())

    test_idx = np.array([i for i, g in enumerate(groups) if g in test_groups], dtype=int)
    train_idx = np.array([i for i, g in enumerate(groups) if g not in test_groups], dtype=int)
    return train_idx, test_idx


# ─────────────────────────────────────────────────────────────────────────────
# FIT / SELECT / SERIALISE
# ─────────────────────────────────────────────────────────────────────────────

def fit_calibrator(scores, labels, method: str = "auto"):
    """
    Fit a calibrator. method='auto' picks Platt when labels are scarce
    (< 200 samples or < 15 of either class), else isotonic.
    """
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=float)
    n_pos = int(labels.sum())
    n_neg = int(len(labels) - n_pos)

    if method == "auto":
        method = "platt" if (len(labels) < 200 or n_pos < 15 or n_neg < 15) else "isotonic"

    if method == "isotonic":
        return IsotonicCalibrator.fit(scores, labels)
    if method == "platt":
        return PlattCalibrator.fit(scores, labels)
    raise ValueError(f"Unknown calibration method: {method}")


def load_calibrator(blob: dict):
    """Reconstruct a calibrator from its serialised dict."""
    method = blob.get("method")
    if method == "isotonic":
        return IsotonicCalibrator(x_knots=blob["x_knots"], y_knots=blob["y_knots"])
    if method == "platt":
        return PlattCalibrator(a=blob["a"], b=blob["b"],
                               x_mean=blob["x_mean"], x_std=blob["x_std"])
    raise ValueError(f"Unknown calibration method in blob: {method}")


def calibrator_to_dict(cal) -> dict:
    return asdict(cal)


def fit_and_evaluate(scores, labels, groups=None, method: str = "auto",
                     test_fraction: float = 0.3, seed: int = 42,
                     decision_threshold: float = 0.5) -> dict:
    """
    Full honest evaluation: split (grouped if groups given), fit on train,
    evaluate calibration + classification on the held-out test set only.

    Returns a JSON-serialisable dict containing the fitted calibrator (refit on
    ALL data for production use), plus held-out metrics that are the honest
    headline numbers.
    """
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=float)
    n = len(scores)

    if groups is not None:
        train_idx, test_idx = group_train_test_split(groups, test_fraction, seed)
        split_kind = "grouped_by_zip"
    else:
        rng = np.random.default_rng(seed)
        perm = rng.permutation(n)
        cut = max(1, int(round(n * test_fraction)))
        test_idx, train_idx = perm[:cut], perm[cut:]
        split_kind = "random_property_level"

    # Guard: need both classes present in train to fit anything meaningful.
    train_labels = labels[train_idx]
    holdout_ok = (len(train_idx) > 0 and len(test_idx) > 0
                  and 0 < train_labels.sum() < len(train_labels))

    result = {
        "n_total": n,
        "n_train": int(len(train_idx)),
        "n_test": int(len(test_idx)),
        "n_positive": int(labels.sum()),
        "split_kind": split_kind,
        "seed": seed,
        "test_fraction": test_fraction,
        "decision_threshold": decision_threshold,
    }

    if holdout_ok:
        cal_eval = fit_calibrator(scores[train_idx], train_labels, method)
        test_probs = cal_eval.predict(scores[test_idx])
        test_labels = labels[test_idx]
        result["holdout_metrics"] = {
            "brier_score": round(brier_score(test_probs, test_labels), 4),
            "expected_calibration_error": round(
                expected_calibration_error(test_probs, test_labels), 4),
            "reliability_curve": reliability_curve(test_probs, test_labels),
            "classification": classification_metrics(
                test_probs >= decision_threshold, test_labels),
            "method": cal_eval.method,
        }
    else:
        result["holdout_metrics"] = None
        result["warning"] = (
            "Insufficient labels or single-class training set — held-out metrics "
            "could not be computed. Provide more ground-truth labels.")

    # Production calibrator: refit on ALL labelled data (standard practice once
    # the held-out number has been recorded honestly above).
    production_cal = fit_calibrator(scores, labels, method)
    result["calibrator"] = calibrator_to_dict(production_cal)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# REPEATED / PAIRED HOLD-OUT EVALUATION (Phase 4c)
# ─────────────────────────────────────────────────────────────────────────────
#
# WHY THIS EXISTS. fit_and_evaluate() draws ONE grouped split at seed 42. With
# 15 zips and a 30% test fraction, that is 4-5 zips deciding the headline
# number, and a single Brier score from ~550 properties. Two candidate signals
# whose true difference is small are then compared through a lens that is
# mostly noise: Phase 4b's dual-pol score moved the held-out Brier from 0.1714
# to 0.1719, and there was no way to tell whether that 0.0005 was a real
# regression or which zips happened to land in the test fold.
#
# The fix is not a bigger test set — there are only 15 zips. It is to draw MANY
# splits and look at the distribution, and crucially to compare candidates on
# the SAME splits (paired), so the split noise cancels in the difference. An
# unpaired comparison of two noisy means needs a large effect to resolve; a
# paired one only needs the DIFFERENCE to be consistent.

def repeated_group_evaluation(scores, labels, groups, n_repeats: int = 200,
                              method: str = "auto", test_fraction: float = 0.3,
                              seed: int = 0) -> dict:
    """
    Distribution of held-out metrics across `n_repeats` grouped splits.

    Returns per-metric mean, standard deviation and 5th/95th percentiles, plus
    the raw per-split values so a caller can pair them against another
    candidate. Splits that fail the both-classes-present guard are skipped and
    counted rather than silently treated as zeros.
    """
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=float)

    briers, eces, skills, aucs = [], [], [], []
    used_seeds = []
    for i in range(n_repeats):
        s = seed + i
        train_idx, test_idx = group_train_test_split(groups, test_fraction, s)
        if len(train_idx) == 0 or len(test_idx) == 0:
            continue
        y_tr, y_te = labels[train_idx], labels[test_idx]
        if not (0 < y_tr.sum() < len(y_tr)) or not (0 < y_te.sum() < len(y_te)):
            continue
        cal = fit_calibrator(scores[train_idx], y_tr, method)
        p = cal.predict(scores[test_idx])
        b = brier_score(p, y_te)
        # Skill is measured against the TRAIN base rate, not the test one:
        # a production model does not get to see the test set's base rate, so
        # scoring it against that would flatter the baseline's competitor.
        base = float(y_tr.mean())
        b_const = brier_score(np.full(len(y_te), base), y_te)
        briers.append(b)
        eces.append(expected_calibration_error(p, y_te))
        skills.append(1.0 - b / b_const if b_const > 0 else float('nan'))
        aucs.append(_auc(scores[test_idx], y_te))
        used_seeds.append(s)

    def summarise(v):
        a = np.asarray([x for x in v if np.isfinite(x)], dtype=float)
        if len(a) == 0:
            return None
        return {
            "mean": round(float(a.mean()), 5),
            "std": round(float(a.std(ddof=1)), 5) if len(a) > 1 else 0.0,
            "p05": round(float(np.percentile(a, 5)), 5),
            "p95": round(float(np.percentile(a, 95)), 5),
        }

    return {
        "n_splits_used": len(used_seeds),
        "n_splits_requested": n_repeats,
        "seeds": used_seeds,
        "brier": summarise(briers),
        "ece": summarise(eces),
        "brier_skill_score": summarise(skills),
        "auc": summarise(aucs),
        "raw": {"brier": briers, "ece": eces,
                "brier_skill_score": skills, "auc": aucs},
    }


def paired_candidate_comparison(candidates: dict, labels, groups,
                                n_repeats: int = 200, method: str = "auto",
                                test_fraction: float = 0.3, seed: int = 0) -> dict:
    """
    Compare several score variants on IDENTICAL splits.

    `candidates` maps a name to a score array. Every candidate sees the same
    train/test partition at every repeat, so per-split noise — which zips
    landed in the test fold — cancels when differences are taken. This is the
    only way to resolve a small effect against 15 zips of ground truth.

    Returns each candidate's distribution plus, for every candidate after the
    first, the paired difference against the first (treated as the baseline):
    mean difference, its standard error, and the fraction of splits the
    candidate won. A mean difference smaller than its own standard error is
    noise, and is reported as such rather than as a direction.
    """
    names = list(candidates)
    if not names:
        raise ValueError("No candidates supplied.")

    per = {n: repeated_group_evaluation(candidates[n], labels, groups,
                                        n_repeats=n_repeats, method=method,
                                        test_fraction=test_fraction, seed=seed)
           for n in names}

    # Pair only over splits every candidate actually used, so the difference is
    # taken like-for-like.
    common = set(per[names[0]]["seeds"])
    for n in names[1:]:
        common &= set(per[n]["seeds"])
    common = sorted(common)

    def series(n, metric):
        idx = {s: i for i, s in enumerate(per[n]["seeds"])}
        return np.array([per[n]["raw"][metric][idx[s]] for s in common], float)

    baseline = names[0]
    comparisons = {}
    for n in names[1:]:
        cmp_metrics = {}
        for metric, better in (("brier", "lower"), ("ece", "lower"),
                               ("brier_skill_score", "higher"), ("auc", "higher")):
            a, b = series(baseline, metric), series(n, metric)
            ok = np.isfinite(a) & np.isfinite(b)
            d = b[ok] - a[ok]
            if len(d) == 0:
                cmp_metrics[metric] = None
                continue
            se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else 0.0
            wins = float((d < 0).mean() if better == "lower" else (d > 0).mean())
            mean_d = float(d.mean())
            cmp_metrics[metric] = {
                "mean_difference": round(mean_d, 6),
                "standard_error": round(se, 6),
                "win_rate": round(wins, 4),
                "n_paired": int(len(d)),
                # The honest verdict. "noise" is not a hedge — it is the
                # correct reading when the effect is smaller than the
                # uncertainty on its own estimate.
                "verdict": ("noise" if se == 0 or abs(mean_d) < se
                            else ("better" if ((mean_d < 0) == (better == "lower"))
                                  else "worse")),
            }
        comparisons[n] = cmp_metrics

    return {"baseline": baseline, "per_candidate": per,
            "n_paired_splits": len(common), "comparisons": comparisons}


def _auc(scores, labels):
    """Rank-based AUC with ties averaged. NaN when labels are single-class."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=float)
    n1 = labels.sum()
    n0 = len(labels) - n1
    if n1 == 0 or n0 == 0:
        return float('nan')
    order = np.argsort(scores, kind='mergesort')
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1, dtype=float)
    # Average ranks within tied score groups, which matters here: most
    # properties share a score of exactly 0.0.
    s_sorted = scores[order]
    i = 0
    while i < len(s_sorted):
        j = i
        while j + 1 < len(s_sorted) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = ranks[order[i:j + 1]].mean()
        i = j + 1
    return float((ranks[labels == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
