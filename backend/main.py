"""
main.py — Altis FastAPI backend.

Run from project root:
    uvicorn backend.main:app --reload --port 8000

Endpoints:
    GET  /api/events
    GET  /api/events/{id}/properties
    GET  /api/events/{id}/tiles
    GET  /api/sar-thumbnails/{property_id}
    GET  /api/portfolio/template
    POST /api/portfolio/upload
    GET  /api/portfolio/{id}
    POST /api/portfolio/{id}/analyze/{event_id}
    GET  /api/portfolio/{id}/results/{event_id}
    GET  /api/health
"""
import os
import io
import uuid
import csv
import asyncio
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from backend.database import (
    init_db, load_event_data, get_event_stats,
    save_portfolio, get_portfolio, list_portfolios,
    save_analysis_results, get_analysis_results,
)
from backend.geocoder import geocode_batch
from backend.gee_service import get_flood_tile_url, get_sar_thumbnails
from pipeline.config import EVENTS

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Altis Flood Intelligence API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# GEE tile URL cache (generated once per server start if GEE available)
_tile_cache: dict = {}


@app.on_event("startup")
def startup():
    init_db()
    # Pre-load event data into memory
    for eid in EVENTS:
        df = load_event_data(eid)
        if df is not None:
            print(f"  Loaded {len(df)} properties for {eid}")
        else:
            print(f"  No data found for {eid} — run pipeline first")
    print("✓ Altis backend ready at http://localhost:8000")


# ── Event endpoints ───────────────────────────────────────────────────────────

@app.get("/api/events")
def list_events():
    """List available flood events with metadata."""
    return list(EVENTS.values())


@app.get("/api/events/{event_id}/properties")
def get_properties(event_id: str):
    """Return all properties with triage data and coordinates."""
    df = load_event_data(event_id)
    if df is None:
        raise HTTPException(404, f"No data for event '{event_id}'. Run the pipeline first.")

    properties = df.to_dict('records')

    # Sanitize NaN
    for p in properties:
        for k, v in p.items():
            if isinstance(v, float) and (v != v):  # NaN check
                p[k] = None

    return {
        "event_id":   event_id,
        "properties": properties,
        "stats":      get_event_stats(df),
    }


@app.get("/api/events/{event_id}/tiles")
def get_tiles(event_id: str):
    """
    Return GEE flood overlay tile URL for Mapbox raster source.
    Returns null if GEE is not authenticated (demo still works).
    """
    if event_id not in EVENTS:
        raise HTTPException(404, f"Unknown event: {event_id}")

    if event_id not in _tile_cache:
        _tile_cache[event_id] = get_flood_tile_url(event_id)

    return {"event_id": event_id, "tile_url": _tile_cache.get(event_id)}


# ── SAR thumbnails ────────────────────────────────────────────────────────────

@app.get("/api/sar-thumbnails/{property_id}")
def get_thumbnails(property_id: str):
    """
    Return before/after SAR thumbnails for a property.
    Uses cached GEE imagery if available, otherwise synthetic.
    """
    # Look up depth from event data
    depth_ft = 0.0
    for eid in EVENTS:
        df = load_event_data(eid)
        if df is not None:
            matches = df[df['property_id'] == property_id]
            if not matches.empty:
                depth_ft = float(matches.iloc[0].get('max_depth_ft', 0))
                break

    return get_sar_thumbnails(property_id, depth_ft)


# ── Portfolio upload ──────────────────────────────────────────────────────────

@app.get("/api/portfolio/template")
def download_template():
    """Download CSV template for portfolio upload."""
    content = "policy_number,address,coverage_amount\n"
    content += "POL-001,\"1234 Main Street, Houston, TX 77001\",250000\n"
    content += "POL-002,\"5678 Oak Avenue, Port Charlotte, FL 33952\",180000\n"
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=altis_portfolio_template.csv"}
    )


@app.post("/api/portfolio/upload")
async def upload_portfolio(file: UploadFile = File(...)):
    """
    Process carrier portfolio CSV.
    Geocodes addresses using Census TIGER (free, no API key).
    Returns portfolio_id, geocoded count, and center for fly-to.
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(400, "File must be a CSV.")

    content = await file.read()
    try:
        text = content.decode('utf-8')
        reader = list(csv.DictReader(io.StringIO(text)))
    except Exception as e:
        raise HTTPException(400, f"Could not parse CSV: {e}")

    if not reader:
        raise HTTPException(400, "CSV is empty.")

    # Detect columns (flexible naming)
    sample     = reader[0]
    addr_col   = next((c for c in sample if 'address' in c.lower()), None)
    policy_col = next((c for c in sample if 'policy' in c.lower()), None)
    cov_col    = next((c for c in sample if 'coverage' in c.lower() or 'amount' in c.lower()), None)

    if not addr_col:
        raise HTTPException(400, "CSV must have an 'address' column.")

    addresses = [row[addr_col].strip() for row in reader if row.get(addr_col, '').strip()]
    if not addresses:
        raise HTTPException(400, "No addresses found in CSV.")

    # Geocode (Census TIGER — free)
    print(f"Geocoding {len(addresses)} addresses...")
    geo_results = await geocode_batch(addresses, concurrency=8)

    # Build property list
    portfolio_id = str(uuid.uuid4())[:8].upper()
    properties   = []
    lats, lons   = [], []

    for i, (row, geo) in enumerate(zip(reader, geo_results)):
        addr = row.get(addr_col, '').strip()
        if not addr:
            continue

        prop = {
            'property_id':    f"PORT-{portfolio_id}-{str(i+1).zfill(4)}",
            'policy_number':  row.get(policy_col, '') if policy_col else '',
            'address':        addr,
            'coverage_amount': float(row.get(cov_col, 0) or 0) if cov_col else 0,
            'matched_address': '',
        }

        if geo:
            prop['latitude']        = geo['lat']
            prop['longitude']       = geo['lon']
            prop['matched_address'] = geo.get('matched_address', addr)
            lats.append(geo['lat'])
            lons.append(geo['lon'])
        else:
            prop['latitude']  = None
            prop['longitude'] = None

        properties.append(prop)

    geocoded_count = sum(1 for p in properties if p.get('latitude') is not None)

    if geocoded_count == 0:
        raise HTTPException(422, "Could not geocode any addresses. Check address format.")

    # Compute center for fly-to
    center = {
        'lat': sum(lats) / len(lats),
        'lon': sum(lons) / len(lons),
    }

    save_portfolio(portfolio_id, properties, center, geocoded_count)
    print(f"Portfolio {portfolio_id}: {geocoded_count}/{len(addresses)} geocoded")

    return {
        "portfolio_id":    portfolio_id,
        "total_count":     len(addresses),
        "geocoded_count":  geocoded_count,
        "center":          center,
        "properties":      [p for p in properties if p.get('latitude') is not None],
    }


@app.get("/api/portfolios")
def list_all_portfolios():
    """List every saved portfolio (id, counts, center), newest first."""
    return {"portfolios": list_portfolios()}


@app.get("/api/portfolio/{portfolio_id}")
def get_portfolio_properties(portfolio_id: str):
    """Return all geocoded properties for a portfolio."""
    props = get_portfolio(portfolio_id)
    if not props:
        raise HTTPException(404, f"Portfolio '{portfolio_id}' not found.")
    return {"portfolio_id": portfolio_id, "properties": props}


@app.post("/api/portfolio/{portfolio_id}/analyze/{event_id}")
async def analyze_portfolio(portfolio_id: str, event_id: str,
                             background_tasks: BackgroundTasks):
    """
    Spatially match portfolio properties against event flood data.
    For each portfolio property, finds the nearest event property
    and inherits its triage decision.

    For the MVP with pre-computed data, this is near-instant.
    For new events, this would trigger the full GEE pipeline.
    """
    props = get_portfolio(portfolio_id)
    if not props:
        raise HTTPException(404, f"Portfolio '{portfolio_id}' not found.")

    event_df = load_event_data(event_id)
    if event_df is None:
        raise HTTPException(404, f"No flood data for event '{event_id}'.")

    # Spatial match: nearest event property within 2km
    from scipy.spatial import cKDTree
    import numpy as np

    event_coords = event_df[['latitude', 'longitude']].values
    tree = cKDTree(event_coords)

    results = []
    COLOR_MAP = {
        'Dispatch':       '#FF4444',
        'Remote-Approve': '#4CAF82',
        'Remote-Deny':    '#6B8FA3',
        'Review':         '#FFB347',
    }

    for prop in props:
        if not prop.get('latitude') or not prop.get('longitude'):
            continue

        # Find nearest event property
        dist, idx = tree.query([prop['latitude'], prop['longitude']])
        dist_km   = dist * 111  # rough degrees to km

        if dist_km > 2.0:
            # Too far from event coverage area — no data
            result = {
                **prop,
                'impact_class':     'No Coverage',
                'max_depth_ft':     0.0,
                'pct_flooded':      0.0,
                'confidence_score': 0,
                'adjuster_note':    'Property is outside the satellite analysis coverage area for this event.',
                'color':            '#444',
            }
        else:
            match = event_df.iloc[idx]
            result = {
                **prop,
                'impact_class':     match['impact_class'],
                'max_depth_ft':     float(match.get('max_depth_ft', 0)),
                'pct_flooded':      float(match.get('pct_flooded', 0)),
                'confidence_score': int(match.get('confidence_score', 0)),
                'adjuster_note':    str(match.get('adjuster_note', '')),
                'color':            COLOR_MAP.get(match['impact_class'], '#6B8FA3'),
            }
        results.append(result)

    save_analysis_results(portfolio_id, event_id, results)

    return {
        "portfolio_id": portfolio_id,
        "event_id":     event_id,
        "analyzed":     len(results),
        "results":      results,
        "stats":        _portfolio_stats(results),
    }


@app.get("/api/portfolio/{portfolio_id}/results/{event_id}")
def get_results(portfolio_id: str, event_id: str):
    """Return stored analysis results for a portfolio/event combination."""
    results = get_analysis_results(portfolio_id, event_id)
    if not results:
        raise HTTPException(404, "No analysis results found. Run analyze first.")
    return {
        "portfolio_id": portfolio_id,
        "event_id":     event_id,
        "results":      results,
        "stats":        _portfolio_stats(results),
    }


def _portfolio_stats(results: list) -> dict:
    dispatch = sum(1 for r in results if r.get('impact_class') == 'Dispatch')
    remote   = sum(1 for r in results
                   if r.get('impact_class') in ('Remote-Approve', 'Remote-Deny'))
    review   = sum(1 for r in results if r.get('impact_class') == 'Review')
    return {
        'total':            len(results),
        'dispatch':         dispatch,
        'remote_total':     remote,
        'review':           review,
        'estimated_savings': remote * 750,
    }


@app.get("/api/validation/{event_id}")
def get_validation_report(event_id: str):
    """
    Return the FEMA accuracy validation report for an event, if it's been
    generated. Run validation/accuracy_check.py first to produce it.
    """
    from pathlib import Path
    BASE_DIR = Path(__file__).parent.parent
    report_path = BASE_DIR / 'outputs' / f"validation_{event_id}.md"

    if not report_path.exists():
        raise HTTPException(
            404,
            f"No validation report found for '{event_id}'. Run "
            f"'python validation/accuracy_check.py --event {event_id}' first."
        )

    return {"event_id": event_id, "content": report_path.read_text()}


@app.get("/api/accuracy/{event_id}")
def get_accuracy_calibration(event_id: str):
    """
    Return the calibrated-probability + precision/recall blob for an event,
    if it's been generated. Run validation/accuracy_check.py first.
    """
    import json
    from pathlib import Path
    BASE_DIR = Path(__file__).parent.parent
    calib_path = BASE_DIR / 'outputs' / f"calibration_{event_id}.json"

    if not calib_path.exists():
        raise HTTPException(
            404,
            f"No calibration data found for '{event_id}'. Run "
            f"'python validation/accuracy_check.py --event {event_id}' first."
        )

    return json.loads(calib_path.read_text())


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "events": {
            eid: load_event_data(eid) is not None
            for eid in EVENTS
        }
    }
