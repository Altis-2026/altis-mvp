"""Offline tests for NOAA post-storm imagery (no network)."""
import json
from backend import eri


def _catalog(tmp_path, monkeypatch):
    rows = [{"storm": "harvey", "code": "20170831a", "date": "2017-08-31", "host": "x.noaa.gov",
             "bounds": [-96.0, 29.0, -95.0, 30.0], "maxzoom": 19},
            {"storm": "other", "code": "20200101a", "date": "2020-01-01", "host": "x.noaa.gov",
             "bounds": None, "maxzoom": 19}]
    p = tmp_path / "eri.json"
    p.write_text(json.dumps(rows))
    monkeypatch.setattr(eri, "CATALOG_PATH", p)
    eri._catalog_cache.clear(); eri._probe_cache.clear(); eri._tile_cache.clear()


def test_tile_xy_known_point():
    x, y, fx, fy = eri.tile_xy(0.0, 0.0, 1)
    assert (x, y) == (1, 1) and fx == 0 and fy == 0


def test_unknown_layer_and_bad_coords_rejected(tmp_path, monkeypatch):
    _catalog(tmp_path, monkeypatch)
    monkeypatch.setattr(eri.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch")))
    assert eri.fetch_tile("evil", 17, 1, 1) is None
    assert eri.fetch_tile("20170831a", 3, 0, 0) is None
    assert eri.fetch_tile("20170831a", 17, 2 ** 17, 0) is None


def test_blank_tile_is_not_imagery(tmp_path, monkeypatch):
    _catalog(tmp_path, monkeypatch)
    class R:
        status_code = 200; content = b"\x89PNG" + b"0" * 91
    monkeypatch.setattr(eri.requests, "get", lambda *a, **k: R())
    assert eri.has_imagery("20170831a", 17, 1, 1) is False


def test_flights_for_filters_by_date_bounds_and_imagery(tmp_path, monkeypatch):
    _catalog(tmp_path, monkeypatch)
    class R:
        status_code = 200; content = b"\xff\xd8" + b"0" * 5000
    monkeypatch.setattr(eri.requests, "get", lambda *a, **k: R())
    out = eri.flights_for(29.5, -95.5, "2017-08-23")
    assert [f["code"] for f in out["flights"]] == ["20170831a"]
    assert out["flights"][0]["days_after_event"] == 8
    outside = eri.flights_for(40.0, -100.0, "2017-08-23")
    assert outside["flights"] == []
    assert eri.flights_for(29.5, -95.5, "bad")["flights"] == []


def test_outage_is_not_reported_as_no_coverage(tmp_path, monkeypatch):
    _catalog(tmp_path, monkeypatch)
    def boom(*a, **k):
        raise eri.requests.ConnectionError("down")
    monkeypatch.setattr(eri.requests, "get", boom)
    out = eri.flights_for(29.5, -95.5, "2017-08-23")
    assert out["flights"] == [] and out["service_error"] is True
