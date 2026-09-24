"""
susceptibility_service.py — Score properties with the pre-landfall model.

score_properties() builds terrain features for the properties' area (free
data via backend.geodata), evaluates the trained model at every rainfall
scenario, and — when a rainfall figure is supplied (a forecast, or the
observed event rain for a hindcast) — reads each property's probability off
its own curve, so the slider in the UI and the point estimate always agree.
"""
from __future__ import annotations

import numpy as np

from pipeline.susceptibility import (FEATURES, MODEL_PATH, SCENARIOS_MM, SusceptibilityModel,
                                     feature_matrix, interp_curve, risk_band, terrain_grids)

_model = None


def model():
    global _model
    if _model is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError('susceptibility model not trained (run pipeline/06_train_susceptibility.py)')
        _model = SusceptibilityModel.load()
    return _model


def model_card() -> dict:
    b = model().blob
    return {'features': b.get('features'), 'training': b.get('training'), 'validation': b.get('validation'),
            'feature_importance': b.get('feature_importance'), 'created_at': b.get('created_at'),
            'scenarios_mm': SCENARIOS_MM,
            'interpretation': 'Probability a location floods given a 3-day rainfall of the chosen size, '
                              'learned from 11 real flood events. A ranking and planning signal — not a '
                              'depth, not a claim probability.'}


def score_properties(props: list, rain_3day_mm: float | None = None, session=None) -> dict:
    pts = [p for p in props if p.get('latitude') is not None and p.get('longitude') is not None]
    if not pts:
        raise ValueError('no geocoded properties')
    lons = np.array([float(p['longitude']) for p in pts])
    lats = np.array([float(p['latitude']) for p in pts])
    bbox = [lons.min() - 0.01, lats.min() - 0.01, lons.max() + 0.01, lats.max() + 0.01]
    grids = terrain_grids(bbox, session)
    X = feature_matrix(grids, lons, lats, 0.0, 0.0)
    X = np.where(np.isfinite(X), X, np.nanmedian(np.where(np.isfinite(X), X, np.nan), axis=0))
    curves = model().scenario_curves(X)
    out = {}
    for i, p in enumerate(pts):
        c = [round(float(v), 4) for v in curves[i]]
        prob = interp_curve(curves[i], SCENARIOS_MM, rain_3day_mm) if rain_3day_mm is not None else None
        out[str(p['property_id'])] = {
            'curve': c,
            'p': None if prob is None else round(prob, 4),
            'band': None if prob is None else risk_band(prob),
            'features': {f: (None if not np.isfinite(X[i, k]) else round(float(X[i, k]), 2))
                         for k, f in enumerate(FEATURES[:7])},
        }
    return {'scenarios_mm': SCENARIOS_MM, 'rain_3day_mm': rain_3day_mm, 'properties': out,
            'count': len(out)}
