"""
Tests for Phase 3 severity: multi-curve selection, contents split, duration.

The behaviours worth pinning are the ones that change a dollar figure:
which curve gets selected, what depth it is indexed on, and the refusal to
adjust on data we don't actually have.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "pipeline"))

from config import SEVERITY_CURVES, SEVERITY_DURATION  # noqa: E402
from severity import (  # noqa: E402
    depth_damage_pct, select_curve_key, damage_pct_for, duration_multiplier,
    estimate_claim_range,
)


# ── curve selection ──────────────────────────────────────────────────────────

def test_selects_one_storey_no_basement_from_nsi_occtype():
    assert select_curve_key('RES1-1SNB') == 'RES1-1S-NB'


def test_selects_two_storey_from_nsi_occtype():
    assert select_curve_key('RES1-2SNB') == 'RES1-2S-NB'


def test_selects_basement_variant():
    assert select_curve_key('RES1-1SWB') == 'RES1-1S-B'


def test_explicit_attributes_override_occtype_string():
    """The structure record's own fields beat parsing the code string."""
    assert select_curve_key('RES1-1SNB', num_stories=2, basement_type=0) \
        == 'RES1-2S-NB'


def test_crawlspace_is_not_a_basement():
    """
    NSI basement codes 3/4 are crawlspaces. They hold no finished space, so
    they must not select the basement curve, which starts taking damage below
    grade.
    """
    assert select_curve_key('RES1-1SNB', num_stories=1, basement_type=3) \
        == 'RES1-1S-NB'
    assert select_curve_key('RES1-1SNB', num_stories=1, basement_type=1) \
        == 'RES1-1S-B'


def test_manufactured_and_commercial_map_to_own_curves():
    assert select_curve_key('RES2') == 'RES2'
    assert select_curve_key('COM1') == 'COM'
    assert select_curve_key('RES3A') == 'RES3'


def test_unknown_occupancy_returns_none_not_a_guess():
    """
    Refusing to choose is the correct answer. Defaulting an unknown structure
    to the single-family one-storey curve would apply the most damage-
    sensitive residential shape to, say, a warehouse.
    """
    assert select_curve_key(None) is None
    assert select_curve_key('') is None
    assert select_curve_key('RES1') is None          # no storeys/basement info


# ── curve behaviour ──────────────────────────────────────────────────────────

def test_two_storey_takes_less_damage_than_one_storey():
    """Same water, larger structure -> smaller fraction of value lost."""
    for depth in (1.0, 2.0, 4.0, 8.0):
        one = damage_pct_for(depth, 'RES1-1S-NB')
        two = damage_pct_for(depth, 'RES1-2S-NB')
        assert two < one, f"at {depth}ft"


def test_basement_home_damaged_below_grade():
    """A basement floods before water reaches the first floor."""
    assert damage_pct_for(-2.0, 'RES1-1S-B') > 0
    assert damage_pct_for(-2.0, 'RES1-1S-NB') == 0


def test_manufactured_home_is_most_vulnerable():
    for depth in (1.0, 2.0, 3.0):
        assert damage_pct_for(depth, 'RES2') > damage_pct_for(depth, 'RES1-1S-NB')


def test_contents_damage_exceeds_structure_at_shallow_depth():
    """A few inches ruins flooring and furniture; the structure survives."""
    s = damage_pct_for(1.0, 'RES1-1S-NB', contents=False)
    c = damage_pct_for(1.0, 'RES1-1S-NB', contents=True)
    assert c > s


def test_all_curves_monotonic_non_decreasing():
    for key, curve in SEVERITY_CURVES.items():
        pcts = [p for _, p in curve]
        assert pcts == sorted(pcts), key
        assert all(0 <= p <= 100 for p in pcts), key


def test_unknown_curve_key_falls_back_to_generic():
    assert damage_pct_for(3.0, None) == depth_damage_pct(3.0)
    assert damage_pct_for(3.0, 'NOT-A-CURVE') == depth_damage_pct(3.0)


# ── duration adjustment ──────────────────────────────────────────────────────

def test_duration_unknown_makes_no_adjustment():
    """An assumed duration must never move a dollar figure."""
    assert duration_multiplier(None) == 1.0


def test_short_duration_makes_no_adjustment():
    assert duration_multiplier(1.0) == 1.0


def test_long_duration_increases_damage_but_is_capped():
    assert duration_multiplier(14.0) > 1.0
    assert duration_multiplier(60.0) <= SEVERITY_DURATION['max_multiplier']


def test_duration_multiplier_stays_well_below_unverified_literature_figure():
    """
    The roadmap cites ~2.6x from a paper that could not be read in full. The
    configured cap is deliberately far below it; if someone raises it, that
    should be a conscious act with the source actually read.
    """
    assert SEVERITY_DURATION['max_multiplier'] < 1.5


# ── end-to-end estimate ──────────────────────────────────────────────────────

def test_backward_compatible_three_arg_call():
    """The original signature must behave exactly as it did before Phase 3."""
    out = estimate_claim_range(3.0, 1.0, 300_000)
    assert out is not None
    assert out['curve'] == 'generic'
    assert out['depth_basis'] == 'above_ground'
    assert out['low'] == int(round(300_000 * depth_damage_pct(2.0) / 100))
    assert out['high'] == int(round(300_000 * depth_damage_pct(4.0) / 100))


def test_uses_first_floor_depth_when_supplied():
    """
    4 ft of water around a home on a 2 ft foundation is 2 ft inside. Indexing
    the curve on 4 ft would overstate the loss.
    """
    above_ground = estimate_claim_range(4.0, 0.0, 300_000,
                                        occupancy_type='RES1-1SNB')
    first_floor = estimate_claim_range(4.0, 0.0, 300_000,
                                       occupancy_type='RES1-1SNB',
                                       depth_above_first_floor_ft=2.0)
    assert first_floor['mid'] < above_ground['mid']
    assert first_floor['depth_basis'] == 'above_first_floor'


def test_contents_reported_separately_not_blended():
    out = estimate_claim_range(3.0, 0.5, 300_000, contents_coverage=100_000,
                               occupancy_type='RES1-1SNB')
    assert out['mid'] > 0 and out['contents_mid'] > 0
    assert out['total_mid'] == out['mid'] + out['contents_mid']
    # The structure figure must NOT silently include contents.
    assert out['mid'] < out['total_mid']


def test_no_estimate_without_depth_or_coverage():
    assert estimate_claim_range(0.05, 0.5, 300_000) is None
    assert estimate_claim_range(2.0, 0.5, 0) is None
    assert estimate_claim_range(2.0, 0.5, None) is None
    assert estimate_claim_range('garbage', 0.5, 300_000) is None


def test_elevated_home_still_gets_an_estimate():
    """
    Gating is on depth above ground, so a home whose first floor stayed dry
    still produces a (small) estimate rather than vanishing from the reserve.
    """
    out = estimate_claim_range(3.0, 0.5, 300_000,
                               occupancy_type='RES1-1SNB',
                               depth_above_first_floor_ft=0.0)
    assert out is not None
    assert out['damage_pct'] >= 0


def test_duration_flows_through_to_dollars():
    base = estimate_claim_range(3.0, 0.0, 300_000, occupancy_type='RES1-1SNB')
    long = estimate_claim_range(3.0, 0.0, 300_000, occupancy_type='RES1-1SNB',
                                duration_days=14.0)
    assert long['mid'] > base['mid']
    assert long['duration_multiplier'] > 1.0
