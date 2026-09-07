"""
test_imagery.py — Street View metadata, Solar API, and the structure fields
an adjuster records from street-level imagery.

No live network and no billable call: every Google endpoint is stubbed. The
most important test in this file is the one asserting the Solar API cannot
reach the network while disabled — that flag is the only thing standing
between this repo and a metered invoice.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.config import SOLAR, STREETVIEW


@pytest.fixture
def temp_db(monkeypatch):
    path = Path(tempfile.mktemp(suffix=".db"))
    import backend.database as db
    monkeypatch.setattr(db, "DB_PATH", path)
    import backend.streetview as sv
    import backend.solar as sol
    import backend.buildings as b
    monkeypatch.setattr(sv, "DB_PATH", path)
    monkeypatch.setattr(sol, "DB_PATH", path)
    monkeypatch.setattr(b, "DB_PATH", path)
    db.init_db()
    return path


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({'url': url, 'params': params})
        r = self._responses.pop(0) if self._responses else FakeResponse({}, 500)
        if isinstance(r, Exception):
            raise r
        return r


OK_META = {
    'status': 'OK', 'date': '2025-02', 'pano_id': 'abc123',
    'copyright': '© Google', 'location': {'lat': 29.6866, 'lng': -95.4658},
}

SOLAR_OK = {
    'imageryQuality': 'HIGH',
    'imageryDate': {'year': 2022, 'month': 3, 'day': 26},
    'solarPotential': {
        'wholeRoofStats': {'areaMeters2': 240.5},
        'roofSegmentStats': [
            {'pitchDegrees': 23.0, 'azimuthDegrees': 6.5,
             'planeHeightAtCenterMeters': 21.5, 'stats': {'areaMeters2': 60.0}},
            {'pitchDegrees': 26.9, 'azimuthDegrees': 182.2,
             'planeHeightAtCenterMeters': 21.2, 'stats': {'areaMeters2': 80.0}},
        ],
    },
}


# ── Street View metadata parsing ─────────────────────────────────────────────

def test_ok_metadata_is_parsed():
    from backend.streetview import parse_metadata
    r = parse_metadata(OK_META)
    assert r['available'] is True
    assert r['date'] == '2025-02'
    assert r['pano_id'] == 'abc123'
    assert r['error'] is None


def test_zero_results_is_absence_not_an_error():
    from backend.streetview import parse_metadata
    r = parse_metadata({'status': 'ZERO_RESULTS'})
    assert r['available'] is False
    assert r['error'] is None       # nothing is wrong; there is just no imagery


def test_request_denied_surfaces_as_an_error():
    """A misconfigured key must not look identical to 'no imagery here'."""
    from backend.streetview import parse_metadata
    r = parse_metadata({'status': 'REQUEST_DENIED',
                        'error_message': 'API key not authorized'})
    assert r['available'] is False
    assert r['error'] == 'API key not authorized'


def test_lookup_uses_the_free_metadata_endpoint_only(temp_db, monkeypatch):
    """The billable Street View *image* SKU must never be called."""
    import backend.streetview as sv
    monkeypatch.setattr(sv, "GOOGLE_MAPS_API_KEY", "test-key")
    session = FakeSession([FakeResponse(OK_META)])
    r = sv.lookup(29.6869, -95.4658, session=session)
    assert r['available'] is True
    assert len(session.calls) == 1
    assert session.calls[0]['url'].endswith('/streetview/metadata')


def test_lookup_caches(temp_db, monkeypatch):
    import backend.streetview as sv
    monkeypatch.setattr(sv, "GOOGLE_MAPS_API_KEY", "test-key")
    sv.lookup(29.6869, -95.4658, session=FakeSession([FakeResponse(OK_META)]))
    again = sv.lookup(29.6869, -95.4658, session=FakeSession([]))   # no calls left
    assert again['date'] == '2025-02'


def test_transient_failures_are_not_cached(temp_db, monkeypatch):
    """Caching an outage for 30 days would bury a fixable problem."""
    import backend.streetview as sv
    monkeypatch.setattr(sv, "GOOGLE_MAPS_API_KEY", "test-key")
    bad = sv.lookup(30.0, -95.0, session=FakeSession([OSError("boom")]))
    assert bad['available'] is False
    assert sv.cache_get(30.0, -95.0) is None


def test_missing_key_reports_cleanly(temp_db, monkeypatch):
    import backend.streetview as sv
    monkeypatch.setattr(sv, "GOOGLE_MAPS_API_KEY", None)
    r = sv.lookup(29.68, -95.47)
    assert r['available'] is False and r['status'] == 'NO_KEY'


def test_batch_summarizes_coverage(temp_db, monkeypatch):
    import backend.streetview as sv
    monkeypatch.setattr(sv, "GOOGLE_MAPS_API_KEY", "test-key")
    session = FakeSession([FakeResponse(OK_META),
                           FakeResponse({'status': 'ZERO_RESULTS'})])
    out = sv.lookup_batch([
        {'property_id': 'A', 'latitude': 29.68, 'longitude': -95.47},
        {'property_id': 'B', 'latitude': 29.69, 'longitude': -95.48},
    ], session=session)
    assert out['available'] is True
    assert out['summary']['with_imagery'] == 1
    assert out['properties']['A']['available'] is True
    assert out['properties']['B']['available'] is False


# ── Solar API: the cost guard ────────────────────────────────────────────────

def test_solar_is_disabled_by_default():
    """The shipped default must not be able to bill."""
    assert SOLAR['enabled'] is False


def test_disabled_solar_makes_no_network_call(temp_db, monkeypatch):
    import backend.solar as sol
    monkeypatch.setattr(sol, "GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setitem(SOLAR, 'enabled', False)
    session = FakeSession([FakeResponse(SOLAR_OK)])
    r = sol.lookup(29.6869, -95.4658, session=session)
    assert r['available'] is False
    assert session.calls == []          # the point: nothing left the building


def test_disabled_solar_enrichment_is_a_no_op(temp_db, monkeypatch):
    import backend.solar as sol
    monkeypatch.setitem(SOLAR, 'enabled', False)
    recs = [{'property_id': 'A', 'centroid': (-95.47, 29.68), 'height_m': 3.2}]
    out = sol.enrich_buildings(recs)
    assert out == {'enabled': False, 'calls': 0, 'enriched': 0,
                   'reason': out['reason']}
    assert recs[0]['height_m'] == 3.2   # untouched


def test_a_key_alone_does_not_enable_solar(monkeypatch):
    import backend.solar as sol
    monkeypatch.setattr(sol, "GOOGLE_MAPS_API_KEY", "a-real-looking-key")
    monkeypatch.setitem(SOLAR, 'enabled', False)
    assert sol.enabled() is False


def test_call_budget_caps_enrichment(temp_db, monkeypatch):
    import backend.solar as sol
    monkeypatch.setattr(sol, "GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setitem(SOLAR, 'enabled', True)
    monkeypatch.setitem(SOLAR, 'max_calls_per_request', 3)
    recs = [{'property_id': str(i), 'centroid': (-95.47 + i * 1e-3, 29.68),
             'ground_elev_m': 18.0} for i in range(10)]
    session = FakeSession([FakeResponse(SOLAR_OK)] * 10)
    out = sol.enrich_buildings(recs, session=session)
    assert out['calls'] == 3            # budget respected, not 10


# ── Solar API: parsing and the sea-level trap ────────────────────────────────

def test_roof_planes_are_parsed_largest_first():
    from backend.solar import parse_building_insights
    r = parse_building_insights(SOLAR_OK)
    assert r['plane_count'] == 2
    assert r['roof_planes'][0]['area_m2'] == 80.0    # largest first
    assert r['roof_planes'][0]['pitch_deg'] == 26.9
    assert r['imagery_date'] == '2022-03'
    assert r['quality_ok'] is True


def test_low_quality_imagery_is_flagged():
    from backend.solar import parse_building_insights
    r = parse_building_insights({**SOLAR_OK, 'imageryQuality': 'BASE'})
    assert r['quality_ok'] is False


def test_building_height_subtracts_ground_elevation():
    """
    The datum trap: planeHeightAtCenterMeters is metres above SEA LEVEL. A
    21.5m roof plane on ground at 18m is a 3.5m house, not a 21m one.
    """
    from backend.solar import parse_building_insights, building_height_m
    rec = parse_building_insights(SOLAR_OK)
    assert building_height_m(rec, ground_elev_m=18.0) == 3.5


def test_building_height_refuses_without_ground_elevation():
    from backend.solar import parse_building_insights, building_height_m
    rec = parse_building_insights(SOLAR_OK)
    assert building_height_m(rec, ground_elev_m=None) is None


def test_building_height_rejects_implausible_results():
    from backend.solar import parse_building_insights, building_height_m
    rec = parse_building_insights(SOLAR_OK)
    assert building_height_m(rec, ground_elev_m=25.0) is None   # roof below ground
    assert building_height_m(rec, ground_elev_m=-100.0) is None  # 120m "house"


def test_building_height_refuses_coarse_imagery():
    from backend.solar import parse_building_insights, building_height_m
    rec = parse_building_insights({**SOLAR_OK, 'imageryQuality': 'BASE'})
    assert building_height_m(rec, ground_elev_m=18.0) is None


def test_not_found_is_ordinary_absence(temp_db, monkeypatch):
    """Australia is outside coverage — that must not read as an error."""
    import backend.solar as sol
    monkeypatch.setattr(sol, "GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setitem(SOLAR, 'enabled', True)
    session = FakeSession([FakeResponse({'error': {'status': 'NOT_FOUND'}}, 404)])
    r = sol.lookup(-28.81, 153.28, session=session)
    assert r['available'] is False
    assert 'coverage' in r['reason'].lower()


# ── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(temp_db):
    import backend.main as main
    with TestClient(main.app) as c:
        yield c


def test_streetview_route_requires_properties(client):
    assert client.post("/api/streetview", json={}).status_code == 400


def test_streetview_route_never_500s(client, monkeypatch):
    import backend.streetview as sv
    monkeypatch.setattr(sv, "lookup_batch",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/api/streetview", json={
        "properties": [{"property_id": "A", "latitude": 29.68, "longitude": -95.47}]})
    assert r.status_code == 200
    assert r.json()['available'] is False


def test_imagery_status_reports_the_billing_posture(client):
    r = client.get("/api/imagery-status")
    assert r.status_code == 200
    body = r.json()
    assert body['street_view']['billable'] is False
    assert body['solar']['billable'] is True
    assert body['solar']['available'] is False       # off unless opted in


# ── Structure fields on adjuster feedback ────────────────────────────────────

def test_structure_observations_are_persisted(client):
    r = client.post("/api/property/P1/feedback", json={
        "event_id": "lismore", "agree": False, "original_class": "Remote-Deny",
        "corrected_class": "Dispatch", "note": "raised on piers",
        "first_floor_type": "piers", "storeys_observed": 2, "has_basement": False,
    })
    assert r.status_code == 200
    rows = client.get("/api/events/lismore/feedback").json()['feedback']
    row = [x for x in rows if x['property_id'] == 'P1'][0]
    assert row['first_floor_type'] == 'piers'
    assert row['storeys_observed'] == '2'
    assert row['has_basement'] == '0'


def test_unknown_first_floor_type_is_dropped_not_fatal(client):
    """A typo must not lose the rest of an adjuster's verdict."""
    r = client.post("/api/property/P2/feedback", json={
        "event_id": "lismore", "agree": True, "note": "kept",
        "first_floor_type": "on stilts maybe", "storeys_observed": "not sure",
    })
    assert r.status_code == 200
    rows = client.get("/api/events/lismore/feedback").json()['feedback']
    row = [x for x in rows if x['property_id'] == 'P2'][0]
    assert row['first_floor_type'] == ''
    assert row['storeys_observed'] == ''
    assert row['note'] == 'kept'        # the verdict survived


def test_feedback_without_structure_fields_still_works(client):
    """Every existing caller omits these — they must stay optional."""
    r = client.post("/api/property/P3/feedback", json={
        "event_id": "lismore", "agree": True})
    assert r.status_code == 200
