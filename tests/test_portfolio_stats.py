from backend.main import _portfolio_stats


def test_portfolio_stats_counts_each_category():
    results = [
        {"impact_class": "Dispatch"},
        {"impact_class": "Dispatch"},
        {"impact_class": "Remote-Approve"},
        {"impact_class": "Remote-Deny"},
        {"impact_class": "Review"},
    ]
    stats = _portfolio_stats(results)
    assert stats["total"] == 5
    assert stats["dispatch"] == 2
    assert stats["remote_total"] == 2
    assert stats["review"] == 1


def test_portfolio_stats_estimated_savings_is_per_remote_property():
    results = [{"impact_class": "Remote-Approve"}, {"impact_class": "Remote-Deny"}]
    stats = _portfolio_stats(results)
    assert stats["estimated_savings"] == 2 * 750


def test_portfolio_stats_empty_results():
    stats = _portfolio_stats([])
    assert stats == {
        "total": 0,
        "dispatch": 0,
        "remote_total": 0,
        "review": 0,
        "estimated_savings": 0,
    }


def test_portfolio_stats_ignores_no_coverage_class():
    results = [{"impact_class": "No Coverage"}, {"impact_class": "Dispatch"}]
    stats = _portfolio_stats(results)
    assert stats["total"] == 2
    assert stats["dispatch"] == 1
    assert stats["remote_total"] == 0
    assert stats["review"] == 0
