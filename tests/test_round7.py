"""
Round-7 unit tests: severity depth-damage curve, dual-pol cross-check
(confidence factor + Review override), FEMA coordinate gating, and the
inundation-duration slice math. Pure functions only — no network, no EE.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.severity import depth_damage_pct, estimate_claim_range
from pipeline.triage_core import confidence_breakdown, dualpol_review_override
from pipeline.config import SEVERITY, SAR_VH, DURATION
from backend.fema import is_us_coord


# ── Severity: depth-damage curve ─────────────────────────────────────────────

def test_damage_curve_zero_depth_is_zero():
    assert depth_damage_pct(0.0) == 0.0
    assert depth_damage_pct(-1.0) == 0.0


def test_damage_curve_monotonic_nondecreasing():
    depths = [i * 0.25 for i in range(100)]
    pcts = [depth_damage_pct(d) for d in depths]
    assert all(b >= a for a, b in zip(pcts, pcts[1:]))


def test_damage_curve_interpolates_between_knots():
    # Between (2.0, 22) and (3.0, 29) → 2.5ft = 25.5%
    assert abs(depth_damage_pct(2.5) - 25.5) < 1e-9


def test_damage_curve_clamps_beyond_curve():
    assert depth_damage_pct(100.0) == SEVERITY['depth_damage_curve'][-1][1]


def test_claim_range_basic():
    out = estimate_claim_range(3.0, 1.0, 300_000)
    assert out is not None
    assert out['low'] <= out['mid'] <= out['high']
    assert out['low'] == int(round(300_000 * depth_damage_pct(2.0) / 100))
    assert out['high'] == int(round(300_000 * depth_damage_pct(4.0) / 100))


def test_claim_range_none_when_dry_or_no_coverage():
    assert estimate_claim_range(0.05, 0.5, 300_000) is None
    assert estimate_claim_range(2.0, 0.5, 0) is None
    assert estimate_claim_range(2.0, 0.5, None) is None
    assert estimate_claim_range('garbage', 0.5, 300_000) is None


def test_claim_range_ci_widens_range():
    narrow = estimate_claim_range(3.0, 0.2, 500_000)
    wide = estimate_claim_range(3.0, 2.0, 500_000)
    assert (wide['high'] - wide['low']) > (narrow['high'] - narrow['low'])


# ── Dual-polarization cross-check ────────────────────────────────────────────

def _row(**kw):
    base = {'max_depth_ft': 3.0, 'pct_flooded': 0.4, 'urban_flag': 0,
            'optical_available': 0, 'optical_water_pct': 0.0}
    base.update(kw)
    return base


def test_dualpol_agree_flood_adds_bonus():
    b = confidence_breakdown(_row(vh_available=1, vh_water_pct=0.35),
                             {'days_since_event': 3})
    factors = {f['factor']: f['delta'] for f in b['factors']}
    assert factors.get('Dual-pol cross-check') == SAR_VH['agree_bonus']


def test_dualpol_disagree_penalizes():
    b = confidence_breakdown(_row(vh_available=1, vh_water_pct=0.0),
                             {'days_since_event': 3})
    factors = {f['factor']: f['delta'] for f in b['factors']}
    assert factors.get('Dual-pol cross-check') == SAR_VH['disagree_penalty']


def test_dualpol_abstains_when_unavailable():
    b = confidence_breakdown(_row(vh_available=0, vh_water_pct=0.0),
                             {'days_since_event': 3})
    assert 'Dual-pol cross-check' not in {f['factor'] for f in b['factors']}
    # Legacy rows without the keys at all score identically.
    b2 = confidence_breakdown(_row(), {'days_since_event': 3})
    assert b['final_score'] == b2['final_score']


def test_dualpol_review_override_fires_only_on_contradiction():
    assert dualpol_review_override(_row(vh_available=1, vh_water_pct=0.0))[0] is True
    assert dualpol_review_override(_row(vh_available=1, vh_water_pct=0.3))[0] is False
    assert dualpol_review_override(_row(vh_available=0, vh_water_pct=0.0))[0] is False
    # Dry VV call → nothing to contradict.
    assert dualpol_review_override(
        _row(pct_flooded=0.0, vh_available=1, vh_water_pct=0.0))[0] is False


# ── FEMA coordinate gating ───────────────────────────────────────────────────

def test_us_coords_gate():
    assert is_us_coord(29.7, -95.5)        # Houston
    assert is_us_coord(26.97, -82.05)      # Port Charlotte FL
    assert not is_us_coord(-28.81, 153.28)  # Lismore, Australia
    assert not is_us_coord(51.5, -0.1)      # London
    assert is_us_coord(18.2, -66.5)         # Puerto Rico


# ── Inundation duration slice math (mirrors live_pipeline logic) ────────────

def _duration(slice_vals, slice_days, flooded=True):
    known = [(i, v) for i, v in enumerate(slice_vals) if v is not None]
    if len(known) >= 2 and flooded:
        return sum(slice_days[i] for i, v in known
                   if v >= DURATION['slice_flood_pct'])
    if len(known) >= 2:
        return 0
    return None


def test_duration_counts_flooded_slices():
    assert _duration([0.5, 0.4, 0.0], [5, 5, 4]) == 10
    assert _duration([0.5, 0.0, 0.0], [5, 5, 4]) == 5
    assert _duration([0.0, 0.0, 0.0], [5, 5, 4]) == 0


def test_duration_unknown_with_sparse_scenes():
    assert _duration([0.5, None, None], [5, 5, 4]) is None
    assert _duration([None, None, None], [5, 5, 4]) is None


def test_duration_skips_missing_slices():
    # Slice 1 has no scene: only slices 0 and 2 contribute.
    assert _duration([0.5, None, 0.3], [5, 5, 4]) == 9


# ── Column-mapping regressions (global best-score assignment) ────────────────
# These header sets previously produced crossed mappings under greedy
# field-order matching ('state' stealing 'Lat', 'address' stealing 'St').

def test_mapping_carrier_xlsx_headers():
    from backend.ingestion import suggest_column_mapping
    m = suggest_column_mapping(
        ['Claim ID', 'Insured', 'Property Address', 'Lat', 'Long', 'Dwelling Limit'])
    got = {k: v['matched_column'] for k, v in m.items()}
    assert got['policy_number'] == 'Claim ID'
    assert got['address'] == 'Property Address'
    assert got['coverage_amount'] == 'Dwelling Limit'
    assert got['latitude'] == 'Lat'
    assert got['longitude'] == 'Long'


def test_mapping_messy_headers_with_punctuation():
    from backend.ingestion import suggest_column_mapping
    m = suggest_column_mapping(
        ['Pol #', 'Insured', 'Street Addr', 'Town', 'St', 'Post Code', 'TIV ($)'])
    got = {k: v['matched_column'] for k, v in m.items()}
    assert got['policy_number'] == 'Pol #'
    assert got['address'] == 'Street Addr'
    assert got['state'] == 'St'
    assert got['city'] == 'Town'
    assert got['zip'] == 'Post Code'
    assert got['coverage_amount'] == 'TIV ($)'


def test_mapping_never_double_assigns_a_column():
    from backend.ingestion import suggest_column_mapping
    m = suggest_column_mapping(['Address', 'Location', 'Lat', 'Lon'])
    cols = [v['matched_column'] for v in m.values() if v['matched_column']]
    assert len(cols) == len(set(cols))
