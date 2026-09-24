"""
validation_metrics.py — Agreement between an Altis flood result and an
independent high-resolution reference water mask.

The reference (e.g. a 0.5 m Umbra / few-metre ICEYE scene) is far finer than
Sentinel-1's 10 m, so it is first aggregated to a wet FRACTION per property
footprint; a property counts as wet in the reference when that fraction
exceeds `wet_frac`. Against Altis's per-property flood calls this gives the
standard contingency statistics:

  hit rate (POD)     hits / (hits + misses)
  false alarm ratio  false_alarms / (hits + false_alarms)
  CSI                hits / (hits + misses + false_alarms)
  F1                 2·hits / (2·hits + misses + false_alarms)

plus the Wilson 95% interval on CSI so a small overlap is reported with the
uncertainty it deserves.

reference_water_mask() derives a water mask from high-resolution amplitude
(dB) with a speckle filter and an Otsu split — the same principle Altis
applies to Sentinel-1, run on independent data.
"""
from __future__ import annotations

import math

import numpy as np


def otsu_threshold(values: np.ndarray, bins: int = 256) -> float:
    v = values[np.isfinite(values)]
    if v.size == 0:
        raise ValueError('no valid pixels')
    hist, edges = np.histogram(v, bins=bins)
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mids) / np.maximum(w0, 1)
    m1 = (np.sum(hist * mids) - np.cumsum(hist * mids)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(mids[int(np.argmax(between))])


def reference_water_mask(amplitude_db: np.ndarray, speckle_px: int = 5) -> tuple[np.ndarray, float]:
    from scipy.ndimage import uniform_filter
    a = np.where(np.isfinite(amplitude_db), amplitude_db, np.nan)
    filled = np.where(np.isfinite(a), a, np.nanmedian(a))
    smooth = uniform_filter(filled, size=speckle_px)
    thr = otsu_threshold(smooth)
    return (smooth < thr) & np.isfinite(a), thr


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(max(0.0, c - h), 3), round(min(1.0, c + h), 3))


def contingency(altis_wet, ref_wet) -> dict:
    a = np.asarray(altis_wet, bool)
    r = np.asarray(ref_wet, bool)
    hits = int((a & r).sum())
    misses = int((~a & r).sum())
    fa = int((a & ~r).sum())
    cn = int((~a & ~r).sum())
    denom = hits + misses + fa
    return {
        'n': int(a.size), 'hits': hits, 'misses': misses, 'false_alarms': fa, 'correct_negatives': cn,
        'pod': None if hits + misses == 0 else round(hits / (hits + misses), 3),
        'far': None if hits + fa == 0 else round(fa / (hits + fa), 3),
        'csi': None if denom == 0 else round(hits / denom, 3),
        'csi_95ci': wilson(hits, denom),
        'f1': None if denom == 0 else round(2 * hits / (2 * hits + misses + fa), 3),
        'accuracy': None if a.size == 0 else round((hits + cn) / a.size, 3),
    }


def reference_fraction_at(ref_mask: np.ndarray, rows, cols, radius_px: int) -> np.ndarray:
    """Wet fraction of the reference within a square window around each point."""
    h, w = ref_mask.shape
    out = np.full(len(rows), np.nan)
    for i, (r, c) in enumerate(zip(rows, cols)):
        r, c = int(round(r)), int(round(c))
        r0, r1 = max(0, r - radius_px), min(h, r + radius_px + 1)
        c0, c1 = max(0, c - radius_px), min(w, c + radius_px + 1)
        if r1 > r0 and c1 > c0:
            out[i] = float(ref_mask[r0:r1, c0:c1].mean())
    return out


def agreement(altis_wet, ref_fraction, wet_frac: float = 0.2) -> dict:
    rf = np.asarray(ref_fraction, float)
    ok = np.isfinite(rf)
    res = contingency(np.asarray(altis_wet, bool)[ok], rf[ok] >= wet_frac)
    res['reference_wet_fraction_threshold'] = wet_frac
    return res
