"""
test_priority.py — Dispatch-queue severity × coverage ranking.
"""
from backend.priority import (
    severity, exposure_multiplier, priority_score, rank_dispatch,
)


def test_severity_monotonic_in_depth_and_area():
    assert severity(0, 0) == 0.0
    assert severity(6, 100) == 1.0
    assert severity(3, 0) < severity(6, 0)
    assert severity(0, 50) < severity(0, 100)


def test_severity_saturates_past_caps():
    assert severity(100, 0) == severity(6, 0)
    assert severity(0, 1000) == severity(0, 100)


def test_exposure_multiplier_neutral_when_no_coverage():
    assert exposure_multiplier(None) == 1.0
    assert exposure_multiplier(0) == 1.0
    assert exposure_multiplier(-5) == 1.0


def test_exposure_multiplier_increases_with_coverage_but_damped():
    low = exposure_multiplier(10_000)
    mid = exposure_multiplier(250_000)
    high = exposure_multiplier(4_000_000)
    assert 1.0 < low < mid < high
    # log-damped: 400x more coverage is well under 2x the multiplier
    assert high < 2 * low


def test_priority_score_combines_severity_and_exposure():
    # Same severity, more coverage → higher priority
    deep_cheap = priority_score(6, 100, 90_000)
    shallow_rich = priority_score(2, 30, 4_000_000)
    assert deep_cheap > 0 and shallow_rich > 0
    # A max-severity property with coverage beats its no-coverage self
    assert priority_score(6, 100, 1_000_000) > priority_score(6, 100, None)


def test_rank_dispatch_orders_and_annotates():
    props = [
        {'property_id': 'a', 'impact_class': 'Dispatch', 'max_depth_ft': 1, 'pct_flooded': 10},
        {'property_id': 'b', 'impact_class': 'Dispatch', 'max_depth_ft': 6, 'pct_flooded': 90},
        {'property_id': 'c', 'impact_class': 'Remote-Deny', 'max_depth_ft': 0, 'pct_flooded': 0},
        {'property_id': 'd', 'impact_class': 'Review', 'max_depth_ft': 3, 'pct_flooded': 40},
    ]
    queue = rank_dispatch(props, classes=('Dispatch', 'Review'))
    ids = [p['property_id'] for p in queue]
    assert 'c' not in ids                  # Remote-Deny excluded
    assert ids[0] == 'b'                    # deepest/largest first
    assert queue[0]['priority_rank'] == 1
    assert queue[-1]['priority_rank'] == len(queue)
    assert all(queue[i]['priority_score'] >= queue[i + 1]['priority_score']
               for i in range(len(queue) - 1))


def test_rank_dispatch_does_not_mutate_input():
    props = [{'property_id': 'a', 'impact_class': 'Dispatch', 'max_depth_ft': 2, 'pct_flooded': 20}]
    rank_dispatch(props)
    assert 'priority_score' not in props[0]


def test_rank_dispatch_handles_missing_and_bad_values():
    props = [
        {'property_id': 'a', 'impact_class': 'Dispatch'},  # no depth/pct
        {'property_id': 'b', 'impact_class': 'Dispatch', 'max_depth_ft': 'NaN', 'pct_flooded': None},
    ]
    queue = rank_dispatch(props)
    assert len(queue) == 2
    assert all('priority_score' in p for p in queue)
