"""
Tests for the Phase 1 detection changes:
  - HAND replacing the relative-elevation heuristic in the DEM-hydrology vote
  - the multi-temporal baseline window arithmetic

The behaviour that matters most is precedence and abstention: HAND wins when
present, the old heuristic still works when it isn't, and a MISSING HAND value
abstains rather than being read as 0 (which would mean "at the drainage line" —
the most flood-prone value there is).
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "pipeline"))

from config import HAND, ENSEMBLE  # noqa: E402
from triage_core import ensemble_votes  # noqa: E402
from flood_detect import baseline_window  # noqa: E402


def _row(**kw):
    base = {'pct_flooded': 0.5, 'optical_available': 0}
    base.update(kw)
    return base


# ── HAND takes precedence ────────────────────────────────────────────────────

def test_low_hand_votes_flood_plausible():
    votes = ensemble_votes(_row(hand_ft=2.0))
    assert votes['dem_hydrology'] == 'flood'


def test_high_hand_votes_dry_implausible():
    votes = ensemble_votes(_row(hand_ft=HAND['implausible_ft'] + 10))
    assert votes['dem_hydrology'] == 'dry'


def test_mid_hand_abstains():
    mid = (HAND['plausible_ft'] + HAND['implausible_ft']) / 2
    votes = ensemble_votes(_row(hand_ft=mid))
    assert votes['dem_hydrology'] == 'abstain'


def test_hand_overrides_relative_elevation():
    """
    HAND says perched well above drainage; the old heuristic says low-lying.
    HAND is the hydrologically correct measure and must win.
    """
    votes = ensemble_votes(_row(hand_ft=HAND['implausible_ft'] + 5,
                                rel_elev_ft=1.0))
    assert votes['dem_hydrology'] == 'dry'


# ── fallback to the legacy heuristic ─────────────────────────────────────────

def test_falls_back_to_rel_elev_when_hand_missing():
    votes = ensemble_votes(_row(rel_elev_ft=1.0))
    assert votes['dem_hydrology'] == 'flood'

    votes = ensemble_votes(_row(rel_elev_ft=ENSEMBLE['dem_implausible_rel_ft'] + 5))
    assert votes['dem_hydrology'] == 'dry'


def test_missing_hand_is_none_not_zero():
    """
    A property with neither HAND nor rel_elev must abstain. If a missing HAND
    were coerced to 0.0 it would read as "at the drainage line" and vote flood.
    """
    votes = ensemble_votes(_row(hand_ft=None))
    assert votes['dem_hydrology'] == 'abstain'


def test_garbage_hand_abstains():
    assert ensemble_votes(_row(hand_ft='n/a'))['dem_hydrology'] == 'abstain'
    assert ensemble_votes(_row(hand_ft=float('nan')))['dem_hydrology'] == 'abstain'


def test_hand_zero_is_meaningful_and_votes_flood():
    """HAND of exactly 0 means at the drainage line — genuinely flood-prone."""
    assert ensemble_votes(_row(hand_ft=0.0))['dem_hydrology'] == 'flood'


# ── baseline window arithmetic ───────────────────────────────────────────────

def test_baseline_window_ends_before_event():
    start, end = baseline_window('2017-08-27', months=12, gap_days=2)
    assert end == '2017-08-25'
    assert start == '2016-08-25'


def test_baseline_window_respects_month_count():
    start, end = baseline_window('2022-09-28', months=6, gap_days=2)
    assert end == '2022-09-26'
    # 6 months back from 2022-09-26
    assert start.startswith('2022-03')


def test_baseline_window_uses_config_defaults():
    start, end = baseline_window('2017-08-27')
    assert start < end < '2017-08-27'
