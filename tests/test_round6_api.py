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
