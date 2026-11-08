"""Offline tests for the flood event library and analog retrieval."""
import json

import pytest

from backend import event_library as el


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(el, "LIB_DIR", tmp_path)
    el._cache.clear()
    gfd = [
        {"id": 1, "cc": "USA", "countries": "United States", "dfo_centroid_x": -95.5, "dfo_centroid_y": 29.8,
         "dfo_main_cause": "Tropical Cyclone", "start": "2008-09-01", "end": "2008-09-10",
         "flooded_km2": 5000, "bounds": [[[-96, 29], [-95, 29], [-95, 30], [-96, 30], [-96, 29]]]},
        {"id": 2, "cc": "IND", "countries": "India", "dfo_centroid_x": 80.0, "dfo_centroid_y": 20.0,
         "dfo_main_cause": "Monsoonal rain", "start": "2010-07-01", "end": "2010-08-01", "flooded_km2": 90000},
        {"id": 3, "cc": "USA", "countries": "United States", "dfo_centroid_x": -95.6, "dfo_centroid_y": 29.7,
         "dfo_main_cause": "Heavy rain", "start": "2016-04-18", "end": "2016-04-25", "flooded_km2": 800},
    ]
    (tmp_path / "gfd_events.json").write_text(json.dumps(gfd))
    nfip = {"1": {"n_claims": 900, "median_paid_usd": 20000, "share_paid": 0.8,
                  "median_water_depth_in": 12, "median_flood_duration_h": 48}}
    (tmp_path / "nfip_outcomes.json").write_text(json.dumps(nfip))
    return tmp_path


def test_cause_groups():
    assert el.cause_group("Hurricane Harvey") == "tropical_cyclone"
    assert el.cause_group("Monsoonal rain") == "monsoon"
    assert el.cause_group("Dam/Levy, break or release") == "dam_break"
    assert el.cause_group("Rain and snowmelt") == "snowmelt"
    assert el.cause_group("Heavy Rain") == "heavy_rain"


def test_same_cause_and_place_ranks_first(lib):
    r = el.find_analogs(29.75, -95.5, "2017-08-27", "hurricane", k=3)
    assert r["analogs"][0]["id"] == "gfd-1"
    assert "same cause (tropical cyclone)" in r["analogs"][0]["why"]
    assert r["analogs"][-1]["id"] == "gfd-2"          # far away, different cause


def test_priors_only_from_events_with_real_outcomes(lib):
    r = el.find_analogs(29.75, -95.5, "2017-08-27", "hurricane", k=3)
    assert r["priors"]["based_on"] == 1
    assert r["priors"]["median_paid_per_claim_usd"] == 20000


def test_query_event_is_not_its_own_analog(lib):
    r = el.find_analogs(29.8, -95.5, "2008-09-03", "hurricane", k=3)
    assert "gfd-1" not in [a["id"] for a in r["analogs"]]


def test_runs_are_recorded_and_searchable(lib):
    el.record_altis_run("Test run", 29.7, -95.4, "2026-10-01", "hurricane", {"n_analyzed": 10})
    ids = [r["id"] for r in el.load_library()]
    assert "altis-1" in ids
    assert el.library_stats()["by_source"]["altis"] == 1


def test_commercial_mode_drops_non_commercial_sources(lib, monkeypatch):
    monkeypatch.setenv("ALTIS_COMMERCIAL_SOURCES_ONLY", "1")
    el._cache.clear()
    assert not any(r["source"] == "gfd" for r in el.load_library())
    assert el.library_stats()["commercial_sources_only"] is True
