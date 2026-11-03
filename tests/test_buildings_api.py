"""
test_buildings_api.py — Overpass fetch, SQLite cache, and the /api/buildings
route behind the 3D property-inspect view.

No live network: Overpass is stubbed with a fake session so the suite stays
offline and deterministic. The stub replays the same JSON shape a real
Overpass `out geom;` response has (verified against live responses from the
Harvey, Ian, and Lismore study areas while this was built).
"""
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.config import BUILDINGS

FIXTURE = Path(__file__).parent / "fixtures" / "osm_buildings_houston.json"


@pytest.fixture
def temp_db(monkeypatch):
    """Point every DB user at a scratch file so the committed altis.db is untouched."""
    path = Path(tempfile.mktemp(suffix=".db"))
    import backend.database as db
    monkeypatch.setattr(db, "DB_PATH", path)
    import backend.buildings as b
    monkeypatch.setattr(b, "DB_PATH", path)
    return path


@pytest.fixture(scope="module")
def osm_payload():
    return json.loads(FIXTURE.read_text())


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text_body=None):
        self.status_code = status_code
        self._payload = payload
        self._text = text_body

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class FakeSession:
    """Replays a scripted sequence of responses and records the calls made."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({'url': url, 'params': params})
        r = self._responses.pop(0) if self._responses else FakeResponse(504)
        if isinstance(r, Exception):
            raise r
        return r


# ── Viewport helpers ─────────────────────────────────────────────────────────

def test_bbox_encloses_and_pads_the_properties():
    import backend.buildings as b
    props = [{'latitude': 29.70, 'longitude': -95.50},
             {'latitude': 29.71, 'longitude': -95.49}]
    s, w, n, e = b.bbox_for_properties(props, pad_m=60.0)
    assert s < 29.70 and n > 29.71
    assert w < -95.50 and e > -95.49
    # ~60m of padding on each side, in degrees of latitude.
    assert (29.70 - s) == pytest.approx(60.0 / 110540.0, rel=0.05)


def test_bbox_requires_a_geocoded_property():
    import backend.buildings as b
    with pytest.raises(b.BuildingFetchError):
        b.bbox_for_properties([{'latitude': None, 'longitude': None}])


def test_oversized_viewport_is_refused_before_hitting_overpass():
    import backend.buildings as b
    session = FakeSession([])
    huge = [0.0, 0.0, BUILDINGS['max_bbox_deg'] + 1.0, 1.0]
    with pytest.raises(b.BuildingFetchError, match="too large"):
        b.fetch_overpass(huge, session=session)
    assert session.calls == []          # never left the building


def test_query_contains_the_bbox_and_asks_for_geometry():
    import backend.buildings as b
    q = b.overpass_query([29.70, -95.50, 29.71, -95.49])
    assert 'way["building"]' in q and 'relation["building"]' in q
    assert 'out geom;' in q
    assert '29.700000,-95.500000,29.710000,-95.490000' in q


# ── Overpass fetch ───────────────────────────────────────────────────────────

def test_successful_fetch_normalizes_footprints(osm_payload):
    import backend.buildings as b
    session = FakeSession([FakeResponse(200, osm_payload)])
    footprints = b.fetch_overpass([29.675, -95.475, 29.690, -95.455], session=session)
    assert len(footprints) >= 50
    assert all(f['ring'][0] == f['ring'][-1] for f in footprints)
    assert len(session.calls) == 1


def test_failover_to_the_next_mirror(osm_payload):
    """The primary endpoint 504s under load — the next mirror must be tried."""
    import backend.buildings as b
    session = FakeSession([FakeResponse(504), FakeResponse(200, osm_payload)])
    footprints = b.fetch_overpass([29.675, -95.475, 29.690, -95.455], session=session)
    assert len(footprints) >= 50
    assert len(session.calls) == 2
    assert session.calls[0]['url'] != session.calls[1]['url']


def test_html_error_page_is_treated_as_a_failure(osm_payload):
    """An overloaded Overpass returns an HTML page with HTTP 200."""
    import backend.buildings as b
    session = FakeSession([FakeResponse(200, payload=None), FakeResponse(200, osm_payload)])
    footprints = b.fetch_overpass([29.675, -95.475, 29.690, -95.455], session=session)
    assert len(footprints) >= 50


def test_network_exception_moves_on_to_the_next_mirror(osm_payload):
    import backend.buildings as b
    session = FakeSession([OSError("connection reset"), FakeResponse(200, osm_payload)])
    assert b.fetch_overpass([29.675, -95.475, 29.690, -95.455], session=session)


def test_total_failure_raises_with_a_readable_reason(monkeypatch):
    import backend.buildings as b
    monkeypatch.setitem(BUILDINGS, 'overpass_retries', 0)
    session = FakeSession([FakeResponse(429), FakeResponse(504), FakeResponse(500)])
    with pytest.raises(b.BuildingFetchError, match="HTTP"):
        b.fetch_overpass([29.675, -95.475, 29.690, -95.455], session=session)


# ── Cache ────────────────────────────────────────────────────────────────────

def test_cache_roundtrip_and_second_call_skips_the_network(temp_db, osm_payload):
    import backend.buildings as b
    bbox = [29.675, -95.475, 29.690, -95.455]

    session = FakeSession([FakeResponse(200, osm_payload)])
    first, source = b.load_footprints(bbox, session=session)
    assert source == 'overpass'
    assert len(session.calls) == 1

    second, source2 = b.load_footprints(bbox, session=FakeSession([]))
    assert source2 == 'cache'
    assert len(second) == len(first)


def test_stale_cache_entries_are_ignored(temp_db, osm_payload):
    import backend.buildings as b
    bbox = [29.675, -95.475, 29.690, -95.455]
    b.cache_put(bbox, [{'id': 'stale'}])
    assert b.cache_get(bbox, ttl_hours=0) is None      # instantly expired


def test_nearby_viewports_share_a_cache_entry(temp_db, osm_payload):
    """Panning a few metres must not re-query Overpass."""
    import backend.buildings as b
    bbox = [29.6750, -95.4750, 29.6900, -95.4550]
    nudged = [29.67501, -95.47501, 29.69001, -95.45501]
    b.load_footprints(bbox, session=FakeSession([FakeResponse(200, osm_payload)]))
    _, source = b.load_footprints(nudged, session=FakeSession([]))
    assert source == 'cache'


# ── building_context (the API's workhorse) ───────────────────────────────────

def _props_on_footprints(osm_payload, n=5):
    from pipeline.building_context import normalize_osm_response
    fps = normalize_osm_response(osm_payload)[:n]
    return [{'property_id': f'P{i}', 'latitude': f['centroid'][1],
             'longitude': f['centroid'][0]} for i, f in enumerate(fps)]


def test_context_matches_real_footprints(temp_db, osm_payload):
    import backend.buildings as b
    props = _props_on_footprints(osm_payload)
    out = b.building_context(props, session=FakeSession([FakeResponse(200, osm_payload)]))
    assert out['available'] is True
    assert out['summary']['footprints_matched'] == len(props)
    assert all(rec['footprint_source'] == 'osm' for rec in out['buildings'])


def test_context_degrades_to_placeholders_when_overpass_is_down(temp_db, monkeypatch):
    import backend.buildings as b
    monkeypatch.setitem(BUILDINGS, 'overpass_retries', 0)
    props = [{'property_id': 'P1', 'latitude': 29.68, 'longitude': -95.47}]
    out = b.building_context(props, session=FakeSession([FakeResponse(503)] * 5))

    assert out['available'] is False
    assert 'failed' in out['reason'].lower()
    # The view still renders: a labelled box per property, never a blank map.
    assert len(out['buildings']) == 1
    assert out['buildings'][0]['footprint_source'] == 'placeholder'


def test_context_with_no_geocoded_properties(temp_db):
    import backend.buildings as b
    out = b.building_context([{'property_id': 'x', 'latitude': None, 'longitude': None}])
    assert out['available'] is False
    assert out['buildings'] == []


def test_context_caps_the_property_count(temp_db, osm_payload, monkeypatch):
    import backend.buildings as b
    monkeypatch.setitem(BUILDINGS, 'max_properties', 3)
    props = [{'property_id': f'P{i}', 'latitude': 29.68 + i * 1e-4, 'longitude': -95.47}
             for i in range(20)]
    out = b.building_context(props, session=FakeSession([FakeResponse(200, osm_payload)]))
    assert len(out['buildings']) == 3


# ── Route ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(temp_db):
    import backend.main as main
    with TestClient(main.app) as c:
        yield c


def test_route_requires_properties(client):
    assert client.post("/api/buildings", json={}).status_code == 400
    assert client.post("/api/buildings", json={"properties": []}).status_code == 400


def test_route_returns_a_renderable_answer_even_when_osm_is_unreachable(client, monkeypatch):
    """
    The route must never 500 on scenery. With no stub session it makes real
    calls, so patch the fetch to fail the way a dead Overpass would.
    """
    import backend.buildings as b
    monkeypatch.setattr(b, "fetch_overpass",
                        lambda *a, **k: (_ for _ in ()).throw(
                            b.BuildingFetchError("OpenStreetMap building lookup failed — test")))

    r = client.post("/api/buildings", json={
        "properties": [{"property_id": "P1", "latitude": 29.68, "longitude": -95.47}]})
    assert r.status_code == 200
    body = r.json()
    assert body['available'] is False
    assert body['buildings'][0]['footprint_source'] == 'placeholder'
    assert body['buildings'][0]['ring'][0] == body['buildings'][0]['ring'][-1]


def test_route_serves_matched_footprints(client, monkeypatch, osm_payload):
    import backend.buildings as b
    from pipeline.building_context import normalize_osm_response
    monkeypatch.setattr(b, "fetch_overpass",
                        lambda *a, **k: normalize_osm_response(osm_payload))

    props = _props_on_footprints(osm_payload, n=3)
    r = client.post("/api/buildings", json={"properties": props})
    assert r.status_code == 200
    body = r.json()
    assert body['available'] is True
    assert body['summary']['match_rate'] == 1.0
    for rec in body['buildings']:
        assert rec['height_m'] > 0
        assert rec['height_source'] in ('osm_height', 'osm_levels', 'typology')


def test_route_never_leaks_an_exception_as_a_500(client, monkeypatch):
    import backend.buildings as b
    monkeypatch.setattr(b, "building_context",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/api/buildings", json={
        "properties": [{"property_id": "P1", "latitude": 29.68, "longitude": -95.47}]})
    assert r.status_code == 200
    assert r.json()['available'] is False
