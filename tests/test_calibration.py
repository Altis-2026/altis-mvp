"""Tests for pipeline/calibration.py — the calibrated-probability core."""
import numpy as np
import pytest

import calibration as cal


# ── Isotonic / PAV ─────────────────────────────────────────────────────────────

def test_pav_recovers_known_monotone_pool():
    # Classic PAV example: [1,2,2,1,3] with unit weights pools the 2,1 -> 1.5,1.5
    y = np.array([1.0, 2.0, 2.0, 1.0, 3.0])
    w = np.ones_like(y)
    fitted = cal._pav(y, w)
    # Must be non-decreasing
    assert np.all(np.diff(fitted) >= -1e-9)
    # The middle violation (2,2,1) averages to 5/3 across those three
    np.testing.assert_allclose(fitted, [1.0, 5 / 3, 5 / 3, 5 / 3, 3.0], rtol=1e-6)


def test_isotonic_is_monotone_and_in_unit_range():
    rng = np.random.default_rng(0)
    scores = rng.uniform(0, 100, size=500)
    # True probability rises with score; labels are noisy draws
    p_true = 1 / (1 + np.exp(-(scores - 50) / 10))
    labels = (rng.uniform(size=500) < p_true).astype(float)
    c = cal.IsotonicCalibrator.fit(scores, labels)
    grid = np.linspace(scores.min(), scores.max(), 50)
    preds = c.predict(grid)
    assert np.all(preds >= 0.0) and np.all(preds <= 1.0)
    assert np.all(np.diff(preds) >= -1e-9)  # monotone non-decreasing


def test_isotonic_perfectly_separable():
    scores = np.array([1, 2, 3, 4, 10, 11, 12, 13], dtype=float)
    labels = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=float)
    c = cal.IsotonicCalibrator.fit(scores, labels)
    assert c.predict([1.5])[0] < 0.25
    assert c.predict([11.5])[0] > 0.75


# ── Platt ──────────────────────────────────────────────────────────────────────

def test_platt_recovers_logistic_signal():
    rng = np.random.default_rng(1)
    scores = rng.uniform(0, 100, size=2000)
    p_true = 1 / (1 + np.exp(-(scores - 50) / 8))
    labels = (rng.uniform(size=2000) < p_true).astype(float)
    c = cal.PlattCalibrator.fit(scores, labels)
    preds = c.predict(scores)
    # Calibrated probs should track the truth with low Brier
    assert cal.brier_score(preds, labels) < 0.20
    # Monotone increasing in score for a positive logistic relationship
    assert c.predict([20])[0] < c.predict([80])[0]


def test_platt_handles_all_one_class_without_crashing():
    scores = np.array([10, 20, 30, 40, 50], dtype=float)
    labels = np.zeros(5)
    c = cal.PlattCalibrator.fit(scores, labels)
    preds = c.predict(scores)
    assert np.all(preds >= 0) and np.all(preds <= 1)
    assert np.all(preds < 0.5)  # all-negative training -> low probabilities


# ── Metrics ─────────────────────────────────────────────────────────────────────

def test_brier_score_known_value():
    probs = np.array([1.0, 0.0, 0.5])
    labels = np.array([1.0, 0.0, 1.0])
    # (0 + 0 + 0.25)/3
    assert cal.brier_score(probs, labels) == pytest.approx(0.25 / 3)


def test_ece_zero_for_perfectly_calibrated():
    # 100 items at p=0.0 all negative, 100 at p=1.0 all positive -> ECE 0
    probs = np.concatenate([np.zeros(100), np.ones(100)])
    labels = np.concatenate([np.zeros(100), np.ones(100)])
    assert cal.expected_calibration_error(probs, labels) == pytest.approx(0.0, abs=1e-9)


def test_ece_detects_miscalibration():
    # Claims p=0.9 but only half are positive -> large ECE
    probs = np.full(100, 0.9)
    labels = np.array([1.0, 0.0] * 50)
    assert cal.expected_calibration_error(probs, labels) > 0.3


def test_classification_metrics_confusion():
    pred = np.array([1, 1, 0, 0, 1])
    lab = np.array([1, 0, 0, 1, 1])
    m = cal.classification_metrics(pred, lab)
    assert m["true_positive"] == 2
    assert m["false_positive"] == 1
    assert m["false_negative"] == 1
    assert m["true_negative"] == 1
    assert m["precision"] == pytest.approx(2 / 3, rel=1e-3)
    assert m["recall"] == pytest.approx(2 / 3, rel=1e-3)


# ── Grouped split honesty ────────────────────────────────────────────────────────

def test_group_split_has_no_group_overlap():
    groups = np.array([f"z{i // 10}" for i in range(200)])  # 20 groups of 10
    train_idx, test_idx = cal.group_train_test_split(groups, test_fraction=0.3, seed=7)
    train_groups = set(groups[train_idx].tolist())
    test_groups = set(groups[test_idx].tolist())
    assert train_groups.isdisjoint(test_groups)
    assert len(train_idx) + len(test_idx) == 200


def test_group_split_deterministic():
    groups = np.array([f"z{i // 5}" for i in range(100)])
    a = cal.group_train_test_split(groups, seed=3)
    b = cal.group_train_test_split(groups, seed=3)
    np.testing.assert_array_equal(a[0], b[0])
    np.testing.assert_array_equal(a[1], b[1])


# ── End-to-end fit_and_evaluate + serialisation ──────────────────────────────────

def test_fit_and_evaluate_grouped_produces_holdout_metrics():
    rng = np.random.default_rng(11)
    n_groups = 40
    scores, labels, groups = [], [], []
    for g in range(n_groups):
        base = rng.uniform(0, 100)
        p = 1 / (1 + np.exp(-(base - 50) / 10))
        for _ in range(25):
            s = np.clip(base + rng.normal(0, 5), 0, 100)
            scores.append(s)
            labels.append(float(rng.uniform() < p))
            groups.append(f"zip{g}")
    res = cal.fit_and_evaluate(scores, labels, groups=groups, method="isotonic")
    assert res["split_kind"] == "grouped_by_zip"
    assert res["holdout_metrics"] is not None
    assert 0.0 <= res["holdout_metrics"]["brier_score"] <= 1.0
    assert res["calibrator"]["method"] == "isotonic"


def test_fit_and_evaluate_single_class_returns_warning():
    scores = list(np.linspace(0, 100, 50))
    labels = [0.0] * 50
    groups = [f"z{i//5}" for i in range(50)]
    res = cal.fit_and_evaluate(scores, labels, groups=groups)
    assert res["holdout_metrics"] is None
    assert "warning" in res


def test_serialisation_round_trip_identical_predictions():
    rng = np.random.default_rng(5)
    scores = rng.uniform(0, 100, 300)
    labels = (rng.uniform(size=300) < (scores / 100)).astype(float)
    for method in ("isotonic", "platt"):
        c = cal.fit_calibrator(scores, labels, method=method)
        blob = cal.calibrator_to_dict(c)
        # JSON round-trip
        import json
        c2 = cal.load_calibrator(json.loads(json.dumps(blob)))
        np.testing.assert_allclose(c.predict(scores), c2.predict(scores), rtol=1e-9)


def test_auto_method_selection():
    # Small sample -> platt; large balanced -> isotonic
    small_s = np.linspace(0, 100, 50)
    small_l = (small_s > 50).astype(float)
    assert cal.fit_calibrator(small_s, small_l, "auto").method == "platt"

    rng = np.random.default_rng(2)
    big_s = rng.uniform(0, 100, 600)
    big_l = (rng.uniform(size=600) < (big_s / 100)).astype(float)
    assert cal.fit_calibrator(big_s, big_l, "auto").method == "isotonic"
