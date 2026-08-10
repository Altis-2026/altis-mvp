#!/usr/bin/env python3
"""
ensemble_model.py — Phase 4d. A LEARNED combination of the detector's signals,
replacing hand-set thresholds and a blunt max().

WHY THIS EXISTS
---------------
Every signal the pipeline produces is currently combined by rules a human
wrote: the coverage term takes max(binary, sub-pixel, dual-pol); the ensemble
vote applies fixed HAND cutoffs; confidence adds and subtracts fixed points.
Those rules were reasonable priors, and Phase 4c showed the cost of them.

Dual-pol carries real information — it improved ranking (AUC) by a margin of
about 2 standard errors across 267 grouped splits. Wired in through max() it
made calibration WORSE by about 7 standard errors, because max() forces a weak
signal to speak at the same volume as a strong one. A weak-but-real signal
should nudge a probability, not overwrite it. That is exactly what a fitted
weight does and a max() cannot.

WHAT THIS IS NOT
----------------
This does not invent information. It reweights signals that were already
measured. If the underlying detector cannot see a flood — the Harvey case,
where dense urban terrain returned essentially nothing — no amount of
reweighting will conjure it, and the honest output there stays "no signal".

LEAKAGE, WHICH IS THE REAL RISK HERE
------------------------------------
Ground truth is zip-resolution. A model given features that identify a zip can
memorise that zip's base rate and post a beautiful in-sample number that means
nothing. Two defences, both mandatory:
  1. Every evaluation uses zip-GROUPED splits (train and test zips disjoint).
  2. No feature is derived from the zip, the label, or any aggregate over
     either. The feature list below is per-property physical measurement only.

Pure numpy, so the fitted model serialises to a small JSON blob that can be
committed and replayed deterministically — the same property calibration.py
holds, and the reason neither uses scikit-learn.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES
# ─────────────────────────────────────────────────────────────────────────────
#
# Each entry is (name, builder). Builders take the raw detector DataFrame and
# return a float column. Missing inputs never become a silent zero: where
# "absent" and "zero" mean different things, an explicit _known indicator
# accompanies the value, and the model learns what to do with the pair.
#
# Deliberately EXCLUDED, and why:
#   - anything zip-derived: that is the label's resolution (leakage)
#   - val_struct / val_cont: exposure value predicts CLAIM SIZE, not whether
#     water arrived. Including it teaches the model that expensive homes flood,
#     which is a property of who buys insurance, not of hydrology.
#   - med_yr_blt: same problem, plus it proxies neighbourhood identity.

def _num(df, col, default=0.0):
    if col not in df.columns:
        return pd.Series(np.full(len(df), default, dtype=float), index=df.index)
    return pd.to_numeric(df[col], errors='coerce').fillna(default).astype(float)


def _known(df, col):
    """1.0 where the column has a real value, 0.0 where it is missing."""
    if col not in df.columns:
        return pd.Series(np.zeros(len(df)), index=df.index)
    return pd.to_numeric(df[col], errors='coerce').notna().astype(float)


FEATURES = [
    # ── Direct water evidence
    ('coverage_binary',   lambda d: _num(d, 'pct_flooded') / 100.0),
    ('depth_ft',          lambda d: _num(d, 'max_depth_ft').clip(0, 15)),
    ('dpol_water',        lambda d: _num(d, 'dpol_water')),
    ('dpol_available',    lambda d: _num(d, 'dpol_available')),
    ('water_fraction',    lambda d: _num(d, 'water_fraction')),

    # ── Optical corroboration. The pct is only meaningful when a cloud-free
    #    scene existed, so the indicator rides alongside it.
    ('optical_water_pct', lambda d: _num(d, 'optical_water_pct')),
    ('optical_available', lambda d: _num(d, 'optical_available')),

    # ── Terrain plausibility. HAND is "how high above the nearest drainage",
    #    so LOW is flood-prone. Inverted and squashed rather than fed raw: the
    #    difference between 2 ft and 12 ft above drainage matters enormously,
    #    the difference between 200 ft and 210 ft not at all, and a linear term
    #    would let a mountain dominate the fit.
    ('hand_proximity',    lambda d: 1.0 / (1.0 + _num(d, 'hand_ft', 0.0).clip(0, 300))),
    ('hand_known',        lambda d: (_num(d, 'hand_ft', -1.0) >= 0).astype(float)),
    ('rel_elev_ft',       lambda d: _num(d, 'rel_elev_ft').clip(0, 50)),

    # ── Detection-quality context. A large water-surface spread means the
    #    depth estimate came from an inconsistent neighbourhood; urban terrain
    #    is where SAR is least reliable. Both are reasons to trust a positive
    #    LESS, and the model is free to learn that sign for itself.
    ('wse_spread_ft',     lambda d: _num(d, 'wse_spread_ft').clip(0, 20)),
    ('urban_flag',        lambda d: _num(d, 'urban_flag')),

    # ── Structure exposure (Phase 2). Depth above first floor is the quantity
    #    damage actually depends on; the indicator marks where NSI gave us a
    #    foundation height at all.
    ('depth_above_ffe_ft', lambda d: _num(d, 'depth_above_ffe_ft').clip(-5, 15)),
    ('ffe_known',          lambda d: _known(d, 'depth_above_ffe_ft')),
]

FEATURE_NAMES = [n for n, _ in FEATURES]


def build_feature_matrix(df: pd.DataFrame) -> np.ndarray:
    """Assemble the model matrix, in a fixed, serialised column order."""
    cols = [builder(df).to_numpy(dtype=float) for _, builder in FEATURES]
    X = np.column_stack(cols)
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)


# ─────────────────────────────────────────────────────────────────────────────
# MODEL
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LogisticEnsemble:
    """
    L2-regularised logistic regression, fitted by Newton-Raphson (IRLS).

    Logistic rather than a gradient-boosted forest, on purpose. With 14 zips of
    ground truth, a flexible learner has enough capacity to fit the zips rather
    than the physics, and Phase 4c already showed how wide the between-zip
    variance is. A linear model in well-chosen features has coefficients an
    adjuster or a regulator can read, signs that can be checked against
    hydrology, and far less room to memorise. Revisit when there are enough
    independent events to hold whole EVENTS out, not just zips.
    """
    coef: list = field(default_factory=list)
    intercept: float = 0.0
    feature_names: list = field(default_factory=list)
    x_mean: list = field(default_factory=list)
    x_std: list = field(default_factory=list)
    l2: float = 1.0
    n_train: int = 0
    method: str = "logistic_l2"

    @staticmethod
    def fit(X, y, l2: float = 1.0, max_iter: int = 100, tol: float = 1e-8,
            feature_names=None) -> "LogisticEnsemble":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)

        mu = X.mean(axis=0)
        sd = X.std(axis=0)
        # A constant feature carries no information; setting sd to 1 leaves it
        # as an all-zeros column after centring, which the L2 penalty then
        # drives to a zero weight. That is the correct outcome and avoids a
        # divide-by-zero that would produce NaN coefficients.
        sd = np.where(sd < 1e-12, 1.0, sd)
        Z = (X - mu) / sd

        n, p = Z.shape
        Zb = np.column_stack([np.ones(n), Z])       # intercept column
        w = np.zeros(p + 1)

        # Penalty applies to slopes only — penalising the intercept would bias
        # the model away from the observed base rate for no good reason.
        pen = np.full(p + 1, l2, dtype=float)
        pen[0] = 0.0

        for _ in range(max_iter):
            eta = np.clip(Zb @ w, -30, 30)
            mu_hat = 1.0 / (1.0 + np.exp(-eta))
            grad = Zb.T @ (mu_hat - y) + pen * w
            s = np.clip(mu_hat * (1.0 - mu_hat), 1e-9, None)
            H = (Zb * s[:, None]).T @ Zb + np.diag(pen)
            try:
                step = np.linalg.solve(H, grad)
            except np.linalg.LinAlgError:
                step = np.linalg.lstsq(H, grad, rcond=None)[0]
            w_new = w - step
            if not np.all(np.isfinite(w_new)):
                break
            if np.max(np.abs(w_new - w)) < tol:
                w = w_new
                break
            w = w_new

        return LogisticEnsemble(
            coef=[float(v) for v in w[1:]],
            intercept=float(w[0]),
            feature_names=list(feature_names or FEATURE_NAMES),
            x_mean=[float(v) for v in mu],
            x_std=[float(v) for v in sd],
            l2=float(l2),
            n_train=int(n),
        )

    def predict(self, X) -> np.ndarray:
        """Calibrated-ish probability in (0, 1). One row per property."""
        X = np.asarray(X, dtype=float)
        mu = np.asarray(self.x_mean, dtype=float)
        sd = np.asarray(self.x_std, dtype=float)
        Z = (X - mu) / np.where(sd < 1e-12, 1.0, sd)
        eta = np.clip(Z @ np.asarray(self.coef, dtype=float) + self.intercept, -30, 30)
        return 1.0 / (1.0 + np.exp(-eta))

    def weights(self) -> dict:
        """Feature -> standardised coefficient, largest magnitude first."""
        pairs = sorted(zip(self.feature_names, self.coef),
                       key=lambda kv: -abs(kv[1]))
        return {k: round(v, 5) for k, v in pairs}

    def to_dict(self) -> dict:
        return asdict(self)


def load_ensemble(blob: dict) -> LogisticEnsemble:
    return LogisticEnsemble(**{k: v for k, v in blob.items()
                               if k in LogisticEnsemble.__dataclass_fields__})
