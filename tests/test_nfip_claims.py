"""
Tests for validation/nfip_claims.py — the NFIP Redacted Claims ground truth.

The interesting logic here is the `waterDepth` unit rule. FEMA documents the
field as inches but notes that some records were entered in feet, and
empirically the feet branch dominates in modern events. These tests pin the
rule down so a future change to it has to be deliberate.
"""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "nfip_claims", BASE / "validation" / "nfip_claims.py")
nfip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nfip)


# ── waterDepth unit disambiguation ───────────────────────────────────────────

@pytest.mark.parametrize("raw,expected_ft,expected_unit", [
    (0, 0.0, 'feet'),
    (1, 1.0, 'feet'),
    (6, 6.0, 'feet'),
    (15, 15.0, 'feet'),        # boundary stays in the feet branch
    (-3, -3.0, 'feet'),        # below the reference level: legitimate
    (24, 2.0, 'inches'),       # above the boundary -> inches
    (120, 10.0, 'inches'),     # the Charlotte/Ian surge spike
])
def test_normalize_water_depth_branches(raw, expected_ft, expected_unit):
    depth, unit = nfip.normalize_water_depth(raw)
    assert unit == expected_unit
    assert depth == pytest.approx(expected_ft)


@pytest.mark.parametrize("raw", [None, "", "abc", 500, -100])
def test_normalize_water_depth_rejects_uninterpretable(raw):
    """Out-of-range and unparseable values return None, never a coerced 0."""
    depth, unit = nfip.normalize_water_depth(raw)
    assert depth is None
    assert unit in ('null', 'invalid')


def test_normalize_water_depth_null_distinguished_from_invalid():
    assert nfip.normalize_water_depth(None)[1] == 'null'
    assert nfip.normalize_water_depth(9999)[1] == 'invalid'


# ── zip filter construction ──────────────────────────────────────────────────

def test_zip_filter_builds_or_chain():
    """
    OpenFEMA 503s on `in (...)` for lists of this size, so the OR chain is the
    working form and must stay one.
    """
    f = nfip._zip_filter(['77096', '77035'])
    assert f == "reportedZipCode eq '77096' or reportedZipCode eq '77035'"


# ── aggregation ──────────────────────────────────────────────────────────────

def _claims_frame():
    rows = [
        # zip 77096: two claims with depth, one without
        {'zip': '77096', 'depth_ft': 4.0, 'depth_unit_assumed': 'feet',
         'paid_building': 100000.0, 'paid_contents': 20000.0,
         'damage_building': 120000.0, 'property_value': 300000.0},
        {'zip': '77096', 'depth_ft': 2.0, 'depth_unit_assumed': 'feet',
         'paid_building': 50000.0, 'paid_contents': 10000.0,
         'damage_building': 60000.0, 'property_value': 300000.0},
        {'zip': '77096', 'depth_ft': None, 'depth_unit_assumed': 'null',
         'paid_building': 10000.0, 'paid_contents': 0.0,
         'damage_building': None, 'property_value': None},
        # zip 77035: one dry claim
        {'zip': '77035', 'depth_ft': 0.0, 'depth_unit_assumed': 'feet',
         'paid_building': 5000.0, 'paid_contents': 0.0,
         'damage_building': 6000.0, 'property_value': 200000.0},
    ]
    return pd.DataFrame(rows)


def test_aggregate_by_zip_depth_stats_use_only_valid_depths():
    agg = nfip.aggregate_by_zip(_claims_frame()).set_index('zip')
    # All three claims count toward nfip_claims...
    assert agg.loc['77096', 'nfip_claims'] == 3
    # ...but only the two with a depth feed the depth statistics.
    assert agg.loc['77096', 'nfip_depth_claims'] == 2
    assert agg.loc['77096', 'nfip_mean_depth_ft'] == pytest.approx(3.0)
    assert agg.loc['77096', 'nfip_median_depth_ft'] == pytest.approx(3.0)


def test_aggregate_by_zip_pct_depth_gt0():
    agg = nfip.aggregate_by_zip(_claims_frame()).set_index('zip')
    assert agg.loc['77096', 'nfip_pct_depth_gt0'] == pytest.approx(100.0)
    assert agg.loc['77035', 'nfip_pct_depth_gt0'] == pytest.approx(0.0)


def test_aggregate_by_zip_damage_ratio_ignores_missing_values():
    agg = nfip.aggregate_by_zip(_claims_frame()).set_index('zip')
    # (120000/300000 + 60000/300000) / 2 = 0.3
    assert agg.loc['77096', 'nfip_mean_damage_ratio'] == pytest.approx(0.3)


def test_aggregate_by_zip_empty_input():
    assert nfip.aggregate_by_zip(pd.DataFrame()).empty


def test_unit_split_reports_every_branch():
    split = nfip.unit_split(_claims_frame())
    assert split['total'] == 4
    assert split['counts']['feet'] == 3
    assert split['counts']['null'] == 1
