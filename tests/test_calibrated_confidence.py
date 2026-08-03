"""
Tests for backend/calibrated_confidence.py — replaying a fitted calibrator at
inference time to produce a validated P(flooded) per property.

These tests deliberately build their own calibration file in a tmp directory
rather than depending on outputs/calibration_*.json: a committed fixture would
risk a synthetic calibrator being mistaken for real FEMA-validated output.
"""
import json

import numpy as np
import pytest

import calibration as cal
from backend import calibrated_confidence as cc


@pytest.fixture
def fitted_blob():
    """A genuinely fitted calibrator (synthetic labels), in the on-disk shape."""
    rng = np.random.default_rng(7)
    scores = rng.uniform(0, 1, size=400)
    labels = (rng.uniform(size=400) < scores).astype(int)  # P(flood) rises with score
    groups = np.repeat(np.arange(40), 10)                  # 40 "zips"
    result = cal.fit_and_evaluate(scores, labels, groups=groups, method="isotonic")
    result['event_id'] = 'testevent'
    result['label_source'] = 'synthetic'
    result['label_resolution'] = 'zip_code'
    return result


@pytest.fixture
def calib_dir(tmp_path, fitted_blob, monkeypatch):
    """Point the module at a tmp outputs/ containing calibration_harvey.json."""
    (tmp_path / "calibration_harvey.json").write_text(json.dumps(fitted_blob))
    monkeypatch.setattr(cc, 'OUTPUT_DIR', tmp_path)
    cc._cache.clear()
    return tmp_path


def _rows():
    return [
        {'property_id': 'A', 'impact_class': 'Dispatch',
         'pct_flooded': 95.0, 'max_depth_ft': 6.0},
        {'property_id': 'B', 'impact_class': 'Remote-Deny',
         'pct_flooded': 0.0, 'max_depth_ft': 0.0},
        {'property_id': 'C', 'impact_class': 'Review',
         'pct_flooded': 40.0, 'max_depth_ft': 1.5},
    ]


# ── Core behaviour ───────────────────────────────────────────────────────────

def test_attaches_probability_in_percent_range(calib_dir):
    rows = _rows()
    prov = cc.attach_flood_probability(rows, 'harvey')
    assert prov is not None
    for r in rows:
        assert 'flood_probability' in r
        assert 0.0 <= r['flood_probability'] <= 100.0


def test_probability_is_monotone_in_flood_evidence(calib_dir):
    """More water and more depth must never lower the calibrated probability."""
    rows = _rows()
    cc.attach_flood_probability(rows, 'harvey')
    by_id = {r['property_id']: r['flood_probability'] for r in rows}
    assert by_id['A'] >= by_id['C'] >= by_id['B']


def test_dry_property_gets_low_probability_but_keeps_its_own_confidence(calib_dir):
    """
    The point of keeping the two numbers separate: a confidently-dry property
    should get a LOW flood probability while its decision confidence is
    untouched.
    """
    rows = [{'property_id': 'B', 'impact_class': 'Remote-Deny', 'pct_flooded': 0.0,
             'max_depth_ft': 0.0, 'confidence_score': 93}]
    cc.attach_flood_probability(rows, 'harvey')
    assert rows[0]['flood_probability'] < 50.0
    assert rows[0]['confidence_score'] == 93  # never overwritten


def test_unanalysed_rows_are_skipped(calib_dir):
    rows = [{'property_id': 'X', 'impact_class': None},
            {'property_id': 'Y', 'impact_class': '', 'pct_flooded': 10}]
    assert cc.attach_flood_probability(rows, 'harvey') is None
    assert all('flood_probability' not in r for r in rows)


# ── Honest degradation ───────────────────────────────────────────────────────

def test_returns_none_when_no_calibration_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, 'OUTPUT_DIR', tmp_path)
    cc._cache.clear()
    rows = _rows()
    assert cc.attach_flood_probability(rows, 'harvey') is None
    # Never fabricate a number when nothing has been validated.
    assert all('flood_probability' not in r for r in rows)


def test_empty_rows_is_safe(calib_dir):
    assert cc.attach_flood_probability([], 'harvey') is None


def test_corrupt_calibration_file_degrades_quietly(tmp_path, monkeypatch):
    (tmp_path / "calibration_harvey.json").write_text("{not json")
    monkeypatch.setattr(cc, 'OUTPUT_DIR', tmp_path)
    cc._cache.clear()
    rows = _rows()
    assert cc.attach_flood_probability(rows, 'harvey') is None
    assert all('flood_probability' not in r for r in rows)


# ── Provenance / borrowing ───────────────────────────────────────────────────

def test_live_analysis_borrows_and_declares_it(calib_dir):
    """Live analysis (no event of its own) borrows harvey and says so."""
    rows = _rows()
    prov = cc.attach_flood_probability(rows, None)
    assert prov['fitted_on_event'] == 'harvey'
    assert prov['is_borrowed'] is True
    assert rows[0]['flood_probability_source'] == 'harvey'


def test_own_event_calibration_is_not_marked_borrowed(calib_dir):
    prov = cc.attach_flood_probability(_rows(), 'harvey')
    assert prov['is_borrowed'] is False


def test_provenance_carries_holdout_metrics(calib_dir):
    prov = cc.attach_flood_probability(_rows(), 'harvey')
    assert prov['brier_score'] is not None
    assert prov['method'] in ('isotonic', 'platt')
    assert prov['n_labelled'] == 400


def test_cache_invalidates_when_file_changes(calib_dir, fitted_blob):
    """A freshly generated calibration must be picked up without a restart."""
    first = cc.get_calibration('harvey')
    assert first is not None
    # Rewrite with a different marker and a newer mtime.
    import os
    import time
    path = calib_dir / "calibration_harvey.json"
    changed = {**fitted_blob, 'n_total': 999}
    path.write_text(json.dumps(changed))
    os.utime(path, (time.time() + 10, time.time() + 10))
    assert cc.get_calibration('harvey')['n_total'] == 999


# ── API integration: the endpoint actually surfaces the calibrated number ────

def test_events_endpoint_surfaces_flood_probability(calib_dir):
    """
    End-to-end proof of the wiring: with a calibration present,
    /api/events/{id}/properties returns a per-property flood_probability and
    reports the calibration provenance.
    """
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.database import load_event_data

    if load_event_data('harvey') is None:
        pytest.skip("harvey event data not present in outputs/")

    client = TestClient(app)
    r = client.get('/api/events/harvey/properties')
    assert r.status_code == 200
    body = r.json()

    assert body['calibration'] is not None
    assert body['calibration']['fitted_on_event'] == 'harvey'
    assert body['calibration']['is_borrowed'] is False

    analysed = [p for p in body['properties'] if p.get('impact_class')]
    assert analysed, "expected analysed properties in the harvey event"
    assert all(0.0 <= p['flood_probability'] <= 100.0 for p in analysed)


def test_events_endpoint_omits_probability_when_uncalibrated(tmp_path, monkeypatch):
    """Without a fitted calibration the API must not invent a number."""
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.database import load_event_data

    if load_event_data('harvey') is None:
        pytest.skip("harvey event data not present in outputs/")

    monkeypatch.setattr(cc, 'OUTPUT_DIR', tmp_path)
    cc._cache.clear()

    body = TestClient(app).get('/api/events/harvey/properties').json()
    assert body['calibration'] is None
    assert all('flood_probability' not in p for p in body['properties'])
