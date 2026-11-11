#!/usr/bin/env python3
"""
s1f11_benchmark.py — Honest flood-EXTENT accuracy on independent hand labels.

DATASET
  Sen1Floods11 (Bonafilia et al., CVPR-W 2020), hand-labelled subset: 446
  Sentinel-1 chips (512 x 512 px, 10 m, VV + VH in dB) from 11 flood events
  across 6 continents, each labelled water / not water / no data by analysts.
  Free: gs://sen1floods11 (public bucket). Official splits: train 252,
  valid 89, test 90, and Bolivia (15 chips) as a never-seen-country hold-out.

WHY THIS MATTERS
  Every other accuracy number in this project is either zip-level (NFIP) or
  recall-only (high water marks have no dry points). These labels have BOTH
  classes per pixel, so they measure precision, recall and IoU of the
  water map directly. They do not measure per-house damage.

LIMIT
  Sen1Floods11 has a single post-event image per chip, no pre-event
  baseline, so this tests single-image water detection: the fallback our
  pipeline uses when no baseline exists, and the input every change-detection
  method depends on. It does not test the multi-temporal change logic.

METHODS COMPARED (all on identical pixels)
  fixed_vv      VV < -15 dB, a common textbook rule
  otsu_vv       per-chip Otsu threshold on VV (what our pipeline's guarded Otsu does)
  dualpol       VV < t1 and VH < t2, thresholds chosen on the train split
  learned       LightGBM pixel classifier on VV, VH, ratio and 3/7/15 px
                neighbourhood statistics; trained on train, decision threshold
                chosen on valid, scored ONCE on test and on Bolivia.

Usage: python validation/s1f11_benchmark.py --data <dir with S1Hand/ LabelHand/ splits/>
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"


def load_chip(data, name_s1, name_lab):
    import rasterio
    with rasterio.open(data / "S1Hand" / name_s1) as f:
        x = f.read().astype(np.float32)
    with rasterio.open(data / "LabelHand" / name_lab) as f:
        y = f.read(1).astype(np.int16)
    return x, y


def otsu(v, bins=256):
    v = v[np.isfinite(v)]
    if v.size < 100:
        return -15.0
    hist, edges = np.histogram(v, bins=bins)
    c = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * c) / np.maximum(w0, 1)
    m1 = (np.sum(hist * c) - np.cumsum(hist * c)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(c[np.argmax(between)])


def box_stats(a, k):
    """Mean and std over a k x k window, NaN-aware, via integral images."""
    from scipy.ndimage import uniform_filter
    valid = np.isfinite(a).astype(np.float32)
    z = np.where(valid > 0, a, 0).astype(np.float64)
    n = uniform_filter(valid, k, mode="constant")
    s = uniform_filter(z, k, mode="constant")
    s2 = uniform_filter(z * z, k, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        m = s / n
        sd = np.sqrt(np.maximum(s2 / n - m * m, 0))
    return m.astype(np.float32), sd.astype(np.float32)


FEATURES = ["vv", "vh", "vv_minus_vh"] + [f"{b}_{s}{k}" for k in (3, 7, 15)
                                          for b in ("vv", "vh") for s in ("mean", "std")] + \
           ["chip_vv_p10", "chip_vv_p50", "chip_vh_p50", "vv_minus_chip_median"]


def features(x):
    vv, vh = x[0], x[1]
    cols = [vv, vh, vv - vh]
    for k in (3, 7, 15):
        for b in (vv, vh):
            m, sd = box_stats(b, k)
            cols += [m, sd]
    p10 = np.nanpercentile(vv, 10) if np.isfinite(vv).any() else np.nan
    p50 = np.nanmedian(vv) if np.isfinite(vv).any() else np.nan
    h50 = np.nanmedian(vh) if np.isfinite(vh).any() else np.nan
    cols += [np.full_like(vv, p10), np.full_like(vv, p50), np.full_like(vv, h50), vv - p50]
    return np.stack(cols, axis=-1)


def scores(pred, y):
    m = y >= 0
    p, t = pred[m].astype(bool), (y[m] == 1)
    tp = int(np.sum(p & t)); fp = int(np.sum(p & ~t)); fn = int(np.sum(~p & t)); tn = int(np.sum(~p & ~t))
    return tp, fp, fn, tn


def summarize(counts_per_chip, rng, n_boot=1000):
    c = np.array(counts_per_chip, dtype=np.float64)
    def metr(cc):
        tp, fp, fn, tn = cc.sum(0)
        iou = tp / max(tp + fp + fn, 1)
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        return iou, prec, rec, f1
    iou, prec, rec, f1 = metr(c)
    boots = np.array([metr(c[rng.integers(0, len(c), len(c))]) for _ in range(n_boot)])
    lo, hi = np.percentile(boots, [2.5, 97.5], axis=0)
    r = lambda v: round(float(v), 4)  # noqa: E731
    return {"n_chips": len(c), "iou": r(iou), "precision": r(prec), "recall": r(rec), "f1": r(f1),
            "iou_ci95": [r(lo[0]), r(hi[0])], "precision_ci95": [r(lo[1]), r(hi[1])],
            "recall_ci95": [r(lo[2]), r(hi[2])]}


def read_split(data, name):
    rows = []
    for line in (data / "splits" / f"{name}.csv").read_text().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[0].endswith(".tif"):
            rows.append(parts)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--pixels-per-chip", type=int, default=4000)
    a = ap.parse_args()
    data = Path(a.data)
    rng = np.random.default_rng(0)
    t0 = time.time()

    splits = {k: read_split(data, f"flood_{k}_data") for k in ("train", "valid", "test", "bolivia")}
    cache = {}

    def chip(row):
        if row[0] not in cache:
            cache[row[0]] = load_chip(data, *row)
        return cache[row[0]]

    # ── Train-split sampling for the learned model and the dual-pol grid ──
    Xs, ys = [], []
    for row in splits["train"]:
        x, y = chip(row)
        F = features(x)
        ok = (y >= 0) & np.all(np.isfinite(F), axis=-1)
        idx = np.flatnonzero(ok.ravel())
        if idx.size == 0:
            continue
        take = rng.choice(idx, size=min(a.pixels_per_chip, idx.size), replace=False)
        Xs.append(F.reshape(-1, F.shape[-1])[take]); ys.append((y.ravel()[take] == 1).astype(np.int8))
    X = np.concatenate(Xs); Y = np.concatenate(ys)
    print(f"train pixels {len(Y):,} (water {Y.mean():.1%}) in {time.time() - t0:.0f}s", flush=True)

    # Dual-pol thresholds: grid search on train pixels for best IoU.
    best = (-1, None)
    for t1 in np.arange(-22, -9, 0.5):
        for t2 in np.arange(-30, -16, 0.5):
            p = (X[:, 0] < t1) & (X[:, 1] < t2)
            tp = np.sum(p & (Y == 1)); fp = np.sum(p & (Y == 0)); fn = np.sum(~p & (Y == 1))
            iou = tp / max(tp + fp + fn, 1)
            if iou > best[0]:
                best = (iou, (float(t1), float(t2)))
    t1, t2 = best[1]
    print(f"dual-pol thresholds from train: VV<{t1} dB, VH<{t2} dB", flush=True)

    import lightgbm as lgb
    model = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=63,
                               subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                               min_child_samples=50, verbose=-1, random_state=0)
    model.fit(X, Y)

    def predict_learned(x):
        F = features(x)
        flat = F.reshape(-1, F.shape[-1])
        prob = np.zeros(flat.shape[0], np.float32)
        ok = np.all(np.isfinite(flat), axis=1)
        if ok.any():
            prob[ok] = model.predict_proba(flat[ok])[:, 1]
        return prob.reshape(x.shape[1:])

    # Decision threshold for the learned model: chosen on VALID only.
    vprobs = [(predict_learned(chip(r)[0]), chip(r)[1]) for r in splits["valid"]]
    best_thr = (-1, 0.5)
    for thr in np.arange(0.2, 0.81, 0.05):
        c = np.array([scores(p >= thr, y) for p, y in vprobs], float).sum(0)
        iou = c[0] / max(c[0] + c[1] + c[2], 1)
        if iou > best_thr[0]:
            best_thr = (iou, float(thr))
    thr = best_thr[1]
    print(f"learned decision threshold from valid: {thr:.2f}", flush=True)

    methods = {
        "fixed_vv": lambda x: x[0] < -15.0,
        "otsu_vv": lambda x: x[0] < otsu(x[0]),
        "dualpol": lambda x: (x[0] < t1) & (x[1] < t2),
        "learned": lambda x: predict_learned(x) >= thr,
    }
    results = {}
    for split in ("test", "bolivia"):
        per = {m: [] for m in methods}
        per_country = {}
        for row in splits[split]:
            x, y = chip(row)
            country = row[0].split("_")[0]
            for m, f in methods.items():
                pred = np.nan_to_num(f(x).astype(float), nan=0) > 0
                pred &= np.isfinite(x[0])
                c = scores(pred, y)
                per[m].append(c)
                per_country.setdefault(country, {}).setdefault(m, []).append(c)
        results[split] = {m: summarize(v, rng) for m, v in per.items()}
        results[split + "_by_country"] = {
            ctry: {m: summarize(v, rng, n_boot=200)["iou"] for m, v in d.items()}
            for ctry, d in per_country.items()}
        for m in methods:
            s = results[split][m]
            print(f"{split:8s} {m:9s} IoU {s['iou']:.3f} {s['iou_ci95']}  P {s['precision']:.3f}  R {s['recall']:.3f}", flush=True)

    imp = sorted(zip(FEATURES, model.booster_.feature_importance("gain")), key=lambda t: -t[1])
    out = {
        "dataset": "Sen1Floods11 hand-labelled (Bonafilia et al. 2020), official splits",
        "task": "single-image water extent (no pre-event baseline)",
        "unit": "pixel metrics pooled over chips; CIs bootstrap over chips",
        "dualpol_thresholds_db": {"vv": t1, "vh": t2, "chosen_on": "train"},
        "learned": {"model": "LightGBM", "features": FEATURES, "decision_threshold": thr,
                    "threshold_chosen_on": "valid", "train_pixels": int(len(Y)),
                    "top_features": [[f, round(float(g), 1)] for f, g in imp[:8]]},
        "results": results,
        "caveats": [
            "Tests single-image detection only; the pipeline's multi-temporal change logic needs pre-event imagery this dataset lacks.",
            "Water includes permanent water bodies, as in the dataset's labels.",
            "Chips are mostly rural/agricultural floodplains; dense urban flooding is under-represented.",
            "Bolivia is a never-seen country: the fairest test of generalisation.",
        ],
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "s1f11_benchmark.json").write_text(json.dumps(out, indent=1))
    import joblib
    (OUT / "models").mkdir(exist_ok=True)
    joblib.dump({"model": model, "features": FEATURES, "threshold": thr},
                OUT / "models" / "s1_water_lgbm.joblib")
    print(f"wrote outputs/s1f11_benchmark.json in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
