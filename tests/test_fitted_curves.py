"""
Tests for the depth-damage curves fitted to real paid claims.

These lock the properties that make a fitted curve safe to price with. A
damage curve that is non-monotonic, or that charges for damage at zero depth,
produces a wrong dollar figure silently — nothing raises, the number is just
wrong, and it lands in a reserves report.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))

import config  # noqa: E402
import severity as sv  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_flag():
    """Each test leaves the shipped setting as it found it."""
    original = config.SEVERITY.get('use_fitted_curves')
    yield
    config.SEVERITY['use_fitted_curves'] = original


class TestCurveShape:
    @pytest.mark.parametrize('key', sorted(config.SEVERITY_CURVES_FITTED))
    def test_starts_at_zero_damage_for_zero_depth(self, key):
        """
        No water must mean no damage.

        The raw fit has NO data below 1 ft — trivial damage does not generate a
        claim — so without an explicit (0, 0) anchor the interpolator clamps to
        the first knot and charges 38% of a house's value at zero detected
        depth. The anchor is the fix and it must not be removed.
        """
        curve = config.SEVERITY_CURVES_FITTED[key]
        assert curve[0] == (0.0, 0.0), f"{key} is not anchored at zero"

    @pytest.mark.parametrize('key', sorted(config.SEVERITY_CURVES_FITTED))
    def test_is_monotonic_non_decreasing(self, key):
        """
        More water can never mean less damage in a pricing curve.

        The raw fit falls above 6 ft because 15% of claims are right-censored
        at the $250k NFIP cap and capped claims cluster in deep water. The
        shipped curve is held at its running maximum instead.
        """
        pcts = [p for _, p in config.SEVERITY_CURVES_FITTED[key]]
        assert pcts == sorted(pcts), f"{key} decreases with depth"

    @pytest.mark.parametrize('key', sorted(config.SEVERITY_CURVES_FITTED))
    def test_percentages_are_plausible(self, key):
        for depth, pct in config.SEVERITY_CURVES_FITTED[key]:
            assert 0.0 <= pct <= 100.0, f"{key} at {depth} ft is {pct}%"

    @pytest.mark.parametrize('key', sorted(config.SEVERITY_CURVES_FITTED))
    def test_depths_are_ascending(self, key):
        """_interpolate walks the curve in order; unsorted knots silently
        return the wrong bracket rather than raising."""
        depths = [d for d, _ in config.SEVERITY_CURVES_FITTED[key]]
        assert depths == sorted(depths)


class TestScaleMixingHazard:
    """
    Why the fitted curves ship DISABLED, recorded as a test so the reason
    cannot be lost.

    HAZUS curves measure the fraction of structure value physically damaged.
    The fitted curves measure the fraction of building value PAID, conditional
    on a claim having been filed — systematically higher, because trivial
    damage never becomes a claim. Only some keys could be fitted, so enabling
    them mixes two calibration scales in one curve set and inverts invariants
    that are physically true. Triage ranks properties against each other, so
    cross-segment coherence is not optional.
    """

    def test_ships_disabled(self):
        assert config.SEVERITY['use_fitted_curves'] is False, (
            "Enabling this mixes calibration scales — fit RES2, the basement "
            "variants and contents on the same paid-claims basis first.")

    def test_enabling_breaks_the_manufactured_home_invariant(self):
        """A manufactured home must be more vulnerable than a site-built one.
        This documents the exact breakage, so anyone re-enabling the flag sees
        what they have to fix rather than rediscovering it."""
        config.SEVERITY['use_fitted_curves'] = True
        assert sv.damage_pct_for(1.0, 'RES2') < sv.damage_pct_for(1.0, 'RES1-1S-NB')
        config.SEVERITY['use_fitted_curves'] = False
        assert sv.damage_pct_for(1.0, 'RES2') > sv.damage_pct_for(1.0, 'RES1-1S-NB')

    def test_enabling_breaks_the_contents_invariant(self):
        """Contents must exceed structure damage at shallow depth."""
        config.SEVERITY['use_fitted_curves'] = True
        assert sv.damage_pct_for(1.0, 'RES1-1S-NB', contents=True) < \
            sv.damage_pct_for(1.0, 'RES1-1S-NB')
        config.SEVERITY['use_fitted_curves'] = False
        assert sv.damage_pct_for(1.0, 'RES1-1S-NB', contents=True) > \
            sv.damage_pct_for(1.0, 'RES1-1S-NB')

    def test_unfitted_segments_are_named(self):
        """The segments that blocked enabling must stay absent, so the gap is
        visible rather than silently filled by a borrowed curve."""
        for key in ('RES2', 'RES1-1S-B', 'RES1-2S-B'):
            assert key not in config.SEVERITY_CURVES_FITTED


class TestWiring:
    def test_fitted_curve_is_used_when_enabled(self):
        config.SEVERITY['use_fitted_curves'] = True
        # 1 ft, single-storey no basement: fitted 38.1% vs national 16.0%
        assert sv.damage_pct_for(1.0, 'RES1-1S-NB') == pytest.approx(38.1)

    def test_national_curve_is_used_when_disabled(self):
        """One-line rollback must actually roll back."""
        config.SEVERITY['use_fitted_curves'] = False
        assert sv.damage_pct_for(1.0, 'RES1-1S-NB') == pytest.approx(16.0)

    def test_contents_always_uses_national_curves(self):
        """
        Contents was deliberately NOT fitted: NFIP contents coverage is
        optional and separately capped, so paid contents is not a clean read
        on contents damage. A future edit that quietly extends the fitted
        table to contents should fail here.
        """
        config.SEVERITY['use_fitted_curves'] = True
        national = config.SEVERITY_CONTENTS_CURVES['RES1-1S-NB']
        expected = dict(national)[1.0]
        assert sv.damage_pct_for(1.0, 'RES1-1S-NB', contents=True) == \
            pytest.approx(expected)

    def test_basement_keys_fall_through_to_national(self):
        """
        No basement curve was fitted — only 8,539 of 25,011 claims report a
        basement type and the subset is too thin to bin by depth. Those keys
        must keep the national shapes rather than silently borrow a
        no-basement fitted curve.
        """
        config.SEVERITY['use_fitted_curves'] = True
        assert 'RES1-1S-B' not in config.SEVERITY_CURVES_FITTED
        national = dict(config.SEVERITY_CURVES['RES1-1S-B'])[2.0]
        assert sv.damage_pct_for(2.0, 'RES1-1S-B') == pytest.approx(national)

    def test_unknown_key_still_falls_back_to_generic(self):
        config.SEVERITY['use_fitted_curves'] = True
        assert sv.damage_pct_for(2.0, 'NOT-A-REAL-KEY') == \
            pytest.approx(sv.depth_damage_pct(2.0))

    def test_national_curves_are_retained_not_deleted(self):
        """The rollback is only real if the national shapes still exist."""
        for key in ('RES1-1S-NB', 'RES1-2S-NB', 'RES1-1S-B', 'RES1-2S-B'):
            assert key in config.SEVERITY_CURVES


class TestFittedBeatsNationalWhereItMatters:
    def test_shallow_water_costs_more_than_national_tables_say(self):
        """
        The headline finding: at 1 ft a single-storey home actually lost 38.1%
        of value against HAZUS's 16.0%. If this inverts, the fit has been
        replaced by something else.
        """
        config.SEVERITY['use_fitted_curves'] = True
        fitted = sv.damage_pct_for(1.0, 'RES1-1S-NB')
        config.SEVERITY['use_fitted_curves'] = False
        national = sv.damage_pct_for(1.0, 'RES1-1S-NB')
        assert fitted > national * 2

    def test_two_storey_loses_less_than_one_storey_at_same_depth(self):
        """Same water, larger denominator. True in the national curves and it
        survived the fit — a sanity check that the segmentation is real."""
        config.SEVERITY['use_fitted_curves'] = True
        assert sv.damage_pct_for(2.0, 'RES1-2S-NB') < \
            sv.damage_pct_for(2.0, 'RES1-1S-NB')
