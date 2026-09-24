"""
susceptibility.py — Pre-landfall flood susceptibility (Phase F).

Question: before any satellite has looked, which properties in the book are
most likely to go under if this storm delivers X mm of rain?

Model: gradient-boosted trees (pipeline/gbdt.py) on terrain + rainfall
features, trained on Sen1Floods11 (Bonafilia et al. 2020, CC-BY 4.0) — 446
hand-labelled Sentinel-1 flood chips from 11 flood events on five
continents — with permanent water (JRC) excluded so the label is *flood*
water, not rivers and lakes. Validated leave-one-event-out, so every quoted
skill number comes from floods the model never saw, and back-tested on
Altis's own demo events.

Features (identical code at training and inference time):
  hand_m          height above nearest drainage (1 km² streams)
  slope_deg       terrain slope
  log_acc_km2     log10 upstream contributing area
  dist_stream_m   distance to the nearest drainage line
  relelev_500m    elevation above the local minimum within ~500 m
  relelev_2km     … within ~2 km (floodplain position)
  jrc_occ_pct     historical surface-water occurrence (1984–2021)
  rain_3day_mm    heaviest 3-day rainfall (CHIRPS)
  rain_7day_mm    7-day rainfall total (CHIRPS)

Output is the probability that a location floods GIVEN the rainfall
scenario — a ranking and planning signal. It is not a depth, not a claim
probability, and the training chips come from places where floods occurred,
so absolute probabilities are conditional on an event of that size.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

FEATURES = ['hand_m', 'slope_deg', 'log_acc_km2', 'dist_stream_m', 'relelev_500m',
            'relelev_2km', 'jrc_occ_pct', 'rain_3day_mm', 'rain_7day_mm']
MODEL_PATH = Path(__file__).resolve().parent / 'models' / 'susceptibility_v1.json'
SCENARIOS_MM = [25, 50, 100, 150, 200, 300, 400, 600]


def terrain_grids(bbox, session=None, res_m: float = 30.0, pad_deg: float = 0.04) -> dict:
    """Terrain feature rasters for bbox=[w,s,e,n] (network via backend.geodata)."""
    from scipy.ndimage import distance_transform_edt
    from backend import geodata
    from pipeline.terrain import Grid, hand, relative_elevation, slope_deg
    w, s, e, n = bbox
    dem = geodata.dem_at_resolution([w - pad_deg, s - pad_deg, e + pad_deg, n + pad_deg], res_m, session)
    cell = dem.cell_m
    hnd, acc, stream = hand(dem.data, cell, 1.0)
    dist = distance_transform_edt(~stream, sampling=(cell[1], cell[0]))
    r500 = max(1, int(round(500 / cell[0])))
    r2k = max(1, int(round(2000 / cell[0])))
    mk = lambda a: Grid(np.asarray(a, float), dem.west, dem.north, dem.dx, dem.dy)
    grids = {'hand_m': mk(hnd), 'slope_deg': mk(slope_deg(dem.data, cell)),
             'log_acc_km2': mk(np.log10(np.maximum(acc, 1e-4))), 'dist_stream_m': mk(dist),
             'relelev_500m': mk(relative_elevation(dem.data, r500)),
             'relelev_2km': mk(relative_elevation(dem.data, r2k))}
    try:
        grids['jrc_occ_pct'] = geodata.jrc_occurrence_grid([w - 0.005, s - 0.005, e + 0.005, n + 0.005], session)
    except Exception:  # noqa: BLE001 — occurrence unavailable → treated as 0
        grids['jrc_occ_pct'] = None
    return grids


def feature_matrix(grids: dict, lons, lats, rain3, rain7) -> np.ndarray:
    lons, lats = np.asarray(lons, float), np.asarray(lats, float)
    cols = []
    for f in FEATURES[:6]:
        cols.append(grids[f].sample(lons, lats))
    j = grids.get('jrc_occ_pct')
    cols.append(np.nan_to_num(j.sample(lons, lats, 'nearest'), nan=0.0) if j is not None else np.zeros(len(lons)))
    cols.append(np.broadcast_to(np.asarray(rain3, float), lons.shape).astype(float))
    cols.append(np.broadcast_to(np.asarray(rain7, float), lons.shape).astype(float))
    return np.column_stack(cols)


class SusceptibilityModel:
    def __init__(self, blob: dict):
        from pipeline.gbdt import GBDT
        self.blob = blob
        self.gbdt = GBDT.from_dict(blob['model'])
        cal = blob.get('calibrator')
        self.cal = None
        if cal:
            from pipeline.calibration import load_calibrator
            self.cal = load_calibrator(cal)
        self.rain7_ratio = float(blob.get('rain7_over_rain3_median', 1.3))

    @classmethod
    def load(cls, path: Path = MODEL_PATH):
        return cls(json.loads(Path(path).read_text()))

    def predict(self, X) -> np.ndarray:
        p = self.gbdt.predict_proba(X)
        return np.asarray(self.cal.predict(p), float) if self.cal is not None else p

    def scenario_curves(self, X_terrain: np.ndarray, scenarios=SCENARIOS_MM) -> np.ndarray:
        """
        P(flood) for each property × 3-day-rain scenario. The 7-day total is
        set from the training data's median 7-day/3-day ratio. Curves are made
        monotone non-decreasing in rainfall (more rain never makes a place
        safer), which the tree ensemble does not guarantee on its own.
        """
        out = np.zeros((X_terrain.shape[0], len(scenarios)))
        for k, mm in enumerate(scenarios):
            X = X_terrain.copy()
            X[:, 7] = mm
            X[:, 8] = mm * self.rain7_ratio
            out[:, k] = self.predict(X)
        return np.maximum.accumulate(out, axis=1)


def interp_curve(curve, scenarios, mm):
    return float(np.interp(mm, scenarios, curve))


def risk_band(p: float) -> str:
    if p >= 0.6:
        return 'very high'
    if p >= 0.35:
        return 'high'
    if p >= 0.15:
        return 'elevated'
    return 'low'
