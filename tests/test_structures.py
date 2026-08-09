"""
Tests for pipeline/structures.py — NSI attributes and depth above first floor.

The core claim Phase 2 makes is that depth above ground and depth above first
floor are different numbers, and that the difference is systematic rather than
noise. These tests pin the arithmetic and, importantly, pin the behaviour when
the foundation height is UNKNOWN — where the honest answer is None, not a
silent fallback to depth above ground.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "pipeline"))

import structures as struct  # noqa: E402


# ── first-floor height ───────────────────────────────────────────────────────

def test_first_floor_height_parses_numeric():
    assert struct.first_floor_height_ft(0.75) == pytest.approx(0.75)
    assert struct.first_floor_height_ft("2") == pytest.approx(2.0)


@pytest.mark.parametrize("bad", [None, "", "n/a", -1])
def test_first_floor_height_unknown_is_none(bad):
    assert struct.first_floor_height_ft(bad) is None


# ── depth above first floor ──────────────────────────────────────────────────

def test_depth_above_first_floor_subtracts_foundation():
    # Slab home: 3 ft of water above grade, 0.75 ft foundation -> 2.25 ft inside.
    assert struct.depth_above_first_floor(3.0, 0.75) == pytest.approx(2.25)


def test_depth_above_first_floor_pier_home_stays_dry():
    """
    The false positive Phase 2 exists to kill: 4 ft of water around a home on
    a 5.25 ft pier foundation means nothing reached the living space.
    """
    assert struct.depth_above_first_floor(4.0, 5.25) == 0.0


def test_depth_above_first_floor_never_negative():
    assert struct.depth_above_first_floor(0.5, 2.0) == 0.0


def test_depth_above_first_floor_unknown_foundation_is_none():
    """
    Unknown foundation height must NOT silently fall back to depth above
    ground — that would report an unadjusted number as if it were adjusted.
    """
    assert struct.depth_above_first_floor(3.0, None) is None
    assert struct.depth_above_first_floor(3.0, "") is None


def test_depth_above_first_floor_bad_depth_is_none():
    assert struct.depth_above_first_floor(None, 0.75) is None


# ── footprint radius ─────────────────────────────────────────────────────────

def test_footprint_radius_equal_area_circle():
    # 2569 sqft -> ~238.7 m^2 -> radius ~8.7 m
    assert struct.footprint_radius_m(2569) == pytest.approx(8.7, abs=0.1)


def test_footprint_radius_much_smaller_than_legacy_buffer():
    """The whole point: a typical structure is far smaller than the 50m buffer."""
    assert struct.footprint_radius_m(2569) < 50


def test_footprint_radius_floors_tiny_and_missing_areas():
    assert struct.footprint_radius_m(None) == 5.0
    assert struct.footprint_radius_m(0) == 5.0
    assert struct.footprint_radius_m(10) == 5.0       # sub-pixel -> floor


def test_footprint_radius_caps_absurd_areas():
    assert struct.footprint_radius_m(10_000_000) == 30.0


# ── foundation labels ────────────────────────────────────────────────────────

def test_foundation_label_known_codes():
    assert struct.foundation_label('S') == 'Slab'
    assert struct.foundation_label('p') == 'Pier'
    assert struct.foundation_label('B') == 'Basement'


def test_foundation_label_passes_through_unknown():
    assert struct.foundation_label('Z') == 'Z'
    assert struct.foundation_label(None) is None


# ── property/structure matching ──────────────────────────────────────────────

def _props():
    return pd.DataFrame([
        {'property_id': 'P1', 'latitude': 29.7000, 'longitude': -95.4000},
        {'property_id': 'P2', 'latitude': 29.7100, 'longitude': -95.4100},
    ])


def _nsi():
    return pd.DataFrame([
        # ~10m from P1
        {'latitude': 29.70009, 'longitude': -95.40000, 'fd_id': 1,
         'occtype': 'RES1-1SNB', 'st_damcat': 'RES', 'found_ht': 0.75,
         'found_type': 'S', 'num_story': 1, 'sqft': 2000, 'ftprntsqft': 2000,
         'val_struct': 300000, 'val_cont': 150000, 'ground_elv': 50.0,
         'med_yr_blt': 1980, 'firmzone': 'AE', 'bldheight': 5.0,
         'usastrucid': 'X1'},
        # far from everything (~1km from P2)
        {'latitude': 29.7200, 'longitude': -95.4100, 'fd_id': 2,
         'occtype': 'RES1-2SNB', 'st_damcat': 'RES', 'found_ht': 2.0,
         'found_type': 'C', 'num_story': 2, 'sqft': 3000, 'ftprntsqft': 1500,
         'val_struct': 400000, 'val_cont': 200000, 'ground_elv': 55.0,
         'med_yr_blt': 1995, 'firmzone': 'X', 'bldheight': 7.0,
         'usastrucid': 'X2'},
    ])


def test_match_attaches_nearest_structure():
    out = match = struct.match_properties_to_structures(_props(), _nsi())
    p1 = out[out['property_id'] == 'P1'].iloc[0]
    assert p1['nsi_matched']
    assert p1['found_ht'] == 0.75
    assert p1['nsi_match_m'] < 20


def test_match_rejects_distant_structures():
    """A structure a kilometre away is not this property's structure."""
    out = struct.match_properties_to_structures(_props(), _nsi())
    p2 = out[out['property_id'] == 'P2'].iloc[0]
    assert not p2['nsi_matched']
    # Attributes are cleared, so nothing downstream can use a bad match.
    # (pandas stores the cleared numeric cell as NaN rather than None, so the
    # contract being asserted is "unusable", which is what the consumers see.)
    assert struct.first_floor_height_ft(p2['found_ht']) is None
    assert struct.depth_above_first_floor(3.0, p2['found_ht']) is None


def test_match_returns_all_properties_when_nsi_empty():
    """Outside CONUS the NSI result is empty; properties must not be dropped."""
    out = struct.match_properties_to_structures(_props(), pd.DataFrame())
    assert len(out) == 2
    assert not out['nsi_matched'].any()


def test_match_empty_properties():
    out = struct.match_properties_to_structures(pd.DataFrame(), _nsi())
    assert out.empty
