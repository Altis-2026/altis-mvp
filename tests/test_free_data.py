"""Offline tests for the free-data integrations: satellite pass prediction,
gauge classification, along-channel water-surface interpolation, and the
gauge-radar conflict flag. No network: inputs are built by hand."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend import passes, gauges
from backend import database


def _feat(dt, platform="sentinel-1c", rel=34, state="ascending", mode="IW", fid="x"):
    return {"id": fid, "properties": {"datetime": dt, "platform": platform,
                                      "sat:relative_orbit": rel, "sat:orbit_state": state,
                                      "sar:instrument_mode": mode}}


def test_s1_slices_of_one_overpass_merge():
    acqs = passes.normalize_s1([_feat("2026-11-05T00:25:50Z"), _feat("2026-11-05T00:26:15Z")])
    assert len(acqs) == 1
    assert acqs[0]["datetime"] == "2026-11-05T00:25:50Z"


def test_non_iw_modes_are_reported_but_not_usable():
    acqs = passes.normalize_s1([_feat("2026-11-05T12:30:37Z", mode="SM")])
    assert acqs[0]["usable"] is False
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    assert passes.predict_next_passes(acqs, now) == []


def test_next_pass_is_last_plus_whole_12_day_cycles():
    acqs = passes.normalize_s1([_feat("2026-10-12T00:26:00Z")])
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    pred = passes.predict_next_passes(acqs, now)
    # 10-12 + 12 = 10-24, + 12 = 11-05 (past), + 12 = 11-17 (first future)
    assert pred[0]["expected"].startswith("2026-11-17")
    s = passes.summarize(acqs, pred, now)
    assert s["next_expected_pass"].startswith("2026-11-17")
    assert s["hours_since_last_pass"] > 24 * 26


def test_flood_category_uses_gauge_thresholds():
    thr = {"action": 20, "minor": 45, "moderate": 48, "major": 50}
    assert gauges.classify_stage(55.19, thr) == "major"
    assert gauges.classify_stage(46, thr) == "minor"
    assert gauges.classify_stage(10, thr) == "no_flooding"
    assert gauges.classify_stage(None, thr) == "unknown"


def test_pool_gauges_and_river_names():
    assert gauges.is_pool_gauge("Buffalo Bayou at Barker Reservoir")
    assert gauges.is_pool_gauge("Langham Creek at Addicks Reservoir")
    assert not gauges.is_pool_gauge("Brazos River at Richmond")
    assert gauges.river_name("Brazos River at Richmond") == "Brazos River"
    assert gauges.river_name("Brazos River near Rosharon") == "Brazos River"


def test_wse_interpolates_along_channel_and_holds_flat_beyond_ends():
    # Straight north-south channel; upstream gauge at the north end.
    line = [[-95.0, 30.0], [-95.0, 29.9], [-95.0, 29.8]]
    gs = [{"latitude": 30.0, "longitude": -95.0, "peak_wse_navd88_ft": 100.0},
          {"latitude": 29.8, "longitude": -95.0, "peak_wse_navd88_ft": 80.0}]
    wse, dist, _ = gauges.interpolate_wse_along_channel(
        [29.9, 29.9, 29.7], [-95.0, -94.99, -95.0], gs, line)
    assert abs(wse[0] - 90.0) < 0.2           # midway between gauges
    assert 900 < dist[1] < 1100               # ~1 km east of the channel
    assert abs(wse[2] - 80.0) < 0.2           # beyond the downstream gauge: held flat


def test_chain_segments_joins_reversed_pieces():
    segs = [[[0, 0], [1, 0]], [[2, 0], [1, 0]], [[2, 0], [3, 0]]]
    chain = gauges._chain_segments(segs)
    assert chain[0] == [0, 0] and chain[-1] == [3, 0] and len(chain) == 4


def test_gauge_conflict_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "OUTPUT_DIR", tmp_path)
    pd.DataFrame({"property_id": [1, 2, 3],
                  "gauge_depth_ft": [2.5, 2.5, np.nan],
                  "gauge_wse_ft": [80, 80, np.nan], "gauge_dist_m": [100, 100, np.nan],
                  "gauge_river": ["Brazos River", "Brazos River", None]}
                 ).to_csv(tmp_path / "ev_gauge_depth.csv", index=False)
    df = pd.DataFrame({"property_id": [1, 2, 3], "pct_flooded": [0.0, 60.0, 0.0],
                       "impact_class": ["Remote-Deny", "Dispatch", "Remote-Deny"]})
    out = database.attach_gauge_depth(df, "ev")
    assert out["gauge_conflict"].tolist() == [1, 0, 0]   # only dry-radar + wet-gauge
    assert database.get_event_stats(out)["gauge_conflicts"] == 1
