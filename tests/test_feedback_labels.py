"""
test_feedback_labels.py — Adjuster feedback → property-resolution ground truth.

Pure-function tests for the calibration merge (no DB, no network).
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd

# accuracy_check.py lives in validation/ and isn't a package module; load it.
ACC_PATH = Path(__file__).resolve().parent.parent / "validation" / "accuracy_check.py"
spec = importlib.util.spec_from_file_location("accuracy_check", ACC_PATH)
acc = importlib.util.module_from_spec(spec)
sys.modules["accuracy_check"] = acc
spec.loader.exec_module(acc)


def test_adjuster_label_corrected_class_wins():
    # Corrected to a positive class → flooded truth regardless of agree
    assert acc.adjuster_label(False, 'Dispatch', 'Remote-Deny') == 1
    assert acc.adjuster_label(True, 'Remote-Deny', 'Dispatch') == 0


def test_adjuster_label_agreement_keeps_original_positivity():
    assert acc.adjuster_label(True, '', 'Dispatch') == 1
    assert acc.adjuster_label(True, '', 'Remote-Approve') == 1
    assert acc.adjuster_label(True, '', 'Remote-Deny') == 0


def test_adjuster_label_disagreement_flips_positivity():
    assert acc.adjuster_label(False, '', 'Dispatch') == 0
    assert acc.adjuster_label(False, '', 'Remote-Deny') == 1


def test_adjuster_label_handles_string_bools():
    assert acc.adjuster_label('1', '', 'Dispatch') == 1
    assert acc.adjuster_label('0', '', 'Dispatch') == 0


def test_adjuster_label_none_when_no_signal():
    assert acc.adjuster_label(None, '', 'Dispatch') is None


def _labeled():
    return pd.DataFrame({
        'property_id': ['p1', 'p2', 'p3'],
        'impact_class': ['Dispatch', 'Remote-Deny', 'Review'],
        'flooded_truth': [1, 0, 0],
        'zip': ['77005', '77005', '77006'],
    })


def test_merge_no_feedback_is_noop():
    labeled = _labeled()
    merged, n = acc.merge_adjuster_labels(labeled, pd.DataFrame())
    assert n == 0
    assert merged['flooded_truth'].tolist() == [1, 0, 0]
    assert (merged['human_labeled'] == 0).all()


def test_merge_overrides_zip_label_with_human_label():
    labeled = _labeled()
    feedback = pd.DataFrame({
        'property_id': ['p2'],
        'agree': [False],            # disagrees with Remote-Deny → flooded
        'corrected_class': [''],
        'original_class': ['Remote-Deny'],
        'created_at': ['2026-06-24 10:00:00'],
    })
    merged, n = acc.merge_adjuster_labels(labeled, feedback)
    assert n == 1
    row = merged[merged['property_id'] == 'p2'].iloc[0]
    assert row['flooded_truth'] == 1       # overridden from 0
    assert row['human_labeled'] == 1
    # untouched rows keep zip truth
    assert merged[merged['property_id'] == 'p1'].iloc[0]['human_labeled'] == 0


def test_merge_uses_latest_verdict_per_property():
    labeled = _labeled()
    feedback = pd.DataFrame({
        'property_id': ['p1', 'p1'],
        'agree': [True, False],            # latest disagrees with Dispatch → dry
        'corrected_class': ['', ''],
        'original_class': ['Dispatch', 'Dispatch'],
        'created_at': ['2026-06-24 09:00:00', '2026-06-24 11:00:00'],
    })
    merged, n = acc.merge_adjuster_labels(labeled, feedback)
    assert n == 1
    assert merged[merged['property_id'] == 'p1'].iloc[0]['flooded_truth'] == 0


def test_merge_ignores_feedback_for_unknown_property():
    labeled = _labeled()
    feedback = pd.DataFrame({
        'property_id': ['ghost'],
        'agree': [True], 'corrected_class': ['Dispatch'],
        'original_class': ['Dispatch'], 'created_at': ['2026-06-24 10:00:00'],
    })
    merged, n = acc.merge_adjuster_labels(labeled, feedback)
    assert n == 0


def test_merge_does_not_mutate_input():
    labeled = _labeled()
    feedback = pd.DataFrame({
        'property_id': ['p2'], 'agree': [False], 'corrected_class': [''],
        'original_class': ['Remote-Deny'], 'created_at': ['2026-06-24 10:00:00'],
    })
    acc.merge_adjuster_labels(labeled, feedback)
    assert 'human_labeled' not in labeled.columns
    assert labeled[labeled['property_id'] == 'p2'].iloc[0]['flooded_truth'] == 0
