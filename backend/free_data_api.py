"""
API routes for the free public data integrations: satellite pass timing,
river gauges, and (added below as they land) the event library and event
watch. Kept out of main.py so each integration stays self-contained.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException, Query

from pipeline.config import EVENTS

router = APIRouter(prefix="/api")
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"


def _parse_bbox(bbox: str) -> list[float]:
    try:
        vals = [float(v) for v in bbox.split(",")]
    except ValueError:
        raise HTTPException(400, "bbox must be minlon,minlat,maxlon,maxlat")
    if len(vals) != 4 or not (-180 <= vals[0] < vals[2] <= 180 and -90 <= vals[1] < vals[3] <= 90):
        raise HTTPException(400, "bbox must be minlon,minlat,maxlon,maxlat with min < max")
    if (vals[2] - vals[0]) > 5 or (vals[3] - vals[1]) > 5:
        raise HTTPException(400, "bbox too large (max 5 degrees per side)")
    return vals


def _event_bbox(event_id: str) -> list[float]:
    ev = EVENTS.get(event_id)
    if not ev:
        raise HTTPException(404, f"Unknown event '{event_id}'.")
    return ev["bbox"]


# ── Satellite passes ─────────────────────────────────────────────────────────

@router.get("/passes")
def passes(bbox: str = Query(..., description="minlon,minlat,maxlon,maxlat"),
           lookback_days: int = Query(40, ge=7, le=120),
           engine: bool = Query(False, description="also check Earth Engine ingestion")):
    """When Sentinel-1/2 last imaged this box and when the next radar pass is
    expected. Source: Copernicus Data Space STAC (free, no account)."""
    from backend.passes import pass_report
    try:
        return pass_report(_parse_bbox(bbox), lookback_days, check_engine=engine)
    except requests.RequestException as e:
        raise HTTPException(502, f"Copernicus catalogue unavailable: {e}")


@router.get("/events/{event_id}/passes")
def event_passes(event_id: str, engine: bool = False):
    from backend.passes import pass_report
    try:
        return pass_report(_event_bbox(event_id), check_engine=engine)
    except requests.RequestException as e:
        raise HTTPException(502, f"Copernicus catalogue unavailable: {e}")


# ── River gauges ─────────────────────────────────────────────────────────────

@router.get("/gauges")
def gauges(bbox: str = Query(..., description="minlon,minlat,maxlon,maxlat")):
    """Live NOAA river gauges in the box: observed and forecast stage with the
    official NWS flood category. US only (NOAA NWPS)."""
    from backend.gauges import gauges_in_bbox, live_status_summary
    try:
        gs = gauges_in_bbox(_parse_bbox(bbox))
    except requests.RequestException as e:
        raise HTTPException(502, f"NOAA gauge service unavailable: {e}")
    return {"summary": live_status_summary(gs), "gauges": gs,
            "source": "NOAA National Water Prediction Service"}


@router.get("/events/{event_id}/gauges")
def event_gauges(event_id: str):
    """Peak stage at each gauge during a past event, plus the validation of
    gauge-anchored depth against USGS high water marks when it has been run."""
    from backend.gauges import event_gauge_peaks
    ev = EVENTS.get(event_id)
    if not ev:
        raise HTTPException(404, f"Unknown event '{event_id}'.")
    start = date.fromisoformat(ev["event_date"]) - timedelta(days=5)
    end = date.fromisoformat(ev["event_date"]) + timedelta(days=21)
    try:
        peaks = event_gauge_peaks(ev["bbox"], start.isoformat(), end.isoformat())
    except requests.RequestException as e:
        raise HTTPException(502, f"NOAA gauge service unavailable: {e}")
    validation = None
    vpath = OUTPUT_DIR / f"gauge_depth_check_{event_id}.json"
    if vpath.exists():
        v = json.loads(vpath.read_text())
        validation = {"method": v.get("method"), "summaries": v.get("summaries"),
                      "sar_detector_same_marks_50m": v.get("sar_detector_same_marks_50m"),
                      "caveats": v.get("caveats")}
    return {"event_id": event_id, "window": [start.isoformat(), end.isoformat()],
            "gauges": [g for g in peaks if g.get("peak_stage_ft") is not None],
            "validation": validation}
