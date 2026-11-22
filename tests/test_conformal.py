import json
from pipeline import conformal


def test_widen_band_uses_stored_q(tmp_path, monkeypatch):
    q = tmp_path / "q.json"
    q.write_text(json.dumps({"q": {"min/above_ground": {"0.8": 0.5}}, "w_floor_usd": 1000, "calibrated_on": ["x"]}))
    monkeypatch.setattr(conformal, "Q_PATH", q)
    monkeypatch.setattr(conformal, "CHECK_PATH", tmp_path / "none.json")
    lo, hi, info = conformal.widen_band(10000, 30000, 250000, False, "above_ground")
    assert (lo, hi) == (0, 40000) or (lo, hi) == (0, 40000.0)
    assert info["guaranteed"] is False


def test_widen_band_capped_at_limit_and_floor(tmp_path, monkeypatch):
    q = tmp_path / "q.json"
    q.write_text(json.dumps({"q": {"full/above_ground": {"0.8": 2.0}}, "w_floor_usd": 1000}))
    monkeypatch.setattr(conformal, "Q_PATH", q)
    monkeypatch.setattr(conformal, "CHECK_PATH", tmp_path / "none.json")
    lo, hi, _ = conformal.widen_band(5000, 20000, 22000, True, "above_ground")
    assert lo == 0 and hi == 22000


def test_missing_calibration_returns_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(conformal, "Q_PATH", tmp_path / "missing.json")
    assert conformal.widen_band(1, 2, 3, True, "above_ground") == (1, 2, None)
