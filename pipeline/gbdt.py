"""
gbdt.py — Gradient-boosted decision trees for binary classification, in numpy.

The susceptibility model (pipeline/susceptibility.py) is a gradient-boosted
tree ensemble on terrain + rainfall features — the model family that
consistently performs best in the flood-susceptibility literature on tabular
geospatial features. This is a compact, dependency-free implementation so
the trained model ships as JSON and scores on Railway without scikit-learn:

  * logistic loss, Newton leaf values  −G / (H + λ)
  * histogram splits on ≤ max_bins quantile bins per feature
  * depth-limited trees grown level-wise, min_child_weight, row subsampling
  * shrinkage (learning rate)

Its accuracy is cross-checked against scikit-learn's HistGradientBoosting
in tests when scikit-learn is installed.
"""
from __future__ import annotations

import json

import numpy as np


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


class GBDT:
    def __init__(self, n_trees=200, depth=4, lr=0.08, min_child_weight=20.0, lam=1.0,
                 subsample=0.8, max_bins=64, seed=0, feature_names=None):
        self.n_trees, self.depth, self.lr = n_trees, depth, lr
        self.min_child_weight, self.lam = min_child_weight, lam
        self.subsample, self.max_bins, self.seed = subsample, max_bins, seed
        self.feature_names = feature_names
        self.edges = None
        self.trees = []
        self.base = 0.0

    # ── binning ─────────────────────────────────────────────────────────
    def _fit_bins(self, X):
        self.edges = []
        for j in range(X.shape[1]):
            col = X[:, j][np.isfinite(X[:, j])]
            qs = np.unique(np.quantile(col, np.linspace(0, 1, self.max_bins + 1)[1:-1])) if col.size else np.array([])
            self.edges.append(qs)

    def _bin(self, X):
        B = np.empty(X.shape, dtype=np.int32)
        for j, e in enumerate(self.edges):
            col = np.nan_to_num(X[:, j], nan=-1e30)
            B[:, j] = np.searchsorted(e, col, side='left')   # bin b ⇔ e[b-1] < x ≤ e[b]
        return B

    # ── training ────────────────────────────────────────────────────────
    def fit(self, X, y, sample_weight=None):
        X = np.asarray(X, float)
        y = np.asarray(y, float)
        w = np.ones_like(y) if sample_weight is None else np.asarray(sample_weight, float)
        rng = np.random.default_rng(self.seed)
        self._fit_bins(X)
        B = self._bin(X)
        p0 = np.clip(np.average(y, weights=w), 1e-6, 1 - 1e-6)
        self.base = float(np.log(p0 / (1 - p0)))
        F = np.full(len(y), self.base)
        nb = [len(e) + 1 for e in self.edges]
        self.trees = []
        for _ in range(self.n_trees):
            p = _sigmoid(F)
            g = (p - y) * w
            h = np.maximum(p * (1 - p), 1e-12) * w
            idx = np.flatnonzero(rng.random(len(y)) < self.subsample) if self.subsample < 1 else np.arange(len(y))
            tree = self._grow(B, g, h, idx, nb)
            self.trees.append(tree)
            F += self._predict_tree_binned(tree, B)
        return self

    def _grow(self, B, g, h, idx, nb):
        feat, thr_bin, left, right, value = [], [], [], [], []

        def new_node():
            feat.append(-1); thr_bin.append(0); left.append(-1); right.append(-1); value.append(0.0)
            return len(feat) - 1
        root = new_node()
        frontier = [(root, idx)]
        for _level in range(self.depth):
            nxt = []
            for node, rows in frontier:
                G, H = g[rows].sum(), h[rows].sum()
                best = (0.0, -1, -1)
                parent_score = G * G / (H + self.lam)
                for j in range(B.shape[1]):
                    bj = B[rows, j]
                    gh = np.bincount(bj, weights=g[rows], minlength=nb[j])
                    hh = np.bincount(bj, weights=h[rows], minlength=nb[j])
                    GL, HL = np.cumsum(gh)[:-1], np.cumsum(hh)[:-1]
                    GR, HR = G - GL, H - HL
                    ok = (HL >= self.min_child_weight) & (HR >= self.min_child_weight)
                    if not ok.any():
                        continue
                    gain = np.where(ok, GL * GL / (HL + self.lam) + GR * GR / (HR + self.lam) - parent_score, -np.inf)
                    b = int(np.argmax(gain))
                    if gain[b] > best[0]:
                        best = (float(gain[b]), j, b)
                if best[1] < 0:
                    value[node] = float(-G / (H + self.lam) * self.lr)
                    continue
                _, j, b = best
                feat[node], thr_bin[node] = j, b
                go_left = B[rows, j] <= b
                ln, rn = new_node(), new_node()
                left[node], right[node] = ln, rn
                nxt += [(ln, rows[go_left]), (rn, rows[~go_left])]
            frontier = nxt
        for node, rows in frontier:
            G, H = g[rows].sum(), h[rows].sum()
            value[node] = float(-G / (H + self.lam) * self.lr)
        # Real-valued thresholds: bin b covers x ≤ edges[b].
        thr = [float(self.edges[f][t]) if f >= 0 and t < len(self.edges[f]) else float('inf')
               for f, t in zip(feat, thr_bin)]
        return {'feature': feat, 'bin': thr_bin, 'threshold': thr, 'left': left, 'right': right, 'value': value}

    @staticmethod
    def _predict_tree_binned(tree, B):
        out = np.zeros(B.shape[0])
        node = np.zeros(B.shape[0], dtype=np.int64)
        feat = np.array(tree['feature']); tb = np.array(tree['bin'])
        left = np.array(tree['left']); right = np.array(tree['right']); val = np.array(tree['value'])
        active = feat[node] >= 0
        while active.any():
            n = node[active]
            go = B[np.flatnonzero(active), feat[n]] <= tb[n]
            node[active] = np.where(go, left[n], right[n])
            active = feat[node] >= 0
        out[:] = val[node]
        return out

    # ── inference (raw feature values; no binning needed) ────────────────
    def decision_function(self, X):
        X = np.nan_to_num(np.asarray(X, float), nan=-1e30)
        F = np.full(X.shape[0], self.base)
        for tree in self.trees:
            feat = np.array(tree['feature']); thr = np.array(tree['threshold'])
            left = np.array(tree['left']); right = np.array(tree['right']); val = np.array(tree['value'])
            node = np.zeros(X.shape[0], dtype=np.int64)
            active = feat[node] >= 0
            while active.any():
                idx = np.flatnonzero(active)
                n = node[idx]
                go = X[idx, feat[n]] <= thr[n]
                node[idx] = np.where(go, left[n], right[n])
                active = feat[node] >= 0
            F += val[node]
        return F

    def predict_proba(self, X):
        return _sigmoid(self.decision_function(X))

    def feature_importance(self):
        imp = np.zeros(len(self.edges))
        for t in self.trees:
            for f in t['feature']:
                if f >= 0:
                    imp[f] += 1
        return (imp / imp.sum()).tolist() if imp.sum() else imp.tolist()

    # ── serialisation ───────────────────────────────────────────────────
    def to_dict(self):
        return {'kind': 'altis-gbdt', 'version': 1, 'base': self.base, 'feature_names': self.feature_names,
                'params': {'n_trees': self.n_trees, 'depth': self.depth, 'lr': self.lr,
                           'min_child_weight': self.min_child_weight, 'lam': self.lam,
                           'subsample': self.subsample, 'max_bins': self.max_bins},
                'trees': [{k: t[k] for k in ('feature', 'threshold', 'left', 'right', 'value')} for t in self.trees]}

    @classmethod
    def from_dict(cls, d):
        m = cls(**d.get('params', {}), feature_names=d.get('feature_names'))
        m.base = d['base']
        m.trees = [{**t, 'threshold': [float('inf') if v is None else v for v in t['threshold']]} for t in d['trees']]
        m.edges = [[] for _ in (d.get('feature_names') or [])]
        return m


def auc(y, p) -> float:
    """ROC AUC via the rank statistic (ties averaged)."""
    y = np.asarray(y, bool)
    p = np.asarray(p, float)
    n1, n0 = y.sum(), (~y).sum()
    if n1 == 0 or n0 == 0:
        return float('nan')
    order = np.argsort(p)
    ranks = np.empty(len(p))
    sp = p[order]
    i = 0
    r = np.arange(1, len(p) + 1, dtype=float)
    while i < len(p):
        j = i
        while j + 1 < len(p) and sp[j + 1] == sp[i]:
            j += 1
        r[i:j + 1] = (i + j + 2) / 2.0
        i = j + 1
    ranks[order] = r
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def dumps(model: GBDT) -> str:
    d = model.to_dict()
    for t in d['trees']:
        t['threshold'] = [None if v == float('inf') else round(v, 6) for v in t['threshold']]
        t['value'] = [round(v, 7) for v in t['value']]
    return json.dumps(d, separators=(',', ':'))
