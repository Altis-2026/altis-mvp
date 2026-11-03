"""
Tests for pipeline/building_context.py — footprint join + height estimation.

The Overpass fixture is 60 real OpenStreetMap buildings from the Meyerland /
Braeswood area of Houston (the Harvey study area in config.HARVEY), so the
join is exercised against genuine residential geometry — irregular rings,
mostly untagged heights — rather than synthetic squares.
"""
import json
from pathlib import Path

import pytest

import building_context as bc
from config import BUILDINGS

FIXTURE = Path(__file__).parent / "fixtures" / "osm_buildings_houston.json"


@pytest.fixture(scope="module")
def houston_footprints():
    payload = json.loads(FIXTURE.read_text())
    return bc.normalize_osm_response(payload)


# ── Geometry primitives ──────────────────────────────────────────────────────

def test_area_of_a_known_square():
    # ~100m x 100m box at the equator, where a degree of longitude is longest.
    d_lat = 100.0 / 110540.0
    d_lon = 100.0 / 111320.0
    ring = [[0, 0], [d_lon, 0], [d_lon, d_lat], [0, d_lat], [0, 0]]
    assert bc.polygon_area_m2(ring) == pytest.approx(10000.0, rel=0.01)


def test_area_is_sign_independent():
    ring = [[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0]]
    assert bc.polygon_area_m2(ring) == pytest.approx(
        bc.polygon_area_m2(list(reversed(ring))), rel=1e-9)


def test_centroid_of_a_rectangle_is_its_middle():
    ring = [[-95.5, 29.7], [-95.4, 29.7], [-95.4, 29.8], [-95.5, 29.8], [-95.5, 29.7]]
    lon, lat = bc.polygon_centroid(ring)
    assert lon == pytest.approx(-95.45, abs=1e-6)
    assert lat == pytest.approx(29.75, abs=1e-6)


def test_centroid_is_precise_for_a_house_sized_ring_at_real_longitudes():
    """
    Regression: a naive shoelace on raw lon/lat loses ~14 m of precision to
    float64 cancellation on a house-sized ring at -95.47°, which would
    mis-join footprints to properties. The centroid must land within
    centimetres of the true centre.
    """
    lat, lon = 29.68, -95.47
    mx, my = bc.meters_per_degree(lat)
    dx, dy = 10.0 / mx, 5.0 / my          # a 20 m x 10 m house
    ring = [[lon - dx, lat - dy], [lon + dx, lat - dy],
            [lon + dx, lat + dy], [lon - dx, lat + dy], [lon - dx, lat - dy]]

    clon, clat = bc.polygon_centroid(ring)
    assert bc.distance_m(lat, lon, clat, clon) < 0.05     # within 5 cm
    assert bc.polygon_area_m2(ring) == pytest.approx(200.0, rel=0.01)


def test_degenerate_ring_falls_back_to_vertex_mean():
    # A collinear "polygon" has zero area — must not divide by zero.
    ring = [[0, 0], [1, 1], [2, 2]]
    lon, lat = bc.polygon_centroid(ring)
    assert (lon, lat) == pytest.approx((1.0, 1.0))


def test_close_ring_is_idempotent():
    ring = [[0, 0], [1, 0], [1, 1]]
    once = bc.close_ring(ring)
    assert once[0] == once[-1]
    assert bc.close_ring(once) == once


def test_point_in_ring():
    ring = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
    assert bc.point_in_ring(1, 1, ring) is True
    assert bc.point_in_ring(3, 1, ring) is False
    assert bc.point_in_ring(1, 3, ring) is False


def test_distance_matches_known_separation():
    # 0.001 deg of latitude is ~110.5 m anywhere.
    assert bc.distance_m(29.7, -95.5, 29.701, -95.5) == pytest.approx(110.5, abs=1.0)


# ── OSM normalization ────────────────────────────────────────────────────────

def test_real_fixture_normalizes(houston_footprints):
    assert len(houston_footprints) >= 50
    for f in houston_footprints:
        assert f['ring'][0] == f['ring'][-1]          # closed
        assert len(f['ring']) >= 4
        assert BUILDINGS['min_footprint_area_m2'] <= f['area_m2'] <= BUILDINGS['max_footprint_area_m2']
        lon, lat = f['centroid']
        assert -96 < lon < -95 and 29 < lat < 30      # inside the Harvey bbox


def test_footprints_outside_the_area_band_are_rejected():
    tiny = {'id': 1, 'geometry': [{'lat': 29.7, 'lon': -95.5},
                                  {'lat': 29.70001, 'lon': -95.5},
                                  {'lat': 29.70001, 'lon': -95.49999},
                                  {'lat': 29.7, 'lon': -95.49999},
                                  {'lat': 29.7, 'lon': -95.5}]}
    assert bc.normalize_osm_element(tiny) is None     # ~1 m² awning

    huge = {'id': 2, 'geometry': [{'lat': 29.70, 'lon': -95.50},
                                  {'lat': 29.70, 'lon': -95.49},
                                  {'lat': 29.71, 'lon': -95.49},
                                  {'lat': 29.71, 'lon': -95.50},
                                  {'lat': 29.70, 'lon': -95.50}]}
    assert bc.normalize_osm_element(huge) is None     # ~1 km² block


def test_malformed_elements_are_skipped_not_raised():
    payload = {'elements': [
        {'id': 1, 'geometry': [{'lat': 29.7, 'lon': -95.5}]},   # too few points
        {'id': 2},                                              # no geometry
        {'id': 3, 'geometry': [{'lat': None, 'lon': None}] * 4},
    ]}
    assert bc.normalize_osm_response(payload) == []


# ── Height ladder ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("12", 12.0), ("12 m", 12.0), ("12.5m", 12.5), ("6,5", 6.5),
    ("40'", 12.19), ("40'6\"", 12.34), ("30 ft", 9.14),
])
def test_parse_osm_height(raw, expected):
    assert bc.parse_osm_height(raw) == pytest.approx(expected, abs=0.02)


@pytest.mark.parametrize("raw", [None, "", "tall", "abc m", "-3", "0"])
def test_unparseable_height_refuses_rather_than_guesses(raw):
    assert bc.parse_osm_height(raw) is None


def test_explicit_height_tag_wins():
    h, src, levels = bc.estimate_height_m({'building': 'house', 'height': '7.5',
                                           'building:levels': '3'}, 200.0)
    assert (h, src) == (7.5, 'osm_height')
    assert levels == pytest.approx(7.5 / BUILDINGS['storey_height_m'], abs=0.01)


def test_levels_tag_used_when_no_height():
    h, src, levels = bc.estimate_height_m({'building': 'house', 'building:levels': '2'}, 200.0)
    assert (h, src, levels) == (2 * BUILDINGS['storey_height_m'], 'osm_levels', 2.0)


def test_typology_fallback_for_untagged_house():
    h, src, levels = bc.estimate_height_m({'building': 'house'}, 200.0)
    assert src == 'typology'
    assert levels == 1.0
    assert h == pytest.approx(BUILDINGS['storey_height_m'])


def test_apartments_are_taller_than_houses():
    house, _, _ = bc.estimate_height_m({'building': 'house'}, 200.0)
    apts, _, _ = bc.estimate_height_m({'building': 'apartments'}, 200.0)
    assert apts > house


def test_large_untagged_footprint_gets_more_storeys():
    small, _, _ = bc.estimate_height_m({'building': 'yes'}, 200.0)
    big, _, _ = bc.estimate_height_m({'building': 'yes'}, 3000.0)
    assert big > small


def test_absurd_osm_height_is_rejected_not_rendered():
    # A house tagged '300' is a unit error, not a skyscraper in a subdivision.
    h, src, _ = bc.estimate_height_m({'building': 'house', 'height': '300'}, 200.0)
    assert src == 'typology'
    assert h < 10


def test_a_tall_but_plausible_storey_count_is_clamped_to_the_cap():
    h, src, levels = bc.estimate_height_m({'building': 'yes', 'building:levels': '100'}, 200.0)
    assert src == 'osm_levels'
    assert levels == BUILDINGS['max_levels']
    assert h == pytest.approx(BUILDINGS['max_levels'] * BUILDINGS['storey_height_m'])


def test_a_nonsense_storey_count_is_discarded_entirely():
    # 500 storeys is a data error, not a tall building: fall back to typology
    # rather than rendering a clamped-but-still-wrong tower.
    _, src, _ = bc.estimate_height_m({'building': 'yes', 'building:levels': '500'}, 200.0)
    assert src == 'typology'


# ── Footprint ↔ property join ────────────────────────────────────────────────

def test_point_inside_a_footprint_matches_at_zero_distance(houston_footprints):
    target = houston_footprints[0]
    lon, lat = target['centroid']
    match, dist = bc.match_footprint(lat, lon, houston_footprints)
    assert match['id'] == target['id']
    assert dist == 0.0


def test_far_away_point_is_rejected(houston_footprints):
    # Middle of the Gulf of Mexico — nearest footprint is kilometres away.
    match, dist = bc.match_footprint(28.0, -94.0, houston_footprints)
    assert match is None
    assert dist > BUILDINGS['match_radius_m']


def test_match_radius_is_enforced(houston_footprints):
    target = houston_footprints[0]
    lon, lat = target['centroid']
    # Nudge ~150m north: beyond the radius, so no match even though one is near.
    offset_lat = lat + 150.0 / 110540.0
    match, dist = bc.match_footprint(offset_lat, lon, houston_footprints)
    if match is not None:
        # Only acceptable if some *other* footprint genuinely sits there.
        assert dist <= BUILDINGS['match_radius_m']
    else:
        assert dist > BUILDINGS['match_radius_m']


def test_nearest_of_two_candidates_wins():
    near = {'id': 'near', 'ring': [[0, 0], [0.0001, 0], [0.0001, 0.0001], [0, 0.0001], [0, 0]],
            'centroid': (0.00005, 0.00005), 'area_m2': 100.0, 'tags': {'building': 'house'}}
    far = {'id': 'far', 'ring': [[0.001, 0.001], [0.0011, 0.001], [0.0011, 0.0011],
                                 [0.001, 0.0011], [0.001, 0.0011]],
           'centroid': (0.00105, 0.00105), 'area_m2': 100.0, 'tags': {'building': 'house'}}
    match, _ = bc.match_footprint(0.0002, 0.0002, [far, near])
    assert match['id'] == 'near'


# ── Per-property records ─────────────────────────────────────────────────────

def test_unmatched_property_gets_a_labelled_placeholder():
    rec = bc.building_for_property(
        {'property_id': 'P1', 'latitude': 28.0, 'longitude': -94.0}, [])
    assert rec['footprint_source'] == 'placeholder'
    assert rec['height_source'] == 'default'
    assert rec['ring'][0] == rec['ring'][-1]
    assert rec['area_m2'] == pytest.approx(
        BUILDINGS['placeholder_width_m'] * BUILDINGS['placeholder_depth_m'], rel=0.01)


def test_placeholder_footprint_has_the_configured_size():
    ring = bc.placeholder_footprint(29.7, -95.5)
    area = bc.polygon_area_m2(ring)
    expected = BUILDINGS['placeholder_width_m'] * BUILDINGS['placeholder_depth_m']
    assert area == pytest.approx(expected, rel=0.02)


def test_matched_property_reports_real_provenance(houston_footprints):
    target = houston_footprints[0]
    lon, lat = target['centroid']
    rec = bc.building_for_property(
        {'property_id': 'P1', 'latitude': lat, 'longitude': lon}, houston_footprints)
    assert rec['footprint_source'] == 'osm'
    assert rec['osm_id'] == target['id']
    assert rec['match_distance_m'] == 0.0
    assert rec['height_m'] > 0
    assert rec['height_source'] in ('osm_height', 'osm_levels', 'typology')


def test_batch_summary_reports_match_rate(houston_footprints):
    props = []
    for i, f in enumerate(houston_footprints[:10]):
        lon, lat = f['centroid']
        props.append({'property_id': f'P{i}', 'latitude': lat, 'longitude': lon})
    # Two properties that cannot match anything.
    props.append({'property_id': 'far1', 'latitude': 28.0, 'longitude': -94.0})
    props.append({'property_id': 'far2', 'latitude': 28.1, 'longitude': -94.1})

    records, summary = bc.buildings_for_properties(props, houston_footprints)
    assert len(records) == 12
    assert summary['footprints_matched'] == 10
    assert summary['match_rate'] == pytest.approx(10 / 12, abs=0.01)
    assert summary['candidates'] == len(houston_footprints)
    assert sum(summary['height_sources'].values()) == 12


def test_ungeocoded_properties_are_skipped_not_rendered(houston_footprints):
    props = [{'property_id': 'x', 'latitude': None, 'longitude': None},
             {'property_id': 'y', 'latitude': 29.7, 'longitude': -95.5}]
    records, summary = bc.buildings_for_properties(props, houston_footprints)
    assert len(records) == 1
    assert summary['requested'] == 2
    assert summary['rendered'] == 1


def test_every_record_is_renderable(houston_footprints):
    """Whatever the input, the globe must never receive an unusable ring."""
    props = [{'property_id': str(i), 'latitude': 29.68 + i * 0.001, 'longitude': -95.47}
             for i in range(20)]
    records, _ = bc.buildings_for_properties(props, houston_footprints)
    for r in records:
        assert len(r['ring']) >= 4
        assert r['ring'][0] == r['ring'][-1]
        assert r['height_m'] > 0
        assert r['footprint_source'] in ('osm', 'placeholder')
