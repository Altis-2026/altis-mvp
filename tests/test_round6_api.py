"""
test_round6_api.py — Endpoint + DB tests for Round 6 features.

Uses a temp SQLite DB so the committed altis.db is never touched, and the
real committed event CSVs (read-only) for the dispatch queue / PDF.
"""
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


def test_dispatch_queue_ranked(client):
    r = client.get("/api/events/harvey/dispatch-queue")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] > 0
    queue = body["queue"]
    scores = [p["priority_score"] for p in queue]
    assert scores == sorted(scores, reverse=True)
    assert queue[0]["priority_rank"] == 1
    assert all(p["impact_class"] in ("Dispatch", "Review") for p in queue)


def test_dispatch_queue_class_filter(client):
    r = client.get("/api/events/harvey/dispatch-queue?classes=Dispatch")
    assert r.status_code == 200
    assert all(p["impact_class"] == "Dispatch" for p in r.json()["queue"])


def test_dispatch_queue_unknown_event_404(client):
    assert client.get("/api/events/nope/dispatch-queue").status_code == 404


def test_feedback_roundtrip_and_summary(client):
    r = client.post("/api/property/HARV-00006/feedback", json={
        "event_id": "harvey", "agree": False,
        "original_class": "Dispatch", "corrected_class": "Review",
        "note": "tarp not water", "address": "2025 West 11th",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    summary = r.json()["summary"]
    assert summary["total"] == 1 and summary["disagreed"] == 1 and summary["corrected"] == 1

    r = client.get("/api/events/harvey/feedback")
    assert r.status_code == 200
    assert len(r.json()["feedback"]) == 1
    assert r.json()["feedback"][0]["property_id"] == "HARV-00006"


def test_runs_queue_lifecycle(client):
    r = client.post("/api/runs", json={
        "title": "USGS: Buffalo Bayou 18.2ft", "source": "monitor",
        "bbox": [-95.6, 29.6, -95.3, 29.9], "note": "auto",
    })
    assert r.status_code == 200
    run = r.json()["run"]
    assert run["status"] == "queued" and run["bbox"] == [-95.6, 29.6, -95.3, 29.9]

    rid = run["id"]
    r = client.post(f"/api/runs/{rid}/status", json={"status": "running"})
    assert r.status_code == 200

    r = client.get("/api/runs")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert any(x["id"] == rid and x["status"] == "running" for x in runs)


def test_runs_requires_title(client):
    assert client.post("/api/runs", json={}).status_code == 400


def test_run_status_validates(client):
    r = client.post("/api/runs", json={"title": "x"})
    rid = r.json()["run"]["id"]
    assert client.post(f"/api/runs/{rid}/status", json={"status": "bogus"}).status_code == 400


def test_event_report_pdf(client):
    r = client.get("/api/events/harvey/report")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"
    assert len(r.content) > 2000


def test_event_report_unknown_event_404(client):
    assert client.get("/api/events/nope/report").status_code == 404


# ── SAR / optical thumbnails ──────────────────────────────────────────────────

def test_sar_and_optical_thumbnails_differ(client):
    """The SAR/OPTICAL toggle must return genuinely different imagery."""
    sar = client.get("/api/sar-thumbnails/demo_prop?view=sar").json()
    opt = client.get("/api/sar-thumbnails/demo_prop?view=optical").json()
    assert sar["view"] == "sar" and opt["view"] == "optical"
    assert sar["pre_url"].startswith("data:image/png;base64,")
    assert sar["post_url"] != opt["post_url"]   # different sensors, different image
    assert sar["pre_url"] != opt["pre_url"]


def test_sar_thumbnail_uses_analyzed_depth_for_uploaded_property(client):
    """An uploaded property's flood signature comes from saved analysis."""
    import backend.database as db
    db.init_db()
    db.save_analysis_results("pf1", "harvey", [{
        "property_id": "PF-UP-1", "impact_class": "Dispatch", "max_depth_ft": 4.2,
        "pct_flooded": 0.6, "confidence_score": 88, "adjuster_note": "",
    }])
    assert db.get_analyzed_depth("PF-UP-1") == 4.2
    # flooded post image must differ from the dry pre image
    body = client.get("/api/sar-thumbnails/PF-UP-1?view=sar").json()
    assert body["pre_url"] != body["post_url"]


def test_sar_thumbnail_defaults_to_sar_view(client):
    body = client.get("/api/sar-thumbnails/whatever").json()
    assert body["view"] == "sar"


# ── Chat ("Ask about this area") ─────────────────────────────────────────────

def test_chat_requires_message(client):
    assert client.post("/api/chat", json={}).status_code == 400


def test_chat_grounds_reply_in_context(client, monkeypatch):
    import backend.chat as chat_mod

    captured = {}

    def fake_ask(message, history, event_meta, event_stats, property_row,
                 portfolio_summary=None):
        captured.update(message=message, event_meta=event_meta, event_stats=event_stats)
        return f"{event_stats['dispatch']} properties need dispatch."

    monkeypatch.setattr(chat_mod, "ask", fake_ask)
    r = client.post("/api/chat", json={
        "message": "How many need dispatch?",
        "event_meta": {"label": "Hurricane Harvey"},
        "event_stats": {"dispatch": 80},
    })
    assert r.status_code == 200
    assert r.json()["reply"] == "80 properties need dispatch."
    assert captured["event_meta"]["label"] == "Hurricane Harvey"


def test_chat_surfaces_upstream_error_as_502(client, monkeypatch):
    import backend.chat as chat_mod

    def fake_ask(*a, **kw):
        raise chat_mod.ChatError("OPENROUTER_API_KEY is missing")

    monkeypatch.setattr(chat_mod, "ask", fake_ask)
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 502
    assert "OPENROUTER_API_KEY" in r.json()["detail"]
