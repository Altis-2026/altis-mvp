"""
06_train_susceptibility.py — Train and validate the pre-landfall susceptibility model.

    python pipeline/06_train_susceptibility.py [--chips N] [--per-chip K]

1. Sen1Floods11 hand-labelled chips (446 chips, 11 events) + their JRC
   permanent-water layers, read with pipeline/cogread.py.
2. Terrain features per chip from AWS Terrain Tiles, JRC occurrence tiles,
   and CHIRPS rainfall in the 10 days before each event's Sentinel-1 date.
3. Leave-one-event-out cross-validation → out-of-fold AUC / Brier per event,
   isotonic calibration fitted on the out-of-fold scores.
4. Final model on all chips → pipeline/models/susceptibility_v1.json.
5. Back-test on Altis's demo events (property points) → report.

Output report: outputs/susceptibility_report.json
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import requests  # noqa: E402

from backend import geodata  # noqa: E402
from pipeline.calibration import IsotonicCalibrator, brier_score, reliability_curve  # noqa: E402
from pipeline.cogread import CogReader, bytes_fetcher  # noqa: E402
from pipeline.gbdt import GBDT, auc, dumps  # noqa: E402
from pipeline.susceptibility import FEATURES, MODEL_PATH, feature_matrix, terrain_grids  # noqa: E402

GCS = 'https://storage.googleapis.com/sen1floods11/v1.1'
LIST = ('https://storage.googleapis.com/storage/v1/b/sen1floods11/o?prefix=v1.1/data/flood_events/'
        'HandLabeled/LabelHand/&maxResults=1000&fields=items(name)')
CACHE = geodata.CACHE_DIR / 'sen1floods11'
CACHE.mkdir(parents=True, exist_ok=True)


def fetch(url):
    key = url.rsplit('/', 1)[-1]
    p = CACHE / key
    if p.exists():
        return p.read_bytes()
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                p.write_bytes(r.content)
                return r.content
        except requests.RequestException:
            pass
        time.sleep(1 + attempt)
    raise RuntimeError(f'download failed: {url}')


def chip_samples(name, event_date, per_chip, rng_seed):
    """Features + labels for one chip, or None."""
    stem = name.rsplit('/', 1)[-1].replace('_LabelHand.tif', '')
    lab = CogReader(bytes_fetcher(fetch(f'{GCS}/data/flood_events/HandLabeled/LabelHand/{stem}_LabelHand.tif')))
    jrc = CogReader(bytes_fetcher(fetch(f'{GCS}/data/flood_events/HandLabeled/JRCWaterHand/{stem}_JRCWaterHand.tif')))
    L, (w0, n0, rx, ry) = lab.read_all()
    J, _ = jrc.read_all()
    if J.shape != L.shape:
        return None
    valid = (L >= 0) & (J != 1)                    # drop nodata and permanent water
    if valid.sum() < 200:
        return None
    rng = np.random.default_rng(rng_seed)
    idx = np.flatnonzero(valid.ravel())
    pick = rng.choice(idx, size=min(per_chip, idx.size), replace=False)
    r, c = np.divmod(pick, L.shape[1])
    lons = w0 + (c + 0.5) * rx
    lats = n0 - (r + 0.5) * ry
    y = (L.ravel()[pick] == 1).astype(float)
    bbox = [float(lons.min()), float(lats.min()), float(lons.max()), float(lats.max())]
    grids = terrain_grids(bbox)
    # Rainfall: CHIRPS for the 10 days up to the Sentinel-1 date, at the chip.
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    days = [event_date - timedelta(days=k) for k in range(9, -1, -1)]
    vals = []
    for d in days:
        g = geodata.chirps_day(d, [cx - 0.1, cy - 0.1, cx + 0.1, cy + 0.1])
        vals.append(float(np.nan_to_num(g.sample([cx], [cy], 'nearest')[0], nan=0.0)))
    vals = np.array(vals)
    r3 = float(np.convolve(vals, np.ones(3), 'valid').max())
    r7 = float(vals[-7:].sum())
    X = feature_matrix(grids, lons, lats, r3, r7)
    ok = np.isfinite(X).all(axis=1)
    return X[ok], y[ok], stem.split('_')[0], {'chip': stem, 'rain_3day_mm': r3, 'rain_7day_mm': r7,
                                             'flood_frac': float(y.mean())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chips', type=int, default=0, help='limit chips (0 = all)')
    ap.add_argument('--per-chip', type=int, default=600)
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args()
    t0 = time.time()
    meta = json.loads(fetch(f'{GCS}/Sen1Floods11_Metadata.geojson'))
    dates = {f['properties']['location']: datetime.strptime(f['properties']['s1_date'], '%Y/%m/%d').date()
             for f in meta['features']}
    names = [i['name'] for i in requests.get(LIST, timeout=60).json()['items']]
    if args.chips:
        rng = np.random.default_rng(0)
        names = list(rng.choice(names, size=min(args.chips, len(names)), replace=False))
    print(f'{len(names)} chips; events {sorted(dates)}')

    def job(k_name):
        k, name = k_name
        loc = name.rsplit('/', 1)[-1].split('_')[0]
        if loc not in dates:
            return None
        try:
            return chip_samples(name, dates[loc], args.per_chip, k)
        except Exception as ex:  # noqa: BLE001
            print(f'  skip {name.rsplit("/", 1)[-1]}: {type(ex).__name__}: {ex}')
            return None
    results = []
    with ThreadPoolExecutor(args.workers) as ex:
        for i, res in enumerate(ex.map(job, enumerate(names))):
            if res is not None:
                results.append(res)
            if (i + 1) % 40 == 0:
                print(f'  {i + 1}/{len(names)} chips processed ({time.time() - t0:.0f}s)')
    X = np.vstack([r[0] for r in results])
    y = np.concatenate([r[1] for r in results])
    groups = np.concatenate([[r[2]] * len(r[1]) for r in results])
    chips = [r[3] for r in results]
    print(f'samples {len(y)}  flood share {y.mean():.3f}  events {sorted(set(groups))}')

    params = dict(n_trees=250, depth=5, lr=0.06, min_child_weight=30.0, subsample=0.8)
    # ── Leave-one-event-out CV ─────────────────────────────────────────
    oof = np.full(len(y), np.nan)
    per_event = {}
    for ev in sorted(set(groups)):
        te = groups == ev
        if y[te].sum() < 20 or (1 - y[te]).sum() < 20:
            continue
        m = GBDT(**params, feature_names=FEATURES).fit(X[~te], y[~te])
        oof[te] = m.predict_proba(X[te])
        per_event[ev] = {'n': int(te.sum()), 'flood_share': round(float(y[te].mean()), 3),
                         'auc': round(auc(y[te], oof[te]), 3),
                         'brier': round(float(brier_score(oof[te], y[te])), 4)}
        print(f'  held-out {ev:10s} AUC {per_event[ev]["auc"]:.3f}  (n={te.sum()}, flood {y[te].mean():.2f})')
    ok = np.isfinite(oof)
    pooled_auc = auc(y[ok], oof[ok])
    cal = IsotonicCalibrator.fit(oof[ok], y[ok])
    # Thin the calibration knots so the model file stays small.
    xk, yk = np.array(cal.x_knots), np.array(cal.y_knots)
    if len(xk) > 200:
        sel = np.unique(np.linspace(0, len(xk) - 1, 200).astype(int))
        cal = IsotonicCalibrator(x_knots=xk[sel].tolist(), y_knots=yk[sel].tolist())
    cal_oof = cal.predict(oof[ok])
    rel = reliability_curve(cal_oof, y[ok], n_bins=10)
    # Terrain-only baseline: how much does rainfall add?
    base_auc = {}
    for f_i, f in enumerate(FEATURES):
        if f in ('hand_m', 'relelev_2km'):
            base_auc[f] = round(auc(y, -X[:, f_i]), 3)     # lower = wetter

    # ── Final model ─────────────────────────────────────────────────────
    final = GBDT(**params, feature_names=FEATURES).fit(X, y)
    r3 = np.array([c['rain_3day_mm'] for c in chips])
    r7 = np.array([c['rain_7day_mm'] for c in chips])
    ratio = float(np.median(r7[r3 > 5] / r3[r3 > 5])) if (r3 > 5).any() else 1.3
    blob = {'model': json.loads(dumps(final)), 'calibrator': {'method': 'isotonic', 'x_knots': cal.x_knots,
                                                              'y_knots': cal.y_knots},
            'features': FEATURES, 'rain7_over_rain3_median': round(ratio, 3),
            'training': {'dataset': 'Sen1Floods11 v1.1 hand-labelled chips (CC-BY 4.0)',
                         'chips': len(chips), 'samples': int(len(y)),
                         'events': sorted(set(groups)), 'permanent_water': 'excluded via JRCWaterHand',
                         'rain_product': 'CHIRPS v2.0 daily', 'terrain': 'AWS Terrain Tiles 30 m',
                         'params': params},
            'validation': {'scheme': 'leave-one-event-out', 'pooled_oof_auc': round(pooled_auc, 3),
                           'per_event': per_event,
                           'oof_brier_calibrated': round(float(brier_score(cal_oof, y[ok])), 4),
                           'reliability': rel, 'single_feature_auc': base_auc},
            'feature_importance': dict(zip(FEATURES, [round(v, 3) for v in final.feature_importance()])),
            'created_at': datetime.utcnow().isoformat() + 'Z'}
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(blob, separators=(',', ':')))
    print(f'✓ model → {MODEL_PATH} ({MODEL_PATH.stat().st_size / 1e3:.0f} kB); pooled OOF AUC {pooled_auc:.3f}; '
          f'{time.time() - t0:.0f}s')
    (ROOT / 'outputs' / 'susceptibility_report.json').write_text(json.dumps(
        {k: v for k, v in blob.items() if k not in ('model', 'calibrator')}, indent=1, default=str))


if __name__ == '__main__':
    main()
