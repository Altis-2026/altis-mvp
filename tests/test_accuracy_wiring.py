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


def test_run_calibration_single_class_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, 'OUTPUT_DIR', tmp_path)
    altis = _altis_df()
    # Force all zips to the same (flooded) label by making every FEMA rate high.
    fema = _fema_agg()
    fema['fema_pct_flood'] = 90.0
    labeled = acc.derive_property_labels(altis, fema)
    assert acc.run_calibration('harvey', labeled) is None
