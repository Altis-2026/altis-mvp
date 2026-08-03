"""
calibrated_confidence.py — replay a fitted calibrator at inference time.

WHY THIS EXISTS
The triage `confidence_score` (30-97) is decision confidence: how sure the
model is about the CALL it made. It is hand-tuned and it is not a probability.
A separate, defensible question a carrier actually asks is "what is the
probability this property flooded?" — and that number is only meaningful if it
has been calibrated against real ground truth.

validation/accuracy_check.py fits exactly that map (raw flood-evidence score ->
P(flooded)) against FEMA Individual Assistance data and writes it to
outputs/calibration_{event}.json. Until now nothing replayed it at inference
time, so the fitted calibrator was reported in the audit PDF but never applied
to a property. This module closes that gap.

DELIBERATELY NOT OVERWRITING confidence_score
P(flooded) and decision-confidence are different quantities and must stay
separate. A correctly-identified DRY property has HIGH decision confidence and
LOW flood probability; collapsing them into one number would make every
confident Remote-Deny look uncertain. So this adds `flood_probability` as its
own clearly-labelled field and leaves `confidence_score` alone.

HONESTY
- Returns None (never a fabricated number) when no calibration has been fitted.
- Carries provenance with every value: which event's ground truth it was fitted
  on, the method, and the held-out Brier score, so the UI can state where the
  number came from instead of showing a bare percentage.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pipeline.calibration import load_calibrator, raw_flood_score

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / 'outputs'

# Live analysis has no FEMA disaster of its own, so it borrows a fitted
# calibrator. Order of preference is configurable; the chosen event is always
# reported back as provenance rather than hidden.
DEFAULT_CALIBRATION_ORDER = tuple(
    e.strip() for e in os.getenv('CALIBRATION_EVENT_ORDER', 'harvey,ian').split(',')
    if e.strip()
)

# path -> (mtime, parsed) so a freshly generated calibration file is picked up
# without a server restart, and a missing file is not re-read every call.
_cache: dict = {}


def _calibration_path(event_id: str) -> Path:
    return OUTPUT_DIR / f"calibration_{event_id}.json"


def _load_blob(event_id: str) -> Optional[dict]:
    """Parse calibration_{event}.json, memoised on file mtime."""
    path = _calibration_path(event_id)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _cache.pop(str(path), None)
        return None

    cached = _cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        blob = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        print(f"  [calibration] could not read {path.name}: {e}")
        return None

    _cache[str(path)] = (mtime, blob)
    return blob


def get_calibration(event_id: Optional[str]) -> Optional[dict]:
    """
    Resolve which fitted calibration to use for an event.

    A pre-baked event uses its own if present. Live analysis (event_id None, or
    an event with no fitted calibration) falls back through
    DEFAULT_CALIBRATION_ORDER. Returns None when nothing has been fitted yet.
    """
    candidates = []
    if event_id:
        candidates.append(event_id)
    candidates.extend(e for e in DEFAULT_CALIBRATION_ORDER if e != event_id)

    for candidate in candidates:
        blob = _load_blob(candidate)
        if blob and blob.get('calibrator'):
            return {**blob, 'fitted_on_event': candidate,
                    'is_borrowed': candidate != event_id}
    return None


def calibration_provenance(blob: dict) -> dict:
    """Small, UI-safe description of where a probability came from."""
    hm = blob.get('holdout_metrics') or {}
    return {
        'fitted_on_event':   blob.get('fitted_on_event'),
        'is_borrowed':       blob.get('is_borrowed', False),
        'method':            hm.get('method') or blob.get('calibrator', {}).get('method'),
        'brier_score':       hm.get('brier_score'),
        'expected_calibration_error': hm.get('expected_calibration_error'),
        'n_labelled':        blob.get('n_total'),
        'label_source':      blob.get('label_source'),
        'label_resolution':  blob.get('label_resolution'),
    }


def _pct_to_fraction(pct_flooded) -> float:
    """Result rows carry pct_flooded as 0-100; raw_flood_score wants 0-1."""
    try:
        return max(0.0, min(float(pct_flooded or 0.0), 100.0)) / 100.0
    except (TypeError, ValueError):
        return 0.0


def attach_flood_probability(rows: list, event_id: Optional[str] = None) -> Optional[dict]:
    """
    Add a calibrated `flood_probability` (0-100, one decimal) to each analysed
    row in place. Rows without an impact_class (unanalysed) are skipped.

    Returns the provenance dict when a calibration was applied, else None — so
    the caller can say "not yet calibrated" rather than implying a validated
    number exists.
    """
    if not rows:
        return None

    blob = get_calibration(event_id)
    if not blob:
        return None

    try:
        calibrator = load_calibrator(blob['calibrator'])
    except (KeyError, ValueError) as e:
        print(f"  [calibration] unusable calibrator blob: {e}")
        return None

    analysed = [r for r in rows if isinstance(r, dict) and r.get('impact_class')]
    if not analysed:
        return None

    scores = [raw_flood_score(_pct_to_fraction(r.get('pct_flooded')),
                              r.get('max_depth_ft') or 0.0)
              for r in analysed]
    probs = calibrator.predict(scores)

    provenance = calibration_provenance(blob)
    for row, prob in zip(analysed, probs):
        row['flood_probability'] = round(float(prob) * 100.0, 1)
        row['flood_probability_source'] = provenance['fitted_on_event']
    return provenance
