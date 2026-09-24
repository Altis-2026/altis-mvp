"""
test_physics_intel.py — The physics & intelligence layer (Phases A–D).

Everything here is offline: synthetic terrain, synthetic tiles, synthetic
TIFFs, the vendored HURDAT2 subset. The hydraulic router is held to exact
physical invariants (lake at rest, mass conservation, non-negative depth,
Froude cap) rather than to plausible-looking numbers.
"""
import io
from datetime import date, datetime, timezone

import numpy as np
import pytest

from pipeline import access, flags as F, habitability, structure_depth as SD
from pipeline.cogread import CogReader, bytes_fetcher, lzw_decode
from pipeline.hydraulic_route import route
from pipeline.terrain import (Grid, decode_terrarium, drainage, flow_accumulation, hand,
                              lonlat_to_tile, mosaic_tiles, tile_to_lonlat)
from pipeline.water_surface import observed_depth_grid
from pipeline import wind_field as WF


# ── COG reader ───────────────────────────────────────────────────────────────

def _lzw_encode(data: bytes) -> bytes:
    """Minimal TIFF-LZW encoder (MSB-first, early change) for round-trip tests."""
    table = {bytes([i]): i for i in range(256)}
    nxt, width = 258, 9
    out_bits, nbits = 0, 0
    buf = bytearray()

    def emit(code):
        nonlocal out_bits, nbits
        out_bits = (out_bits << width) | code
        nbits += width
        while nbits >= 8:
            nbits -= 8
            buf.append((out_bits >> nbits) & 0xFF)
    emit(256)
    w = b''
    for b in data:
        wc = w + bytes([b])
        if wc in table:
            w = wc
            continue
        emit(table[w])
        table[wc] = nxt
        nxt += 1
        # The decoder learns each entry one code late and widens one code
        # early; together that is exactly "widen once nxt reaches 2^width".
        if nxt >= (1 << width) and width < 12:
            width += 1
        if nxt >= 4093:
            emit(256)
            table = {bytes([i]): i for i in range(256)}
            nxt, width = 258, 9
        w = bytes([b])
    if w:
        emit(table[w])
    emit(257)
    if nbits:
        buf.append((out_bits << (8 - nbits)) & 0xFF)
    return bytes(buf)


def test_lzw_roundtrip():
    rng = np.random.default_rng(1)
    for data in (b'', b'TOBEORNOTTOBEORTOBEORNOT' * 50, bytes(rng.integers(0, 6, 20000, dtype=np.uint8))):
        assert lzw_decode(_lzw_encode(data)) == data


@pytest.mark.parametrize('dtype,predictor', [(np.float32, 1), (np.float32, 3), (np.int16, 2)])
def test_cog_window_matches_source(dtype, predictor):
    tifffile = pytest.importorskip('tifffile')
    rng = np.random.default_rng(3)
    arr = rng.normal(100, 30, (40, 56)).astype(dtype)
    buf = io.BytesIO()
    tifffile.imwrite(buf, arr, tile=(16, 16), compression='zlib',
                     predictor=predictor if predictor != 1 else None,
                     extratags=[(33550, 12, 3, (0.05, 0.05, 0.0)),
                                (33922, 12, 6, (0, 0, 0, 150.0, -20.0, 0))])
    r = CogReader(bytes_fetcher(buf.getvalue()))
    win, (west, north, rx, ry) = r.read_window(150.3, -21.2, 151.1, -20.6, pad=0)
    c0 = int(round((west - 150.0) / 0.05))
    r0 = int(round((-20.0 - north) / 0.05))
    assert np.allclose(win, arr[r0:r0 + win.shape[0], c0:c0 + win.shape[1]])
    full, _ = r.read_all()
    assert np.allclose(full, arr)


def test_cog_rejects_unsupported():
    with pytest.raises(ValueError):
        CogReader(bytes_fetcher(b'NOTATIFF' + b'\0' * 64))


# ── Terrain ──────────────────────────────────────────────────────────────────

def test_terrarium_decode():
    rgb = np.array([[[128, 0, 0], [128, 100, 128]]], np.uint8)
    assert np.allclose(decode_terrarium(rgb), [[0.0, 100.5]])


def test_tile_math_roundtrip():
    for lon, lat in [(-95.4, 29.7), (153.3, -28.8), (0.0, 0.0)]:
        x, y = lonlat_to_tile(lon, lat, 12)
        lo, la = tile_to_lonlat(x, y, 12)
        assert abs(lo - lon) < 1e-9 and abs(la - lat) < 1e-9


def test_mosaic_reproduces_a_smooth_field():
    # Elevation = longitude-linear ramp; the mosaic must return the same ramp.
    def get_tile(z, x, y):
        px = np.arange(256) + 0.5
        lon = (x + px / 256) / 2 ** z * 360 - 180
        return np.tile(lon * 10.0, (256, 1))
    g = mosaic_tiles([-95.50, 29.60, -95.40, 29.70], 13, get_tile)
    lon, lat = g.lonlat(np.array([10, 50]), np.array([20, 80]))
    assert np.allclose(g.sample(lon, lat), lon * 10.0, atol=0.02)


def test_grid_sample_and_bounds():
    g = Grid(np.arange(12.0).reshape(3, 4), 0.0, 3.0, 1.0, 1.0)
    assert g.sample([0.5], [2.5])[0] == 0.0
    assert np.isnan(g.sample([10.0], [1.0])[0])
    assert g.sample([1.0], [2.5])[0] == pytest.approx(0.5)


def test_drainage_tree_and_accumulation():
    # Plane tilting east: every cell's water ends at the east edge.
    z = np.tile(np.linspace(10, 0, 8), (5, 1))
    filled, parent, order = drainage(z)
    seen = set()
    for i in order:                       # parents always come first
        if parent[i] >= 0:
            assert parent[i] in seen
        seen.add(i)
    acc = flow_accumulation(parent, order)
    assert acc.sum() >= z.size
    assert acc.max() <= z.size


def test_drainage_fills_pits():
    z = np.full((7, 7), 5.0)
    z[3, 3] = 1.0                         # a pit
    z[0, :] = 0.0                         # outlet along the north edge
    filled, _, _ = drainage(z)
    assert filled[3, 3] >= 5.0 - 1e-9


def test_hand_in_a_valley():
    x = np.abs(np.arange(41) - 20.0)      # V-valley, channel at column 20
    z = np.tile(x * 0.5, (60, 1)) + np.linspace(3, 0, 60)[:, None]    # gently falls south
    h, acc, stream = hand(z, (30.0, 30.0), stream_area_km2=0.2)
    assert stream[:, 20].sum() > 20
    assert h[30, 20] == pytest.approx(0.0, abs=1e-6)
    assert h[30, 30] == pytest.approx(5.0, abs=0.6)   # 10 cells × 0.5 m up the slope


# ── Wind ─────────────────────────────────────────────────────────────────────

def test_hurdat_subset_parses():
    ian = WF.load_storm('AL092022')
    assert ian['name'] == 'Ian'
    land = [f for f in ian['fixes'] if f['record'] == 'L']
    assert any(abs(f['lat'] - 26.7) < 0.05 and f['vmax'] == 130 for f in land)
    assert ian['fixes'][0]['time'].tzinfo is not None


def test_parse_tolerates_malformed_lines():
    txt = ("AL012099,  TEST,  3,\n"
           "20990801, 0000,  , TS, 25.0N,  80.0W,  40, 1000,   60,   60,   60,   60,    0,    0,    0,    0,    0,    0,    0,    0, -999\n"
           "garbage line\n"
           "20990801, 0600,  , TS, 25.5N    80.5W,  45,  998,   60,   60,   60,   60,    0,    0,    0,    0,    0,    0,    0,    0, -999\n")
    st = WF.parse_hurdat2(txt)['AL012099']
    assert len(st['fixes']) == 2
    assert st['fixes'][1]['lat'] == 25.5 and st['fixes'][1]['lon'] == -80.5


def test_willoughby_rmax_reasonable():
    r = WF.willoughby_rmax_nm(120, 26.0)
    assert 10 < r < 30


def test_wind_profile_properties():
    fix = {'time': datetime(2020, 1, 1, tzinfo=timezone.utc), 'lat': 25.0, 'lon': -80.0, 'vmax': 100.0,
           'r34': [150, 150, 150, 150], 'r50': [80, 80, 80, 80], 'r64': [40, 40, 40, 40], 'rmw': 20}
    at = lambda nm: WF.wind_at(fix, 25.0 + nm / 60.0, -80.0)
    assert at(20) == pytest.approx(100, rel=0.03)            # eyewall
    assert at(5) < at(20)                                     # calm eye
    assert 30 <= at(150) <= 38                                # passes near the analysed R34
    assert at(300) < 34                                       # outside R34 → below gale
    assert at(40) > at(80) > at(150)                          # monotonic decay


def test_property_wind_history_and_surface_factor():
    ian = WF.load_storm('AL092022')
    marine = WF.property_wind_history(ian, 26.93, -82.05)
    land = WF.property_wind_history(ian, 26.93, -82.05, surface_factor=0.85)
    assert marine['peak_kt'] >= 96                            # major-hurricane winds at Punta Gorda
    assert land['peak_kt'] == pytest.approx(marine['peak_kt'] * 0.85, rel=0.01)
    far = WF.property_wind_history(WF.load_storm('AL092017'), 32.78, -96.8)   # Dallas, Harvey
    assert far['peak_kt'] < 34


# ── Flags ────────────────────────────────────────────────────────────────────

WIND_HU = {'peak_kt': 110.0, 'category': 'Cat 3 hurricane-force', 'peak_time': '2022-09-28T20:00:00+00:00',
           'hours_ge_34kt': 20, 'hours_ge_64kt': 6, 'closest_nm': 5}


def test_wind_vs_water_verdicts():
    assert F.wind_vs_water(None, 3.0, 2.0) == ('no_tc', None)
    v, f = F.wind_vs_water(WIND_HU, 4.0, 3.0)
    assert v == 'concurrent' and f['level'] == 'alert'
    v, f = F.wind_vs_water(WIND_HU, 0.0, None)
    assert v == 'wind' and 'wind' in f['title'].lower()
    v, f = F.wind_vs_water(WIND_HU, 0.0, None, water_uncertain=True)
    assert v == 'wind_possible_water' and f['level'] == 'alert'
    weak = dict(WIND_HU, peak_kt=30.0, category='Below gale')
    v, f = F.wind_vs_water(weak, 3.0, 2.0)
    assert v == 'water'


def test_transient_miss_requires_dry_rain_low_late():
    rain = {'max_3day_mm': 400.0, 'total_mm': 500.0, 'peak_day': '2017-08-27'}
    assert F.transient_miss(False, rain, 0.5, 36) is None                   # SAR saw water
    f = F.transient_miss(True, rain, 0.5, 36)
    assert f['level'] == 'alert' and f['action'] == 'hold_remote_deny'
    assert F.transient_miss(True, rain, 12.0, 36) is None                   # high ground
    assert F.transient_miss(True, rain, 0.5, 2) is None                     # pass during the peak
    assert F.transient_miss(True, {'max_3day_mm': 20.0}, 0.5, 36) is None   # light rain
    heavy = {'max_3day_mm': 120.0}
    assert F.transient_miss(True, heavy, 1.0, 36)['level'] == 'caution'


def test_transient_miss_surge_bands():
    f = F.transient_miss(True, None, 5.0, 90, WIND_HU, ground_asl_m=1.2)
    assert f['level'] == 'alert' and f['evidence']['surge_exposed']
    f = F.transient_miss(True, None, 5.0, 90, WIND_HU, ground_asl_m=2.6)
    assert f['level'] == 'caution'
    assert F.transient_miss(True, None, 5.0, 90, WIND_HU, ground_asl_m=8.0) is None


def test_prior_water():
    assert F.prior_water(0.0, 5.0, True) is None
    assert F.prior_water(30.0, 60.0, True)['level'] == 'caution'
    assert F.prior_water(30.0, 60.0, False)['level'] == 'info'
    assert F.prior_water(None, None, True) is None


def test_access_and_floor_clear_flags():
    assert F.access({'status': 'accessible'}) is None
    assert F.access({'status': 'isolated', 'threshold_m': 0.3})['level'] == 'alert'
    assert F.access({'status': 'street_flooded', 'threshold_m': 0.3, 'dry_point_m': 80})['level'] == 'caution'
    assert F.floor_clear(2.0, -1.0, 'piers')['code'] == 'FLOOR_CLEAR'
    assert F.floor_clear(2.0, 1.0, 'slab') is None


def test_sort_flags():
    fl = F.sort_flags([{'level': 'info', 'code': 'a'}, None, {'level': 'alert', 'code': 'b'}])
    assert [f['code'] for f in fl] == ['b', 'a']


# ── Road access ──────────────────────────────────────────────────────────────

def _grid_roads():
    # A ladder: two arterials (y=0, y=0.02) joined by residential rungs.
    ways = [{'id': 1, 'highway': 'primary', 'coords': [[x / 100, 0.0] for x in range(0, 11)]},
            {'id': 2, 'highway': 'primary', 'coords': [[x / 100, 0.02] for x in range(0, 11)]},
            {'id': 3, 'highway': 'residential', 'coords': [[0.05, 0.0], [0.05, 0.01], [0.05, 0.02]]},
            {'id': 4, 'highway': 'residential', 'coords': [[0.05, 0.01], [0.07, 0.01]]},
            {'id': 5, 'highway': 'residential', 'coords': [[0.2, 0.2], [0.21, 0.2]]}]   # fragment
    return ways


def test_access_statuses():
    bbox = [0.0, -0.001, 0.1, 0.021]
    dry = lambda lo, la: np.zeros(len(lo))
    props = [{'property_id': 'A', 'lon': 0.07, 'lat': 0.0101}]
    assert access.assess(_grid_roads(), dry, props, bbox)['A']['status'] == 'accessible'
    # Flood both halves of the rung but not the junction: the spur and the
    # junction stay dry, yet no dry route reaches either arterial.
    def wet_rung(lo, la):
        lo, la = np.asarray(lo), np.asarray(la)
        on_rung = np.abs(lo - 0.05) < 1e-4
        away = ((la > 0.001) & (la < 0.008)) | ((la > 0.012) & (la < 0.019))
        return np.where(on_rung & away, 1.0, 0.0)
    assert access.assess(_grid_roads(), wet_rung, props, bbox)['A']['status'] == 'isolated'
    street = lambda lo, la: np.where(np.asarray(lo) > 0.055, 1.0, 0.0) * (np.abs(np.asarray(la) - 0.01) < 1e-4)
    assert access.assess(_grid_roads(), street, props, bbox)['A']['status'] == 'street_flooded'
    far = [{'property_id': 'F', 'lon': 0.205, 'lat': 0.2}]
    r = access.assess(_grid_roads(), dry, far, [0.0, -0.001, 0.3, 0.3])
    assert r['F']['status'] == 'unknown'          # disconnected OSM fragment is not a flood finding


# ── Structure depth & habitability ───────────────────────────────────────────

def test_structure_depth():
    sd = SD.structure_depth(3.0, 0.5, 'slab')
    assert sd['depth_above_floor_ft'] == pytest.approx(2.0)
    assert sd['foundation_observed']
    sd = SD.structure_depth(3.0, 0.5, 'piers')
    assert sd['depth_above_floor_ft'] < 0 and SD.damage_depth_ft(sd) == 0.0
    sd = SD.structure_depth(3.0, 0.5, None)
    assert not sd['foundation_observed'] and sd['depth_above_floor_ci_ft'] > 1.5
    assert SD.structure_depth(0.5, 0.1, 'basement')['basement_water']
    assert SD.grade_correction_m(10.0, 12.0) == -0.5


def test_habitability_bands():
    assert habitability.estimate(None) is None
    assert habitability.estimate(-1.0)['displacement'] is False
    small = habitability.estimate(0.3, 10)
    big = habitability.estimate(5.0, 96)
    assert small['days_high'] < big['days_low']
    assert big['accommodation'].startswith('long-let')


# ── Observed water surface ───────────────────────────────────────────────────

def test_observed_depth_grid():
    z = np.tile(np.linspace(0, 10, 50), (50, 1))          # slope rising east
    dem = Grid(z, 0.0, 0.05, 0.001, 0.001)
    lons = np.array([0.005, 0.045])
    lats = np.array([0.025, 0.025])
    depths = np.array([1.0, 0.0])                        # wet low west, dry high east
    g = observed_depth_grid(dem, lons, lats, depths, radius_m=3000)
    assert g.data[25, 5] > 0.5
    assert g.data[25, 45] == 0.0
    assert (g.data >= 0).all()


# ── Hydraulic router invariants ──────────────────────────────────────────────

def test_router_lake_at_rest():
    y, x = np.mgrid[0:30, 0:30]
    z = ((x - 15) ** 2 + (y - 15) ** 2) * 0.02
    h0 = np.maximum(0, 2.0 - z)
    r = route(z, 40, 40, 3 * 3600, h0=h0, dt_max=30)
    assert np.abs(r['h'] - h0).max() < 1e-9


def test_router_mass_conservation_dambreak():
    z = np.tile(np.linspace(8, 0, 50), (12, 1))
    h0 = np.zeros_like(z)
    h0[:, :8] = 2.0
    r = route(z, 25, 25, 2 * 3600, h0=h0, manning=0.03, dt_max=10)
    v0 = h0.sum() * 625
    assert abs(r['volume_final'] + r['volume_out'] - v0) / v0 < 1e-10
    assert (r['h'] >= 0).all()
    assert r['volume_out'] > 0                              # water left through the low edge


def test_router_rain_in_closed_bowl_and_sea():
    y, x = np.mgrid[0:24, 0:24]
    z = ((x - 12) ** 2 + (y - 12) ** 2) * 0.01
    z[0, :] = z[-1, :] = z[:, 0] = z[:, -1] = np.nan       # walls
    r = route(z, 50, 50, 4 * 3600, rain_rate=lambda t: 1e-5 if t < 3600 else 0.0, dt_max=30)
    assert abs(r['volume_in'] - r['volume_final']) / r['volume_in'] < 1e-10
    zz = np.tile(3 - 0.1 * np.arange(40), (10, 1))
    zz[:, 30:] = -15
    r = route(zz, 100, 100, 6 * 3600, rain_rate=lambda t: 1e-5 if t < 3600 else 0.0,
              sea_mask=zz < 0, dt_max=30)
    assert abs(r['volume_in'] - r['volume_out'] - r['volume_final']) / r['volume_in'] < 1e-9
    assert r['volume_out'] > 0.3 * r['volume_in']


def test_router_records_and_snapshot():
    z = np.tile(np.linspace(2, 0, 20), (6, 1))
    r = route(z, 30, 30, 7200, rain_rate=lambda t: 2e-5, record_every_s=1800,
              record_cells=(np.array([3]), np.array([10])), snapshot_at_s=3600, dt_max=20)
    assert len(r['times']) == 5 and r['series'].shape == (5, 1)
    assert r['h_snapshot'] is not None
    assert (r['h_max'] >= r['h'] - 1e-12).all()


# ── Rain summary / merging ───────────────────────────────────────────────────

def test_rain_summary():
    from backend.intel import rain_summary
    days = [date(2017, 8, d) for d in range(24, 31)]
    vals = [0, 50, 100, 300, 250, 10, float('nan')]
    s = rain_summary(list(zip(days, vals)), end_hour=12)
    assert s['max_3day_mm'] == 650.0 and s['peak_day'] == '2017-08-27'
    assert s['peak_end_utc'].startswith('2017-08-27T12:00')
    assert rain_summary([]) is None


def test_merge_intel_rows_applies_hold_and_timing():
    from backend.intel import merge_intel_rows
    intel = {'event': {'router': {'times': ['2022-02-21T00:00:00+00:00', '2022-02-21T03:00:00+00:00'],
                                  'skill_at_pass': {'csi': 0.6}}},
             'properties': {'P1': {'class_override': {'from': 'Remote-Deny', 'to': 'Review', 'reason': 'held'},
                                   'hydrograph': {'depth_ft': [0, 1]}}}}
    rows = merge_intel_rows([{'property_id': 'P1', 'impact_class': 'Remote-Deny'},
                             {'property_id': 'P2', 'impact_class': 'Dispatch'}], intel,
                            {'Review': '#FFB347'})
    assert rows[0]['impact_class'] == 'Review' and rows[0]['original_class'] == 'Remote-Deny'
    assert rows[0]['intel']['hydrograph']['step_h'] == 3.0
    assert rows[0]['intel']['hydrograph']['skill_csi'] == 0.6
    assert 'intel' not in rows[1]


def test_inherit_from_event():
    from backend.intel import inherit_from_event
    ev_rows = [{'property_id': 'E1', 'latitude': 29.7, 'longitude': -95.4},
               {'property_id': 'E2', 'latitude': 29.8, 'longitude': -95.5}]
    intel = {'version': 'x', 'properties': {'E1': {'flags': []}, 'E2': {'flags': [1]}}}
    out = inherit_from_event([{'property_id': 'P', 'latitude': 29.701, 'longitude': -95.401},
                              {'property_id': 'Far', 'latitude': 31.0, 'longitude': -97.0}], ev_rows, intel)
    assert out['properties']['P']['inherited_from']['property_id'] == 'E1'
    assert 'Far' not in out['properties']


# ── Regression tests for the two router bugs found in the Lismore bake ───────

def test_d4_conditioning_opens_diagonal_valleys():
    """
    A valley that connects only diagonally (legal for an 8-neighbour fill) is
    a dam to a 4-neighbour flow model. Before d4_conditioned(), water stood
    20 m deep behind such links; afterwards every cell must drain along
    orthogonal steps that never climb.
    """
    from pipeline.terrain import d4_conditioned, drainage
    z = np.full((9, 9), 50.0)
    for k in range(9):                      # a diagonal channel from (0,0) to (8,8)
        z[k, k] = 10.0 - k
    z[8, 8] = 0.0                           # outlet at the corner edge
    out = d4_conditioned(z)
    _, parent4, order4 = drainage(out, connectivity=4)
    h, w = out.shape
    for i in order4:
        p = parent4[i]
        if p >= 0:
            r, c = divmod(int(i), w)
            pr, pc = divmod(int(p), w)
            assert abs(r - pr) + abs(c - pc) == 1          # orthogonal link
            assert out.ravel()[p] <= out.ravel()[i] + 1e-9  # never uphill
    # The channel itself was not raised to the ridge height.
    assert out[4, 4] < 20.0


def test_router_drains_a_diagonal_valley_after_conditioning():
    from pipeline.terrain import d4_conditioned
    z = np.full((12, 12), 30.0)
    for k in range(12):
        z[k, k] = 12.0 - k
    zc = d4_conditioned(z)
    r = route(zc, 100, 100, 6 * 3600, rain_rate=lambda t: 2e-5 if t < 3600 else 0.0, dt_max=30)
    assert r['h'].max() < 1.0                               # no 20-m ponds behind diagonal links
    assert r['volume_out'] > 0.5 * r['volume_in']


def test_sea_mask_only_open_ocean():
    from backend.routing import sea_mask
    z = np.full((6, 8), 5.0)
    z[:, 6:] = -10.0                                        # open sea on the east edge
    z[2, 2] = -1.0                                          # an inland depression below sea level
    m = sea_mask(z)
    assert m[:, 6:].all() and not m[2, 2] and m.sum() == 12
