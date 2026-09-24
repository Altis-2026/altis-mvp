"""
test_intel_api.py — Endpoints and models of the intelligence layer
(Phases A–F), fully offline: a temp database, a synthetic intel record, a
tiny synthetic susceptibility model, and a fake HTTP session.
"""
import json
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    path = Path(tempfile.mktemp(suffix='.db'))
    import backend.database as db
    monkeypatch.setattr(db, 'DB_PATH', path)
    db.init_db()
    from backend.main import app
    return TestClient(app)


INTEL_ROW = {
    'flags': [{'code': 'TRANSIENT_MISS', 'level': 'alert', 'title': 'Transient flooding likely missed by SAR',
               'detail': 'x', 'evidence': {'rain_3day_max_mm': 700.0, 'hand_m': 0.4}, 'action': 'hold_remote_deny'}],
    'class_override': {'from': 'Remote-Deny', 'to': 'Review', 'reason': 'held'},
    'structure': {'depth_at_structure_ft': 0.0, 'floor_height_ft': 1.0, 'depth_above_floor_ft': -1.0,
                  'depth_above_floor_ci_ft': 1.5, 'foundation': 'assumed slab-on-grade', 'foundation_observed': False,
                  'basement_water': False, 'grade_correction_ft': 0.0},
    'structure_peak': None, 'damage_depth_ft': 0.0, 'peril': 'neither', 'wind': None,
    'rain': {'total_mm': 800.0, 'max_3day_mm': 700.0, 'peak_day': '2017-08-27'},
    'terrain': {'ground_asl_m': 15.0, 'hand_m': 0.4}, 'jrc_occurrence_pct': 0.0,
    'access': {'status': 'accessible', 'threshold_m': 0.3}, 'sar_lag_hours': 36.4,
    'hydrograph': None, 'habitability': None,
}


# ── Flag feedback ────────────────────────────────────────────────────────────

def test_flag_feedback_roundtrip(client):
    r = client.post('/api/property/P1/flag-feedback',
                    json={'event_id': 'harvey', 'flag_code': 'TRANSIENT_MISS', 'verdict': 'confirmed'})
    assert r.status_code == 200
    assert r.json()['summary']['TRANSIENT_MISS']['confirmed'] == 1
    bad = client.post('/api/property/P1/flag-feedback', json={'flag_code': 'NOPE', 'verdict': 'confirmed'})
    assert bad.status_code == 400
    bad = client.post('/api/property/P1/flag-feedback', json={'flag_code': 'ACCESS', 'verdict': 'maybe'})
    assert bad.status_code == 400


# ── Triage decisions (pure) ──────────────────────────────────────────────────

def _row(**kw):
    base = {'property_id': 'P1', 'impact_class': 'Review', 'confidence_score': 80, 'max_depth_ft': 0.0,
            'depth_ci_ft': 0.5, 'intel': dict(INTEL_ROW), 'original_class': 'Remote-Deny'}
    base.update(kw)
    return base


def test_route_decisions():
    from backend import triage_api as T
    r = T.route(_row())
    assert r['decision'] == 'desk' and any('Held back' in x for x in r['reasons'])
    ok = _row(impact_class='Remote-Approve', confidence_score=85, intel={**INTEL_ROW, 'flags': []})
    assert T.route(ok)['decision'] == 'fast-track'
    iso = _row(impact_class='Dispatch', intel={**INTEL_ROW, 'access': {'status': 'isolated'}})
    out = T.route(iso)
    assert out['decision'] == 'field' and out['constraints']


def test_emergency_and_habitability():
    from backend import triage_api as T
    from pipeline.habitability import estimate
    wet = dict(INTEL_ROW, structure={**INTEL_ROW['structure'], 'depth_above_floor_ft': 2.5},
               habitability=estimate(2.5, 48))
    e = T.emergency(_row(intel=wet), per_diem_usd=200)
    assert e['eligible'] and e['suggested_advance_usd'] > 0
    assert T.emergency(_row(intel={**INTEL_ROW, 'flags': []}))['eligible'] is False
    assert T.habitability(_row(intel=wet))['displacement']


def test_consistency_outliers():
    from backend import triage_api as T
    dry = _row(intel={**INTEL_ROW, 'flags': []})
    c = T.consistency(dry, 'flood', 4.0, True)
    assert c['outlier'] and c['agreement_score'] < 60
    # The transient flag widens what is consistent: not an outlier.
    c2 = T.consistency(_row(), 'flood', 4.0, True)
    assert not c2['outlier']
    w = T.consistency(_row(intel={**INTEL_ROW, 'flags': [], 'wind': {'peak_kt': 20.0}}), 'wind')
    assert any('kt' in n for n in w['notes'])


def test_silent_claims_ranking():
    from backend import triage_api as T
    rows = [_row(property_id='A'),                                            # transient alert
            _row(property_id='B', impact_class='Dispatch', max_depth_ft=5.0,
                 intel={**INTEL_ROW, 'flags': []}),
            _row(property_id='C', impact_class='Remote-Deny', intel={**INTEL_ROW, 'flags': []})]
    s = T.silent(rows, ['B'])
    assert [x['property_id'] for x in s['silent']] == ['A']
    assert s['expected_losses'] == 2 and s['reported'] == 1


# ── Evidence pack ────────────────────────────────────────────────────────────

def test_evidence_pack_builds_pdf_without_network():
    from backend.evidence_pack import build_evidence_pack, digest
    row = {'property_id': 'P1', 'address': '1 Test St, Houston, TX, 77096', 'impact_class': 'Review',
           'original_class': 'Remote-Deny', 'confidence_score': 70, 'max_depth_ft': 0.0, 'depth_ci_ft': 1.0,
           'pct_flooded': 0.0, 'latitude': 29.69, 'longitude': -95.46,
           'confidence_factors': json.dumps([{'factor': 'SAR recency', 'delta': 8, 'reason': 'fresh'}]),
           'intel': {**INTEL_ROW, 'hydrograph': {'depth_ft': [0, 1, 2, 1], 't0': '2022-02-27T00:00:00+00:00',
                                                 'step_h': 3.0, 'pass_time': '2022-02-27T06:00:00+00:00',
                                                 'obs_depth_ft': 1.0, 'peak_depth_ft': 2.0,
                                                 'peak_time': '2022-02-27T06:00:00+00:00',
                                                 'hours_wet': 9, 'basis': 'anchored to SAR at the pass'}}}
    pdf = build_evidence_pack(row, {'id': 'harvey', 'label': 'Hurricane Harvey', 'sub': 'TX'},
                              {'products': {'rain': 'Stage IV'}}, include_site_map=False)
    assert pdf[:4] == b'%PDF' and len(pdf) > 4000
    assert digest({'a': 1}) == digest({'a': 1}) != digest({'a': 2})


def test_evidence_endpoint_404_for_unknown(client):
    r = client.get('/api/property/NOPE/evidence-pack', params={'event_id': 'harvey'})
    assert r.status_code in (404,)


# ── Validation metrics & open-data search ────────────────────────────────────

def test_contingency_and_wilson():
    from pipeline.validation_metrics import agreement, contingency, otsu_threshold, reference_water_mask, wilson
    c = contingency([1, 1, 0, 0, 1], [1, 0, 0, 1, 1])
    assert c['hits'] == 2 and c['misses'] == 1 and c['false_alarms'] == 1 and c['csi'] == 0.5
    lo, hi = wilson(8, 10)
    assert 0 < lo < 0.8 < hi <= 1
    rng = np.random.default_rng(0)
    img = np.where(np.arange(100)[None, :] < 40, -22.0, -8.0) + rng.normal(0, 1, (60, 100))
    mask, thr = reference_water_mask(img)
    assert -20 < thr < -10 and mask[:, :35].mean() > 0.95 and mask[:, 45:].mean() < 0.05
    a = agreement([True, False, True], [0.9, 0.0, np.nan])
    assert a['n'] == 2 and a['hits'] == 1


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return json.loads(self.content)


def test_opendata_search_with_fake_session(monkeypatch, tmp_path):
    from backend import geodata, opendata
    monkeypatch.setattr(geodata, 'CACHE_DIR', tmp_path)
    ns = 'http://s3.amazonaws.com/doc/2006-03-01/'
    umbra_keys = ['stac/2024/2024-09/2024-09-02/a/a.json', 'stac/2024/2024-09/2024-09-20/b/b.json']
    iceye_keys = ['stac-items/2024/09/ICEYE_X_20240903T101010Z_1_SM.json']

    def listing(keys):
        body = ''.join(f'<Contents><Key>{k}</Key></Contents>' for k in keys)
        return f'<ListBucketResult xmlns="{ns}"><IsTruncated>false</IsTruncated>{body}</ListBucketResult>'.encode()

    class S:
        def get(self, url, timeout=None, headers=None, params=None):
            if params and 'prefix' in params:
                return _Resp(200, listing(umbra_keys if 'umbra' in url else iceye_keys))
            if url.endswith('a.json'):
                return _Resp(200, {'id': 'a', 'bbox': [-95.5, 29.6, -95.4, 29.7],
                                   'properties': {'datetime': '2024-09-02T00:00:00Z'}, 'assets': {}})
            if 'ICEYE_X' in url:
                return _Resp(200, {'id': 'x', 'bbox': [10, 10, 11, 11], 'properties': {}, 'assets': {}})
            return _Resp(404, {})
    rep = opendata.search([-95.6, 29.6, -95.38, 29.8], date(2024, 9, 1), date(2024, 9, 5), session=S())
    assert rep['archives']['umbra']['items_in_window'] == 1
    assert [o['id'] for o in rep['overlaps']] == ['a']
    assert rep['archives']['iceye']['overlapping'] == 0


# ── GBDT & susceptibility ────────────────────────────────────────────────────

def test_gbdt_learns_and_roundtrips():
    from pipeline.gbdt import GBDT, auc, dumps
    rng = np.random.default_rng(0)
    X = rng.normal(size=(6000, 4))
    y = ((1.5 * X[:, 0] - X[:, 1] + 0.3 * rng.normal(size=6000)) > 0).astype(float)
    m = GBDT(n_trees=60, depth=3, lr=0.2).fit(X[:4000], y[:4000])
    p = m.predict_proba(X[4000:])
    assert auc(y[4000:], p) > 0.9
    m2 = GBDT.from_dict(json.loads(dumps(m)))
    assert np.abs(m2.predict_proba(X[4000:]) - p).max() < 1e-5
    assert abs(sum(m.feature_importance()) - 1) < 1e-9


def test_gbdt_matches_sklearn_skill():
    sk = pytest.importorskip('sklearn.ensemble')
    from sklearn.metrics import roc_auc_score
    from pipeline.gbdt import GBDT, auc
    rng = np.random.default_rng(1)
    X = rng.normal(size=(20000, 5))
    logit = 1.2 * X[:, 0] - 2 * (X[:, 1] > 0.5) + np.sin(2 * X[:, 2])
    y = (rng.random(20000) < 1 / (1 + np.exp(-logit))).astype(float)
    ours = GBDT(n_trees=120, depth=4).fit(X[:14000], y[:14000]).predict_proba(X[14000:])
    ref = sk.HistGradientBoostingClassifier(max_iter=120, max_depth=4, learning_rate=0.08).fit(
        X[:14000], y[:14000]).predict_proba(X[14000:])[:, 1]
    assert abs(auc(y[14000:], ours) - roc_auc_score(y[14000:], ref)) < 0.02
    assert abs(auc(y[14000:], ours) - roc_auc_score(y[14000:], ours)) < 1e-9


def test_auc_ties():
    from pipeline.gbdt import auc
    assert auc([0, 1], [0.5, 0.5]) == 0.5
    assert auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0


def test_susceptibility_curves_monotone(tmp_path):
    from pipeline.gbdt import GBDT, dumps
    from pipeline.susceptibility import FEATURES, SCENARIOS_MM, SusceptibilityModel, interp_curve, risk_band
    rng = np.random.default_rng(2)
    X = rng.uniform(0, 1, size=(3000, len(FEATURES)))
    X[:, 7] = rng.uniform(0, 600, 3000)
    y = ((X[:, 7] / 600 - X[:, 0]) + 0.1 * rng.normal(size=3000) > 0).astype(float)
    g = GBDT(n_trees=40, depth=3, lr=0.2, feature_names=FEATURES).fit(X, y)
    m = SusceptibilityModel({'model': json.loads(dumps(g)), 'calibrator': None, 'rain7_over_rain3_median': 1.3})
    curves = m.scenario_curves(X[:50])
    assert curves.shape == (50, len(SCENARIOS_MM))
    assert (np.diff(curves, axis=1) >= -1e-12).all()
    assert interp_curve(curves[0], SCENARIOS_MM, 25) == pytest.approx(curves[0][0])
    assert risk_band(0.7) == 'very high' and risk_band(0.01) == 'low'


def test_susceptibility_endpoint_validation(client):
    assert client.post('/api/susceptibility', json={}).status_code == 400
