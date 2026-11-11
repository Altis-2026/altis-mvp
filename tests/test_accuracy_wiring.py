"""
Tests for the FEMA-label + calibration wiring in validation/accuracy_check.py.

The live FEMA fetch needs network and is exercised where the egress proxy
allows it; these tests cover the pure label-derivation, precision/recall, and
calibration-persistence logic with synthetic data (no network).
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "accuracy_check", BASE / "validation" / "accuracy_check.py")
acc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acc)


def _altis_df(n_zips=30, per_zip=20, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for z in range(n_zips):
        zipcode = f"{77000 + z:05d}"
        flooded_zip = z % 2 == 0
        for i in range(per_zip):
            # Flooded zips tend to higher coverage/depth, but with noise.
            base = 0.5 if flooded_zip else 0.05
            pct = float(np.clip(rng.normal(base, 0.15), 0, 1)) * 100  # final-CSV scale
            depth = max(0.0, rng.normal(2.5 if flooded_zip else 0.2, 0.8))
            cls = ('Dispatch' if depth > 3 else
                   'Remote-Approve' if pct > 20 else
                   'Remote-Deny' if pct < 5 else 'Review')
            rows.append({'property_id': f"P-{z}-{i}", 'zip': zipcode,
                         'pct_flooded': round(pct, 1), 'max_depth_ft': round(depth, 2),
                         'impact_class': cls})
    return pd.DataFrame(rows)


def _fema_agg(n_zips=30):
    # Even zips ~80% flood-damage rate, odd zips ~10%.
    rows = []
    for z in range(n_zips):
        rows.append({'zip': f"{77000 + z:05d}",
                     'fema_pct_flood': 80.0 if z % 2 == 0 else 10.0})
    return pd.DataFrame(rows)


def test_derive_property_labels_basic():
    altis, fema = _altis_df(), _fema_agg()
    labeled = acc.derive_property_labels(altis, fema)
    assert len(labeled) == len(altis)
    assert set(labeled['flooded_truth'].unique()) == {0, 1}
    # Even zip -> rate 0.8 >= 0.5 -> flooded; odd zip -> 0.1 -> not.
    even = labeled[labeled['zip'] == "77000"]
    odd = labeled[labeled['zip'] == "77001"]
    assert (even['flooded_truth'] == 1).all()
    assert (odd['flooded_truth'] == 0).all()
    assert (labeled['raw_flood_score'].between(0, 1)).all()


def test_derive_property_labels_drops_zips_without_fema():
    altis = _altis_df()
    altis.loc[altis['zip'] == "77000", 'zip'] = "99999"  # zip not in FEMA
    labeled = acc.derive_property_labels(altis, _fema_agg())
    assert "99999" not in set(labeled['zip'])


def test_precision_recall_by_category_structure():
    labeled = acc.derive_property_labels(_altis_df(), _fema_agg())
    pr = acc.precision_recall_by_category(labeled)
    assert set(pr['by_category']) == {'Dispatch', 'Remote-Approve',
                                      'Remote-Deny', 'Review'}
    assert pr['overall'] is not None
    assert 'precision' in pr['overall'] and 'recall' in pr['overall']


def test_run_calibration_writes_files_and_holdout(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, 'OUTPUT_DIR', tmp_path)
    labeled = acc.derive_property_labels(_altis_df(), _fema_agg())
    res = acc.run_calibration('harvey', labeled)
    assert res is not None
    assert res['split_kind'] == 'grouped_by_zip'
    assert res['holdout_metrics'] is not None
    # Files persisted
    assert (tmp_path / "calibration_harvey.json").exists()
    assert (tmp_path / "harvey_labels.csv").exists()
    blob = json.loads((tmp_path / "calibration_harvey.json").read_text())
    assert 'triage_precision_recall' in blob
    assert blob['label_resolution'] == 'zip_code'


def test_run_calibration_refuses_degenerate_constant_scores(tmp_path, monkeypatch):
    """
    The Harvey trap: when the detector finds nothing, every property gets the
    same raw_flood_score, and any calibrator fitted on it is constant. Its
    Brier score is then p(1-p) — the base rate variance — which looks like a
    good accuracy number but measures only label prevalence.

    Calibration must refuse, and must NOT persist a calibrator file, since
    backend/calibrated_confidence.py would otherwise replay it at inference
    and show a meaningless flood_probability on every property.
    """
    monkeypatch.setattr(acc, 'OUTPUT_DIR', tmp_path)
    labeled = acc.derive_property_labels(_altis_df(), _fema_agg())
    labeled['raw_flood_score'] = 0.0          # what a zero-detection event gives

    res = acc.run_calibration('harvey', labeled)

    assert res is not None
    assert res['degenerate'] is True
    assert res['holdout_metrics'] is None
    assert res['distinct_scores'] == 1
    assert 'DETECTION_LIMITS' in res['warning']
    # Nothing persisted — a degenerate calibrator must never reach inference.
    assert not (tmp_path / "calibration_harvey.json").exists()


def test_degenerate_calibration_report_states_the_arithmetic(tmp_path, monkeypatch):
    """The report must show why the number is meaningless, not just omit it."""
    monkeypatch.setattr(acc, 'OUTPUT_DIR', tmp_path)
    labeled = acc.derive_property_labels(_altis_df(), _fema_agg())
    labeled['raw_flood_score'] = 0.0
    res = acc.run_calibration('harvey', labeled)

    lines = "\n".join(acc._calibration_report_lines(res))
    assert 'No calibration was fitted' in lines
    assert 'by construction' in lines
    assert 'Brier' in lines


def test_run_calibration_single_class_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, 'OUTPUT_DIR', tmp_path)
    altis = _altis_df()
    # Force all zips to the same (flooded) label by making every FEMA rate high.
    fema = _fema_agg()
    fema['fema_pct_flood'] = 90.0
    labeled = acc.derive_property_labels(altis, fema)
    assert acc.run_calibration('harvey', labeled) is None


# ── Phase 4b: dual-pol in the coverage term ─────────────────────────────────

def _dpol_df(dpol_values, available):
    """
    Minimal frame for the coverage term: no binary detection anywhere, so any
    resulting score has to have come from the dual-pol column.
    """
    return pd.DataFrame({
        'property_id': [f"P-{i}" for i in range(len(dpol_values))],
        'zip': ["77000"] * len(dpol_values),
        'pct_flooded': [0.0] * len(dpol_values),
        'max_depth_ft': [0.0] * len(dpol_values),
        'impact_class': ['Remote-Deny'] * len(dpol_values),
        'dpol_water': dpol_values,
        'dpol_available': available,
    })


def test_dpol_score_reaches_the_coverage_term():
    """A measured dual-pol score must lift the raw score off the floor."""
    df = acc.derive_property_labels(
        _dpol_df([0.0, 0.4, 0.9], [1, 1, 1]),
        pd.DataFrame([{'zip': "77000", 'fema_pct_flood': 80.0}]))
    scores = df.sort_values('property_id')['raw_flood_score'].tolist()
    assert scores[0] == pytest.approx(0.0)
    assert scores[1] > scores[0]
    assert scores[2] > scores[1]


def test_unavailable_dpol_is_not_read_as_dry():
    """
    The abstention contract. With dpol_available clear, the score was never
    measured — folding its 0 in as evidence would be fabricating a dry reading.
    Both properties must land on the same floor as a run with no dual-pol
    column at all, rather than the 0.9 one being credited.
    """
    df = acc.derive_property_labels(
        _dpol_df([0.0, 0.9], [0, 0]),
        pd.DataFrame([{'zip': "77000", 'fema_pct_flood': 80.0}]))
    assert (df['raw_flood_score'] == 0.0).all()


def test_dpol_never_lowers_an_existing_detection():
    """
    Coverage is a max(), so a property the binary mask flagged cannot be
    dragged down by a low or abstaining dual-pol score.
    """
    fema = pd.DataFrame([{'zip': "77000", 'fema_pct_flood': 80.0}])
    df = _dpol_df([0.0, 0.0], [0, 1])
    df['pct_flooded'] = [90.0, 90.0]
    df['max_depth_ft'] = [3.0, 3.0]

    # The same portfolio scored with no dual-pol column at all is the
    # reference: adding an abstaining or zero dual-pol reading must not move it.
    reference = acc.derive_property_labels(
        df.drop(columns=['dpol_water', 'dpol_available']), fema)
    out = acc.derive_property_labels(df, fema)

    assert (out['raw_flood_score'] > 0).all()
    assert out['raw_flood_score'].tolist() == pytest.approx(
        reference['raw_flood_score'].tolist())
