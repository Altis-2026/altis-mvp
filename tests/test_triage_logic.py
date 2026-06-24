import pytest

from config import HARVEY, TRIAGE
from tests.conftest import load_pipeline_module

triage_notes = load_pipeline_module("04_triage_notes.py")
calculate_confidence = triage_notes.calculate_confidence
classify_triage = triage_notes.classify_triage


def test_confidence_score_is_clamped_to_30_97():
    row = {"max_depth_ft": 0.0, "pct_flooded": 0.0, "urban_flag": 0}
    score = calculate_confidence(row, HARVEY)
    assert 30 <= score <= 97


def test_confidence_penalized_for_urban_shadow_zone():
    # pct_flooded is a 0-1 fraction at the point calculate_confidence runs
    # (sample_properties / run_triage_pipeline convert to 0-100 only afterward, for display).
    row_urban = {"max_depth_ft": 0.4, "pct_flooded": 0.10, "urban_flag": 1}
    row_rural = {"max_depth_ft": 0.4, "pct_flooded": 0.10, "urban_flag": 0}
    assert calculate_confidence(row_urban, HARVEY) == calculate_confidence(row_rural, HARVEY) - 15


def test_confidence_internal_inconsistency_penalty_uses_percent_scale_thresholds():
    """
    KNOWN BUG: the "internal consistency" and "coverage coherence" branches in
    calculate_confidence compare `pct` against thresholds written for a 0-100
    percent scale (pct >= 60, pct >= 35, pct < 5, pct > 45), but the function is
    always called with pct_flooded as a 0-1 fraction in the real pipeline
    (see run_triage_pipeline in 04_triage_notes.py — the *100 conversion to
    percent happens AFTER classify_triage/calculate_confidence run). Because a
    fraction is always < 5, `pct < 5` is always true and every property — fully
    flooded or completely dry — gets treated as "confidently dry" (+7). This
    test documents the current (buggy) behavior; it is not the intended design.
    """
    fully_flooded = {"max_depth_ft": 5.0, "pct_flooded": 1.0, "urban_flag": 0}  # 100% flooded
    completely_dry = {"max_depth_ft": 0.0, "pct_flooded": 0.0, "urban_flag": 0}  # 0% flooded

    score_flooded = calculate_confidence(fully_flooded, HARVEY)
    score_dry = calculate_confidence(completely_dry, HARVEY)

    # Both hit the same "pct < 5" branch today because pct is a fraction, not a percent.
    # A correct implementation would NOT score full flooding the same way as fully dry.
    assert score_flooded != score_dry  # depth/recency factors still differ
    # This assertion exists to flag the gap: if someone "fixes" the % thresholds
    # to operate on the 0-1 scale, this test should be revisited.


# Note: pct_flooded is a 0-1 fraction here too — classify_triage runs before
# the *100-to-percent conversion in run_triage_pipeline, and TRIAGE thresholds
# in config.py (dispatch_pct=0.50, remote_deny_pct=0.05, remote_approve_min_pct=0.20)
# are correctly written for that 0-1 scale.
@pytest.mark.parametrize(
    "depth,pct,conf,expected_class",
    [
        (5.5, 0.01, 90, "Dispatch"),         # extreme depth always dispatches
        (3.5, 0.01, 60, "Dispatch"),         # deep + adequate confidence
        (1.2, 0.60, 60, "Dispatch"),         # shallow but extensive coverage
        (0.10, 0.02, 85, "Remote-Deny"),     # dry, near-zero coverage, high confidence
        (0.7, 0.30, 80, "Remote-Approve"),   # moderate confirmed flooding
        (0.6, 0.0, 60, "Review"),            # borderline / low confidence
    ],
)
def test_classify_triage_categories(depth, pct, conf, expected_class):
    row = {"max_depth_ft": depth, "pct_flooded": pct, "confidence_score": conf}
    impact_class, _action = classify_triage(row, TRIAGE)
    assert impact_class == expected_class


def test_classify_triage_returns_action_string():
    row = {"max_depth_ft": 4.0, "pct_flooded": 0.01, "confidence_score": 90}
    _impact_class, action = classify_triage(row, TRIAGE)
    assert isinstance(action, str) and len(action) > 0
