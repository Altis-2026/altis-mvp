"""Tests for the carrier-style loss calculation: policy terms, the claims-
calibrated damage model, storm calibration, and the upload fields that feed
them. The model JSON is built synthetically so the tests never depend on (or
are fooled by) the real fitted file."""
import numpy as np
import pytest

from pipeline import loss_model as lm
from pipeline.policy_terms import (PolicyTerms, net_payment, net_payment_array, deductible_from_code,
                                   DEDUCTIBLE_CODES)
from backend import loss_calibration as lc
from backend import ingestion


# ── policy terms ────────────────────────────────────────────────────────────

def test_fema_deductible_codes():
    assert DEDUCTIBLE_CODES["F"] == 1250 and DEDUCTIBLE_CODES["A"] == 10000 and DEDUCTIBLE_CODES["9"] == 750
    assert deductible_from_code("2.0") == 2000 and deductible_from_code(" f ") == 1250
    assert deductible_from_code(None) is None and deductible_from_code("nan") is None and deductible_from_code("Z") is None


def test_acv_pays_damage_minus_deductible_capped_at_limit():
    t = PolicyTerms(limit=250000, deductible=2000, valuation="ACV", acv_value=200000, replacement_cost=250000)
    assert net_payment(30000, t)["net"] == 28000
    assert net_payment(1500, t)["net"] == 0                      # below the deductible
    r = net_payment(400000, t)
    assert r["net"] == 250000 and r["limit_binding"] is True


def test_replacement_cost_pays_more_than_acv_damage():
    acv = PolicyTerms(limit=250000, deductible=1000, valuation="ACV", acv_value=200000, replacement_cost=250000)
    rcv = PolicyTerms(limit=250000, deductible=1000, valuation="RCV", acv_value=200000, replacement_cost=250000)
    assert net_payment(30000, rcv)["net"] > net_payment(30000, acv)["net"]
    assert net_payment(30000, rcv)["uplift"] == pytest.approx(1 + 0.33 * 0.25, abs=0.01)


def test_missing_terms_are_assumed_and_labelled_never_silent():
    r = net_payment(20000, PolicyTerms(limit=100000))
    prov = r["provenance"]
    assert all(str(prov[k]).startswith("assumed") for k in ("deductible", "valuation", "acv_value", "replacement_cost"))
    assert r["net"] == 20000 - 1250


def test_provided_terms_are_marked_provided():
    t = PolicyTerms(limit=1e5, deductible=500, valuation="ACV", acv_value=9e4, replacement_cost=1.1e5)
    assert set(net_payment(5000, t)["provenance"].values()) == {"provided"}


def test_vectorised_matches_scalar():
    t = PolicyTerms(limit=200000, deductible=2000, valuation="RCV", acv_value=180000, replacement_cost=240000)
    a = np.array([0, 1000, 5000, 30000, 500000.0])
    assert np.allclose(net_payment_array(a, t), [net_payment(x, t)["net"] for x in a])


def test_coinsurance_only_when_asked():
    base = dict(limit=100000, deductible=0, valuation="ACV", acv_value=180000, replacement_cost=250000)
    assert net_payment(40000, PolicyTerms(**base))["net"] == 40000
    pen = net_payment(40000, PolicyTerms(**base, coinsurance_ratio=0.8))["net"]
    assert pen == pytest.approx(40000 * 100000 / (0.8 * 250000))      # 20,000


# ── damage model ────────────────────────────────────────────────────────────

def _model():
    """Damage ratio rises with depth; same table for 'other' and 'all'."""
    qs = (np.arange(lm.NQ) + 0.5) / lm.NQ
    rows = []
    for lo, hi, centre, mean in [(1, 3, 2, 0.1), (3, 6, 4.5, 0.2), (6, 12, 9, 0.3), (12, 18, 15, 0.4)]:
        rows.append({"bin_in": [lo, hi], "centre_in": centre,
                     "cell": {"n": 500, "mean": mean, "q": [round(float(mean * 2 * q), 4) for q in qs]}})
    tables = {"building_interior": {"other|all": rows, "all|all": rows},
              "building_exterior": {"other|all": rows, "all|all": rows}}
    return {"trained_on": ["x"], "n_train": {"building_interior": 2000}, "theta": 0.33, "tables": tables}


def test_expected_loss_rises_with_depth_and_range_orders():
    m = _model()
    t = PolicyTerms(limit=300000, deductible=1000, valuation="ACV", acv_value=200000, replacement_cost=250000)
    shallow = lm.expected_loss(0.25, 0.0, t, model=m)
    deep = lm.expected_loss(1.25, 0.0, t, model=m)
    assert deep["mid"] > shallow["mid"] > 0
    ranged = lm.expected_loss(0.75, 0.4, t, model=m)
    assert ranged["low"] <= ranged["mid"] <= ranged["high"]
    assert ranged["claim_p10"] <= ranged["claim_p50"] <= ranged["claim_p90"]


def test_deductible_is_applied_per_outcome_not_to_the_average():
    """E[max(0, D - ded)] > max(0, E[D] - ded) when D is spread out: the reason
    the model pushes every quantile through the terms."""
    m = _model()
    big = PolicyTerms(limit=1e9, deductible=15000, valuation="ACV", acv_value=100000, replacement_cost=125000)
    none = PolicyTerms(limit=1e9, deductible=0, valuation="ACV", acv_value=100000, replacement_cost=125000)
    with_ded = lm.expected_loss(0.375, 0.0, big, model=m)["mid"]       # mean damage ~ $10k < $15k deductible
    assert with_ded > 0
    assert with_ded < lm.expected_loss(0.375, 0.0, none, model=m)["mid"]


def test_limit_caps_the_estimate():
    m = _model()
    t = PolicyTerms(limit=5000, deductible=0, valuation="ACV", acv_value=500000, replacement_cost=600000)
    r = lm.expected_loss(1.25, 0.0, t, model=m)
    assert r["high"] <= 5000 and r["claim_p90"] <= 5000


def test_no_model_or_no_limit_returns_none():
    assert lm.expected_loss(1.0, 0.0, PolicyTerms(limit=1e5), model={"tables": {}}) is None
    assert lm.expected_loss(1.0, 0.0, PolicyTerms(limit=0), model=_model()) is None


def test_segments_and_value_scaling():
    assert lm.segment_for("1", 0) == "res1_nb" and lm.segment_for("RES1-2SWB", True) == "res1_b"
    assert lm.segment_for("14", None) == "other" and lm.segment_for("COM1") == "other"
    m = _model()
    m["value_norm"] = {"building_interior": {"c": 0.5, "v0": 200000.0}}
    small = lm._value_scale(m, "building_interior", 50000)
    big = lm._value_scale(m, "building_interior", 800000)
    assert small > 1 > big                       # smaller buildings take a larger share of value
    assert lm._value_scale({}, "building_interior", 50000) == 1.0


# ── storm calibration ───────────────────────────────────────────────────────

def test_storm_factor_recovers_the_true_scale_and_is_idempotent():
    rng = np.random.default_rng(1)
    pred = rng.uniform(5000, 60000, 300)
    paid = pred * 1.3 * rng.uniform(0.7, 1.3, 300)
    r = lc.storm_factor(pred, paid)
    assert r["ok"] and 1.2 < r["factor"] < 1.4 and r["ci95"][0] < r["factor"] < r["ci95"][1]
    rows = [{"property_id": f"P{i}", "policy_number": str(i), "severity_low_usd": 100, "severity_mid_usd": 200,
             "severity_high_usd": 300, "total_mid_usd": 200} for i in range(5)]
    lc.apply_factor(rows, 2.0, {"P0"})
    assert rows[0]["severity_mid_usd"] == 400 and rows[0]["calibrated_from_settled_claim"] is True
    lc.apply_factor(rows, 0.5, set())                 # a NEW calibration replaces, never stacks
    assert rows[0]["severity_mid_usd"] == 100 and rows[1]["uncal_severity_mid_usd"] == 200


def test_storm_factor_refuses_tiny_samples():
    r = lc.storm_factor([100] * 5, [120] * 5)
    assert r["ok"] is False and "at least" in r["reason"]


def test_match_rows_uses_original_prediction_and_skips_duplicates():
    rows = [{"property_id": "A", "policy_number": "1", "severity_mid_usd": 10, "uncal_severity_mid_usd": 8},
            {"property_id": "B", "policy_number": "2", "severity_mid_usd": 20}]
    pred, paid, ids = lc.match_rows(rows, [{"policy_number": "1", "paid_amount": "$9"},
                                           {"property_id": "A", "paid_amount": 50},       # duplicate of A
                                           {"policy_number": "2", "paid_amount": 25},
                                           {"policy_number": "999", "paid_amount": 1}])   # no match
    assert pred == [8.0, 20.0] and paid == [9.0, 25.0] and ids == {"A", "B"}


# ── upload fields ───────────────────────────────────────────────────────────

def test_upload_columns_are_recognised_and_parsed():
    cols = ["Policy No", "Address", "Coverage Amount", "Deductible", "Replacement Cost", "Valuation Basis", "Contents Coverage"]
    m = ingestion.suggest_column_mapping(cols)
    for field, col in (("deductible", "Deductible"), ("replacement_cost", "Replacement Cost"),
                       ("valuation_basis", "Valuation Basis"), ("contents_coverage_amount", "Contents Coverage"),
                       ("coverage_amount", "Coverage Amount")):
        assert m[field]["matched_column"] == col, (field, m[field])
    from backend import main
    t = main._parse_policy_terms({"deductible": "$2,500", "replacement_cost": "300000", "valuation_basis": "Replacement Cost",
                                  "contents_coverage_amount": "", "acv_value": "n/a"})
    assert t == {"deductible": 2500.0, "replacement_cost": 300000.0, "valuation_basis": "RCV"}
    assert main._parse_valuation("ACV") == "ACV" and main._parse_valuation("maybe") is None


# ── live pipeline hook and the calibration endpoint ─────────────────────────

def test_live_net_loss_uses_policy_terms_and_degrades_without_model(monkeypatch):
    from backend import live_pipeline as lp
    monkeypatch.setattr(lm, "load_model", lambda *a, **k: _model())
    prop = {"coverage_amount": 250000, "terms": {"deductible": 5000, "valuation_basis": "ACV",
                                                 "acv_value": 220000, "replacement_cost": 275000}}
    row = {"max_depth_ft": 1.0}
    net = lp._net_loss(prop, None, row, 0.25, None)
    assert net and net["mid"] > 0 and net["depth_basis"] == "above_ground"
    assert set(net["terms_provenance"].values()) == {"provided"}
    assert net["outside_calibration_region"] is True                                    # no coordinates -> flagged
    us = lp._net_loss({**prop, "latitude": 29.7, "longitude": -95.4}, None, row, 0.25, None)
    au = lp._net_loss({**prop, "latitude": -28.8, "longitude": 153.3}, None, row, 0.25, None)
    assert us["outside_calibration_region"] is False and au["outside_calibration_region"] is True
    assert lp._net_loss({"coverage_amount": 0}, None, row, 0.25, None) is None          # no coverage
    assert lp._net_loss(prop, None, {"max_depth_ft": 0.0}, 0.25, None) is None           # no flooding
    monkeypatch.setattr(lm, "load_model", lambda *a, **k: None)
    assert lp._net_loss(prop, None, row, 0.25, None) is None                             # no model -> legacy path


def test_calibrate_endpoint_scales_and_is_idempotent(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main, database as db
    rows = [{"property_id": f"P{i}", "policy_number": str(1000 + i), "impact_class": "Dispatch",
             "severity_low_usd": 8000, "severity_mid_usd": 10000, "severity_high_usd": 12000,
             "total_mid_usd": 10000} for i in range(60)]
    store = {"rows": rows, "meta": {"exposure": {"est_loss_mid_usd": 600000}}}
    monkeypatch.setattr(db, "get_analysis_results", lambda pid, ev: [dict(r) for r in store["rows"]])
    monkeypatch.setattr(db, "save_analysis_results", lambda pid, ev, rs: store.update(rows=rs))
    monkeypatch.setattr(db, "get_analysis_meta", lambda pid, ev: store["meta"])
    monkeypatch.setattr(db, "save_analysis_meta", lambda pid, ev, m: store.update(meta=m))
    with TestClient(main.app) as c:
        body = {"settled": [{"policy_number": str(1000 + i), "paid_amount": 15000} for i in range(60)]}
        r = c.post("/api/portfolio/X/calibrate-losses", json=body)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["factor"] == 1.5 and j["matched"] == 60 and j["new_totals"]["est_loss_mid_usd"] == 900000
        assert store["meta"]["exposure"]["storm_calibration"]["n_settled"] == 60
        r2 = c.post("/api/portfolio/X/calibrate-losses", json=body)          # same data again: same answer, no stacking
        assert r2.json()["factor"] == 1.5 and r2.json()["new_totals"]["est_loss_mid_usd"] == 900000
        assert c.post("/api/portfolio/X/calibrate-losses", json={"settled": body["settled"][:5]}).status_code == 422
        assert c.post("/api/portfolio/X/calibrate-losses", json={"settled": []}).status_code == 400
