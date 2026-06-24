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


def test_confidence_coverage_coherence_uses_fraction_scale():
    """
    Coverage coherence operates on the 0-1 fraction scale. A property that is
    extensively flooded (60%+ coverage) earns the high-coverage bonus (+8),
    while one with mid-range partial coverage (5-35%) takes the ambiguity
    penalty (-3). These must differ — the pre-fix code treated every fraction
    as "< 5" and gave them all the same "confidently dry" bonus.
    """
    extensive = {"max_depth_ft": 2.0, "pct_flooded": 0.70, "urban_flag": 0}
    ambiguous = {"max_depth_ft": 2.0, "pct_flooded": 0.15, "urban_flag": 0}
    # +8 (coverage >= 0.60) vs -3 (ambiguous partial) → 11-point spread.
    assert calculate_confidence(extensive, HARVEY) == calculate_confidence(ambiguous, HARVEY) + 11


def test_confidence_confidently_dry_gets_boost():
    """Near-zero coverage (< 0.05 fraction) is a confident 'dry' signal (+7)."""
    dry = {"max_depth_ft": 0.0, "pct_flooded": 0.0, "urban_flag": 0}
    ambiguous = {"max_depth_ft": 0.0, "pct_flooded": 0.15, "urban_flag": 0}
    assert calculate_confidence(dry, HARVEY) > calculate_confidence(ambiguous, HARVEY)


def test_confidence_internal_inconsistency_penalty_fires_on_fraction_scale():
    """
    Deep water over a tiny footprint (depth > 1.5ft, coverage < 0.08) is
    physically suspicious and must be penalized relative to a coherent
    deep+extensive reading.
    """
    suspicious = {"max_depth_ft": 3.0, "pct_flooded": 0.04, "urban_flag": 0}  # deep, tiny area
    coherent = {"max_depth_ft": 3.0, "pct_flooded": 0.70, "urban_flag": 0}    # deep, extensive
    assert calculate_confidence(suspicious, HARVEY) < calculate_confidence(coherent, HARVEY)


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
