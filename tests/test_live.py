"""
test_live.py — Phase 1/2: global geocoding, lat/lon ingestion, and the live
on-demand analysis surface.

GEE and Mapbox are network services, so the live analysis engine and the
geocoder HTTP calls are mocked here — these tests pin the *plumbing* (routing,
fallbacks, windowing, bbox math, endpoint shapes, honest error codes), not the
satellite science (which is validated by real runs separately).
"""
import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import backend.database as db
    monkeypatch.setattr(db, "DB_PATH", Path(tempfile.mktemp(suffix=".db")))
    import backend.main as main
    with TestClient(main.app) as c:
        yield c


# ── Geocoder: Mapbox primary, Census fallback ────────────────────────────────

def test_geocoder_prefers_mapbox_when_token_present(monkeypatch):
    import backend.geocoder as g
    monkeypatch.setattr(g, "MAPBOX_TOKEN", "pk.test")

    async def fake_mapbox(session, addr):
        return {"lat": 10.4, "lon": -75.5, "matched_address": "Cartagena"}

    async def fake_census(session, addr):
        raise AssertionError("Census should not be called when Mapbox succeeds")

    monkeypatch.setattr(g, "_geocode_mapbox", fake_mapbox)
    monkeypatch.setattr(g, "_geocode_census", fake_census)

    out = asyncio.run(g.geocode_batch(["Cartagena, Colombia"]))
    assert out[0]["lat"] == 10.4 and out[0]["lon"] == -75.5


def test_geocoder_falls_back_to_census(monkeypatch):
    import backend.geocoder as g
    monkeypatch.setattr(g, "MAPBOX_TOKEN", "pk.test")

    async def fake_mapbox(session, addr):
        return None  # Mapbox missed

    async def fake_census(session, addr):
        return {"lat": 29.7, "lon": -95.4, "matched_address": "Houston"}

    monkeypatch.setattr(g, "_geocode_mapbox", fake_mapbox)
    monkeypatch.setattr(g, "_geocode_census", fake_census)

    out = asyncio.run(g.geocode_batch(["123 Main St, Houston TX"]))
    assert out[0]["matched_address"] == "Houston"


# ── Ingestion: lat/lon passthrough ───────────────────────────────────────────

def test_ingestion_maps_and_passes_through_latlon():
    import pandas as pd
    from backend.ingestion import suggest_column_mapping, apply_mapping

    df = pd.DataFrame([{
        "Policy Number": "P1", "Property Address": "Plot 14", "City": "Dadu",
        "Latitude": "26.6", "Longitude": "67.8", "Total Insured Value": "185000",
    }])
    mapping = {k: v["matched_column"] for k, v in
               suggest_column_mapping(list(df.columns)).items() if v["matched_column"]}
    assert mapping.get("latitude") == "Latitude"
    assert mapping.get("longitude") == "Longitude"

    out = apply_mapping(df, mapping)
    assert out.iloc[0]["latitude"] == "26.6"
    assert out.iloc[0]["longitude"] == "67.8"


# ── live_pipeline pure helpers ───────────────────────────────────────────────

def test_derive_windows_brackets_event_date():
    from backend.live_pipeline import derive_windows
    w = derive_windows("2022-09-05")
    assert w["pre_start"] < w["pre_end"] < w["post_start"] <= w["post_end"]
    assert w["post_start"] == "2022-09-05"


def test_derive_windows_rejects_bad_date():
    from backend.live_pipeline import derive_windows, LiveAnalysisError
    with pytest.raises(LiveAnalysisError):
        derive_windows("not-a-date")


def test_bbox_from_properties_encloses_points():
    from backend.live_pipeline import bbox_from_properties
    props = [
        {"latitude": 26.6, "longitude": 67.8},
        {"latitude": 27.1, "longitude": 67.6},
    ]
    w, s, e, n = bbox_from_properties(props)
    assert w < 67.6 and s < 26.6 and e > 67.8 and n > 27.1


# ── Endpoints: gee-status + analyze-live (mocked engine) ─────────────────────

def test_gee_status_shape(client):
    body = client.get("/api/gee-status").json()
    assert "live_analysis" in body and "message" in body


def test_analyze_live_persists_and_returns(client, monkeypatch):
    import backend.database as db
    db.init_db()
    db.save_portfolio("PF1", [{
        "property_id": "PORT-PF1-0001", "policy_number": "P1", "address": "Plot 14",
        "coverage_amount": 185000, "latitude": 26.6, "longitude": 67.8, "matched_address": "",
    }], {"lat": 26.6, "lon": 67.8}, 1)

    import backend.live_pipeline as lp

    def fake_live(props, event_date=None, windows=None, wse_radius_m=300, label=""):
        return {
            "results": [{
                "property_id": "PORT-PF1-0001", "address": "Plot 14", "latitude": 26.6,
                "longitude": 67.8, "impact_class": "Dispatch", "max_depth_ft": 5.3,
                "pct_flooded": 100.0, "confidence_score": 97, "adjuster_note": "flooded",
                "color": "#FF4444",
            }],
            "bbox": [67.7, 26.5, 67.9, 26.7],
            "meta": {"is_live": True, "flooded_count": 1, "analyzed_count": 1},
        }

    monkeypatch.setattr(lp, "analyze_portfolio_live", fake_live)
    r = client.post("/api/portfolio/PF1/analyze-live", json={"event_date": "2022-09-05"})
    assert r.status_code == 200
    body = r.json()
    assert body["analyzed"] == 1 and body["results"][0]["impact_class"] == "Dispatch"
    assert body["meta"]["is_live"] is True

    # persisted under the 'live' key
    saved = client.get("/api/portfolio/PF1/results/live").json()
    assert saved["results"][0]["property_id"] == "PORT-PF1-0001"


def test_analyze_live_missing_creds_returns_503(client, monkeypatch):
    import backend.database as db
    db.init_db()
    db.save_portfolio("PF2", [{
        "property_id": "PORT-PF2-0001", "address": "x", "coverage_amount": 0,
        "latitude": 1.0, "longitude": 2.0, "matched_address": "",
    }], {"lat": 1.0, "lon": 2.0}, 1)

    import backend.live_pipeline as lp

    def boom(*a, **k):
        raise lp.LiveAnalysisError("Live satellite analysis requires a service-account key.")

    monkeypatch.setattr(lp, "analyze_portfolio_live", boom)
    r = client.post("/api/portfolio/PF2/analyze-live", json={"event_date": "2022-09-05"})
    assert r.status_code == 503
    assert "service-account" in r.json()["detail"]


def test_sar_thumbnail_serves_real_when_coords_and_gee(client, monkeypatch):
    import backend.live_pipeline as lp
    monkeypatch.setattr(lp, "gee_available", lambda: True)
    monkeypatch.setattr(lp, "real_thumbnail",
                        lambda lat, lon, w, is_post, view='sar': f"data:image/png;base64,REAL-{'post' if is_post else 'pre'}")
    r = client.get("/api/sar-thumbnails/PROP1?view=sar&lat=26.6&lon=67.8&event_date=2022-09-05")
    body = r.json()
    assert body["is_real_sar"] is True
    assert body["pre_url"] != body["post_url"]
