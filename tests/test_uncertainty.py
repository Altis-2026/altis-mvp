"""Tests for pipeline/uncertainty.py — per-depth uncertainty interval."""
import math

import pytest

import uncertainty as unc
from config import UNCERTAINTY, SAR


def test_dry_property_has_zero_uncertainty():
    assert unc.depth_uncertainty_ft(0.0, 1) == 0.0
    assert unc.depth_uncertainty_ft(-1.0, 1) == 0.0


def test_lidar_is_much_tighter_than_srtm():
    # Same depth, but 1m lidar should give a far smaller interval than 30m SRTM.
    lidar = unc.depth_uncertainty_ft(3.0, 1)
    srtm = unc.depth_uncertainty_ft(3.0, 30)
    assert lidar < srtm
    # SRTM's ~6m RMSE (~20ft) should dominate and produce a large interval.
    assert srtm > 15.0


def test_interval_respects_floor():
    # A tiny depth on perfect data still can't claim sub-floor precision.
    ci = unc.depth_uncertainty_ft(0.4, 1)
    assert ci >= UNCERTAINTY['min_ci_ft']


def test_measured_spread_increases_interval():
    base = unc.depth_uncertainty_ft(3.0, 1, wse_spread_ft=0.0)
    wide = unc.depth_uncertainty_ft(3.0, 1, wse_spread_ft=4.0)
    assert wide > base


def test_quadrature_combination_is_correct():
    # With known sigmas, the result equals sqrt(dem^2 + wse^2) (above the floor).
    sigma_dem_ft = UNCERTAINTY['dem_vertical_rmse_m'][1] * 3.28084
    spread = 6.0
    sigma_wse = UNCERTAINTY['wse_spread_to_sigma'] * spread
    expected = math.sqrt(sigma_dem_ft ** 2 + sigma_wse ** 2)
    got = unc.depth_uncertainty_ft(5.0, 1, wse_spread_ft=spread)
    assert got == pytest.approx(round(expected, 2), abs=0.01)


def test_interval_clamped_to_physical_range():
    lower, upper, ci = unc.depth_interval_ft(1.0, 30)  # SRTM huge interval
    assert lower == 0.0                       # cannot go negative
    assert upper <= SAR['max_plausible_depth_ft']
    assert ci > 0


def test_interval_lower_upper_bracket_depth_for_lidar():
    lower, upper, ci = unc.depth_interval_ft(3.0, 1, wse_spread_ft=0.5)
    assert lower < 3.0 < upper
    assert upper - lower == pytest.approx(2 * ci, abs=0.05)


def test_format_string():
    assert unc.format_depth_with_interval(0.0, 1) == "dry (0 ft)"
    s = unc.format_depth_with_interval(2.3, 1, wse_spread_ft=0.3)
    assert s.startswith("2.3 ft ±")


def test_unknown_resolution_uses_conservative_default():
    ci_unknown = unc.depth_uncertainty_ft(3.0, 999)
    ci_srtm = unc.depth_uncertainty_ft(3.0, 30)
    # Default equals the conservative (SRTM-like) fallback.
    assert ci_unknown == pytest.approx(ci_srtm, abs=0.01)


def test_handles_bad_inputs_gracefully():
    assert unc.depth_uncertainty_ft(None, 1) == 0.0
    assert unc.depth_uncertainty_ft("x", 1) == 0.0
    assert unc.depth_uncertainty_ft(3.0, None) > 0  # falls back to default res
    assert unc.depth_uncertainty_ft(3.0, 1, wse_spread_ft=float("nan")) > 0
