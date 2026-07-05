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
    POST /api/portfolio/{upload_id}/confirm
    GET  /api/portfolio/{id}
    POST /api/portfolio/{id}/analyze/{event_id}
    GET  /api/portfolio/{id}/results/{event_id}
    GET  /api/events/{id}/dispatch-queue
    POST /api/property/{id}/feedback
    GET  /api/events/{id}/feedback
    GET  /api/events/{id}/report          (audit PDF)
    GET  /api/runs   POST /api/runs        (monitor → pipeline queue)
    POST /api/chat                         (ask-about-this-area assistant)
    GET  /api/health
"""
import os
import io
import uuid
import asyncio
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from backend.database import (
    init_db, load_event_data, get_event_stats,
    save_portfolio, get_portfolio, list_portfolios,
    save_analysis_results, get_analysis_results, get_analyzed_depth,
    save_analysis_meta, get_analysis_meta,
    save_pending_upload, get_pending_upload, delete_pending_upload,
    save_feedback, get_feedback_for_event, get_feedback_summary,
    save_run, list_runs, update_run_status,
)
from backend.priority import rank_dispatch
from backend.geocoder import geocode_batch
from backend.gee_service import get_flood_tile_url, get_sar_thumbnails
from backend.ingestion import (
    parse_upload, suggest_column_mapping, apply_mapping,
    build_preview, IngestionError,
)
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
def get_thumbnails(property_id: str, view: str = 'sar',
                   lat: float = None, lon: float = None, event_date: str = None):
    """
    Return before/after thumbnails for a property and sensor view
    ('sar' or 'optical').

    When real coordinates + an event date are supplied AND Earth Engine is
    configured, this serves *real* Sentinel-1 / Sentinel-2 imagery for that
    exact spot and date window (the live, global path). Otherwise it falls back
    to a synthetic render whose flood signature is driven by the property's
    analyzed depth — resolved from pre-computed event data, then saved analysis.
    """
    view = 'optical' if view == 'optical' else 'sar'

    # ── Real imagery path (live, global) ─────────────────────────────────
    if lat is not None and lon is not None and event_date:
        try:
            from backend.live_pipeline import real_thumbnail, derive_windows, gee_available
            if gee_available():
                windows = derive_windows(event_date)
                pre  = real_thumbnail(lat, lon, windows, is_post=False, view=view)
                post = real_thumbnail(lat, lon, windows, is_post=True,  view=view)
                if pre and post:
                    return {'property_id': property_id, 'view': view,
                            'pre_url': pre, 'post_url': post, 'is_real_sar': True}
        except Exception as e:
            print(f"real thumbnail path failed, falling back to synthetic: {e}")

    # ── Synthetic fallback (depth-driven) ────────────────────────────────
    depth_ft = 0.0
    for eid in EVENTS:
        df = load_event_data(eid)
        if df is not None:
            matches = df[df['property_id'] == property_id]
            if not matches.empty:
                depth_ft = float(matches.iloc[0].get('max_depth_ft', 0))
                break

    if depth_ft == 0.0:
        analyzed = get_analyzed_depth(property_id)
        if analyzed:
            depth_ft = analyzed

    return get_sar_thumbnails(property_id, depth_ft, view=view)


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
    Parse a carrier portfolio file (.csv/.xlsx/.xls/.pdf) and suggest a
    column mapping. Does NOT geocode or commit anything yet — the caller
    must review/edit the mapping and call /confirm to finish the upload.
    """
    content = await file.read()
    try:
        df = parse_upload(file.filename, content)
    except IngestionError as e:
        raise HTTPException(400, str(e))

    suggested_mapping = suggest_column_mapping(list(df.columns))
    upload_id = save_pending_upload(file.filename, df.to_dict('records'), suggested_mapping)

    mapping_for_preview = {f: v['matched_column'] for f, v in suggested_mapping.items()}
    preview = build_preview(df, mapping_for_preview)

    return {
        "upload_id":          upload_id,
        "filename":           file.filename,
        "columns":            list(df.columns),
        "suggested_mapping":  suggested_mapping,
        "row_count":          preview['row_count'],
        "preview_rows":       preview['preview_rows'],
        "flagged_count":      preview['flagged_count'],
        "flagged_rows":       preview['flagged_rows'],
    }


@app.post("/api/portfolio/{upload_id}/confirm")
async def confirm_portfolio_upload(upload_id: str, body: dict = Body(...)):
    """
    Commit a pending upload: apply the (possibly user-edited) column
    mapping, standardize + geocode addresses, save the portfolio, and
    delete the pending upload row. Returns the same shape the old
    single-shot upload endpoint used to return.
    """
    pending = get_pending_upload(upload_id)
    if pending is None:
        raise HTTPException(404, f"No pending upload '{upload_id}'. Upload the file again.")

    mapping = body.get('mapping') or {}
    if not mapping.get('address'):
        raise HTTPException(400, "An address column (or city/state/zip columns) must be mapped.")

    df = pd.DataFrame(pending['raw_rows'])
    mapped = apply_mapping(df, mapping)

    def _parse_coord(v, lo, hi):
        try:
            f = float(str(v).strip())
        except (TypeError, ValueError):
            return None
        return f if lo <= f <= hi else None

    # First pass: keep rows with an address OR explicit coordinates, and pull
    # any pre-geocoded lat/lon straight through (real carrier files often ship
    # coordinates — and it makes the result deterministic, no geocoder needed).
    rows = []
    for i, row in mapped.iterrows():
        addr = str(row.get('address', '')).strip()
        lat  = _parse_coord(row.get('latitude'),  -90,  90)
        lon  = _parse_coord(row.get('longitude'), -180, 180)
        if not addr and lat is None:
            continue
        rows.append({'i': i, 'addr': addr, 'lat': lat, 'lon': lon, 'row': row})

    if not rows:
        raise HTTPException(422, "No addresses or coordinates found after applying the mapping.")

    # Geocode only the rows that don't already have coordinates.
    to_geocode = [r for r in rows if r['lat'] is None or r['lon'] is None]
    if to_geocode:
        print(f"Geocoding {len(to_geocode)} addresses ({len(rows) - len(to_geocode)} pre-geocoded)...")
        geo_results = await geocode_batch([r['addr'] for r in to_geocode], concurrency=8)
        for r, geo in zip(to_geocode, geo_results):
            if geo:
                r['lat'], r['lon'] = geo['lat'], geo['lon']
                r['matched'] = geo.get('matched_address', r['addr'])

    portfolio_id = str(uuid.uuid4())[:8].upper()
    properties   = []
    lats, lons   = [], []

    for r in rows:
        row = r['row']
        cov_raw = str(row.get('coverage_amount', '') or '').replace(',', '').replace('$', '').strip()
        try:
            coverage_amount = float(cov_raw) if cov_raw else 0
        except ValueError:
            coverage_amount = 0

        prop = {
            'property_id':     f"PORT-{portfolio_id}-{str(r['i']+1).zfill(4)}",
            'policy_number':   str(row.get('policy_number', '') or ''),
            'address':         r['addr'] or f"{r['lat']:.4f}, {r['lon']:.4f}",
            'coverage_amount': coverage_amount,
            'matched_address': r.get('matched', ''),
            # Persisted for the zone-summary geographic breakdown.
            'city':            str(row.get('city', '') or ''),
            'state':           str(row.get('state', '') or ''),
            'zip':             str(row.get('zip', '') or ''),
        }

        if r['lat'] is not None and r['lon'] is not None:
            prop['latitude']  = r['lat']
            prop['longitude'] = r['lon']
            lats.append(r['lat'])
            lons.append(r['lon'])
        else:
            prop['latitude']  = None
            prop['longitude'] = None

        properties.append(prop)

    geocoded_count = sum(1 for p in properties if p.get('latitude') is not None)
    addresses = rows  # for the count in the response/log below

    if geocoded_count == 0:
        raise HTTPException(422, "Could not resolve any locations. Check address format or add lat/lon columns.")

    center = {
        'lat': sum(lats) / len(lats),
        'lon': sum(lons) / len(lons),
    }

    save_portfolio(portfolio_id, properties, center, geocoded_count)
    delete_pending_upload(upload_id)
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


@app.get("/api/events/{event_id}/storm-track")
def get_storm_track(event_id: str):
    """
    NHC best-track overlay (simplified, public-domain HURDAT2) for events that
    have one. 404 for events without a track — the UI just draws nothing.
    """
    from backend.storm_tracks import storm_track_geojson
    gj = storm_track_geojson(event_id)
    if gj is None:
        raise HTTPException(404, f"No storm track available for '{event_id}'.")
    return gj


@app.get("/api/gee-status")
def gee_status():
    """
    Honest capability report: can the backend run live, on-demand satellite
    analysis for an arbitrary location, or is it limited to the pre-computed
    demo events? The frontend uses this to label the experience truthfully.
    """
    from backend.live_pipeline import gee_available
    available = gee_available()
    return {
        "live_analysis": available,
        "project": "altis-mvp" if available else None,
        "message": (
            "Live global satellite analysis is enabled — analyze any location on Earth."
            if available else
            "Live analysis is off (no Earth Engine credentials). Pre-computed demo "
            "events still work; add a GEE service-account key to enable global analysis."
        ),
    }


@app.post("/api/portfolio/{portfolio_id}/analyze-live")
def analyze_portfolio_live_endpoint(portfolio_id: str, body: dict = Body(...)):
    """
    Run a REAL Sentinel-1 flood analysis for this portfolio's properties over a
    user-specified event — anywhere on Earth. Body:
      { event_date: 'YYYY-MM-DD' (auto-windowed),  OR
        windows: {pre_start, pre_end, post_start, post_end[, days_since_event]},
        pre_days?: int, post_days?: int   (window size around event_date),
        bbox_filter?: [w,s,e,n]  (scope: analyze only in-zone properties;
                                  out-of-zone rows are marked excluded, not
                                  silently dropped),
        label?: str, wse_radius_m?: int }

    Persists results under the 'live' event key and returns them inline (same
    shape as /analyze) plus run metadata (bbox, scene counts, signal status).
    """
    from backend.live_pipeline import (
        analyze_portfolio_live, derive_windows, LiveAnalysisError)

    props = get_portfolio(portfolio_id)
    if not props:
        raise HTTPException(404, f"Portfolio '{portfolio_id}' not found.")

    event_date = body.get('event_date')
    pre_days = int(body.get('pre_days') or 30)
    post_days = int(body.get('post_days') or 14)

    # Scope filter (zone-scan flow): analyze only properties inside the bbox.
    bbox_filter = body.get('bbox_filter')
    excluded = []
    to_analyze = props
    if bbox_filter and len(bbox_filter) == 4:
        w, s, e, n = (float(v) for v in bbox_filter)
        inside, outside = [], []
        for p in props:
            lat, lon = p.get('latitude'), p.get('longitude')
            if lat is not None and lon is not None and s <= lat <= n and w <= lon <= e:
                inside.append(p)
            else:
                outside.append(p)
        if not inside:
            raise HTTPException(422, "No properties inside the selected zone.")
        to_analyze = inside
        excluded = [{
            **p,
            'impact_class':     'No Coverage',
            'max_depth_ft':     0.0,
            'pct_flooded':      0.0,
            'confidence_score': 0,
            'adjuster_note':    'Outside the event zone — excluded from satellite '
                                'analysis by scope selection.',
            'color':            '#444444',
        } for p in outside]

    windows = body.get('windows')
    if windows is None and event_date:
        windows = derive_windows(event_date, pre_days=pre_days, post_days=post_days)

    try:
        out = analyze_portfolio_live(
            to_analyze,
            event_date=event_date,
            windows=windows,
            wse_radius_m=int(body.get('wse_radius_m', 300)),
            label=str(body.get('label', 'Custom event')),
        )
    except LiveAnalysisError as e:
        # 503 when credentials are missing (a configuration state, not a bug);
        # 422 for bad/empty input.
        msg = str(e)
        code = 503 if 'service-account' in msg or 'authentication' in msg else 422
        raise HTTPException(code, msg)
    except ValueError as e:
        # Typically "No Sentinel-1 images found" for a too-narrow window.
        raise HTTPException(422, f"{e} Try widening the analysis window "
                                 f"(days before/after the event date).")

    results = out['results'] + excluded
    save_analysis_results(portfolio_id, 'live', results)
    save_analysis_meta(portfolio_id, 'live', out['meta'])
    if event_date:
        save_analysis_meta(portfolio_id, 'settings', {
            'event_date': event_date, 'pre_days': pre_days, 'post_days': post_days})

    return {
        "portfolio_id": portfolio_id,
        "event_id":     "live",
        "analyzed":     len(results),
        "results":      results,
        "meta":         out['meta'],
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
        "meta":         get_analysis_meta(portfolio_id, event_id),
        "stats":        _portfolio_stats(results),
    }


@app.get("/api/portfolio/{portfolio_id}/settings")
def get_portfolio_settings(portfolio_id: str):
    """Saved analysis settings (event date + window) for this portfolio."""
    return get_analysis_meta(portfolio_id, 'settings') or {}


@app.put("/api/portfolio/{portfolio_id}/settings")
def put_portfolio_settings(portfolio_id: str, body: dict = Body(...)):
    """Persist analysis settings so a re-run picks up where the user left off."""
    if not get_portfolio(portfolio_id):
        raise HTTPException(404, f"Portfolio '{portfolio_id}' not found.")
    settings = {
        'event_date': str(body.get('event_date') or ''),
        'pre_days':   int(body.get('pre_days') or 7),
        'post_days':  int(body.get('post_days') or 14),
    }
    save_analysis_meta(portfolio_id, 'settings', settings)
    return settings


@app.post("/api/portfolio/{portfolio_id}/zone-summary")
def portfolio_zone_summary(portfolio_id: str, body: dict = Body(default={})):
    """
    Fast PIF zone check — pure coordinate/bbox math, NO Earth Engine calls, so
    it returns in milliseconds. Body: { bbox?: [w,s,e,n] } — typically a
    selected event's bounding box. Without a bbox, the portfolio's own extent
    is used (every geocoded property counts as in-zone) and the response says
    so via zone_source.
    """
    props = get_portfolio(portfolio_id)
    if not props:
        raise HTTPException(404, f"Portfolio '{portfolio_id}' not found.")

    bbox = body.get('bbox')
    zone_source = 'event'
    geocoded = [p for p in props
                if p.get('latitude') is not None and p.get('longitude') is not None]
    failed = len(props) - len(geocoded)

    if not bbox or len(bbox) != 4:
        if not geocoded:
            raise HTTPException(422, "No geocoded properties to summarize.")
        lats = [p['latitude'] for p in geocoded]
        lons = [p['longitude'] for p in geocoded]
        bbox = [min(lons) - 0.05, min(lats) - 0.05, max(lons) + 0.05, max(lats) + 0.05]
        zone_source = 'portfolio_extent'

    w, s, e, n = (float(v) for v in bbox)

    def _cov(p):
        try:
            return float(p.get('coverage_amount') or 0)
        except (TypeError, ValueError):
            return 0.0

    def _region(p):
        state = (p.get('state') or '').strip()
        city = (p.get('city') or '').strip()
        if not (state or city):
            # Fall back to the tail of the address string ("… Lismore NSW").
            tail = [t.strip() for t in str(p.get('address', '')).split(',') if t.strip()]
            return tail[-1] if tail else 'Unspecified'
        return f"{city}, {state}".strip(', ') or 'Unspecified'

    in_zone, out_zone = [], []
    for p in geocoded:
        (in_zone if (s <= p['latitude'] <= n and w <= p['longitude'] <= e)
         else out_zone).append(p)

    breakdown = {}
    for p in in_zone:
        breakdown[_region(p)] = breakdown.get(_region(p), 0) + 1

    return {
        'portfolio_id':   portfolio_id,
        'zone_source':    zone_source,
        'bbox':           [w, s, e, n],
        'total':          len(props),
        'geocoded':       len(geocoded),
        'geocode_failed': failed,
        'in_zone':        len(in_zone),
        'out_zone':       len(out_zone),
        'tiv_total':      int(sum(_cov(p) for p in geocoded)),
        'tiv_in_zone':    int(sum(_cov(p) for p in in_zone)),
        'tiv_out_zone':   int(sum(_cov(p) for p in out_zone)),
        'by_region':      dict(sorted(breakdown.items(), key=lambda kv: -kv[1])),
    }


@app.get("/api/portfolio/{portfolio_id}/risk-score")
def portfolio_risk_score(portfolio_id: str):
    """
    Pre-event flood risk score (1–5) per property — static hazard layers
    (JRC flood history, elevation vs drainage, permanent-water proximity)
    plus FEMA NFHL zone for US properties. The 365-day underwriting/renewals
    view; requires GEE but no storm/event date.
    """
    from backend.live_pipeline import portfolio_risk_scores, LiveAnalysisError

    props = get_portfolio(portfolio_id)
    if not props:
        raise HTTPException(404, f"Portfolio '{portfolio_id}' not found.")
    try:
        out = portfolio_risk_scores(props)
    except LiveAnalysisError as e:
        msg = str(e)
        code = 503 if 'service-account' in msg or 'authentication' in msg else 422
        raise HTTPException(code, msg)

    scores = [r['risk_score'] for r in out['results']]
    return {
        "portfolio_id": portfolio_id,
        "results":      out['results'],
        "method":       out['method'],
        "summary": {
            "total":     len(scores),
            "by_score":  {s: scores.count(s) for s in range(1, 6)},
            "high_risk": sum(1 for s in scores if s >= 4),
        },
    }


@app.get("/api/portfolio/{portfolio_id}/cat-report/{event_id}")
def portfolio_cat_report(portfolio_id: str, event_id: str):
    """
    Reinsurance-format catastrophe report (PDF) for an analyzed portfolio:
    exposure, estimated loss range, triage distribution, methodology.
    """
    from backend.reporting import build_cat_report, ReportError

    results = get_analysis_results(portfolio_id, event_id)
    if not results or not any(r.get('impact_class') for r in results):
        raise HTTPException(404, "No analysis results found. Run analyze first.")
    meta = get_analysis_meta(portfolio_id, event_id) or {}
    label = meta.get('label', 'Live satellite analysis')

    try:
        pdf = build_cat_report(portfolio_id, results, meta, label=label)
    except ReportError as e:
        raise HTTPException(422, str(e))

    return StreamingResponse(
        io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="altis-cat-report-{portfolio_id}.pdf"'})


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


# ── Dispatch queue (severity × coverage ranking) ──────────────────────────────

@app.get("/api/events/{event_id}/dispatch-queue")
def get_dispatch_queue(event_id: str, classes: str = "Dispatch,Review"):
    """
    Return the event's Dispatch (and by default Review) properties ranked by
    severity × financial exposure — the order an adjuster should actually work,
    not a flat list. `classes` is a comma-separated override.
    """
    df = load_event_data(event_id)
    if df is None:
        raise HTTPException(404, f"No data for event '{event_id}'.")

    wanted = tuple(c.strip() for c in classes.split(',') if c.strip())
    records = df.to_dict('records')
    for p in records:
        for k, v in list(p.items()):
            if isinstance(v, float) and v != v:  # NaN
                p[k] = None

    queue = rank_dispatch(records, classes=wanted)
    return {
        "event_id":  event_id,
        "count":     len(queue),
        "queue":     queue,
    }


# ── Adjuster feedback loop (human-in-the-loop ground truth) ───────────────────

@app.post("/api/property/{property_id}/feedback")
def submit_feedback(property_id: str, body: dict = Body(...)):
    """
    Record an adjuster's verdict on a triage decision: agree (thumbs up/down),
    an optional corrected class, and a free-text note. This is the ground-truth
    signal that feeds back into calibration (validation/accuracy_check.py can
    merge it as human labels).
    """
    agree = bool(body.get('agree', True))
    fid = save_feedback(
        property_id=property_id,
        event_id=str(body.get('event_id', '')),
        agree=agree,
        original_class=str(body.get('original_class', '')),
        corrected_class=str(body.get('corrected_class', '')),
        note=str(body.get('note', '')),
        address=str(body.get('address', '')),
        portfolio_id=str(body.get('portfolio_id', '')),
    )
    return {"ok": True, "feedback_id": fid,
            "summary": get_feedback_summary(str(body.get('event_id', '')))}


@app.get("/api/events/{event_id}/feedback")
def list_feedback(event_id: str):
    """All adjuster verdicts for an event plus rollup counts."""
    return {
        "event_id": event_id,
        "summary":  get_feedback_summary(event_id),
        "feedback": get_feedback_for_event(event_id),
    }


# ── Audit-ready PDF report ────────────────────────────────────────────────────

@app.get("/api/events/{event_id}/report")
def event_report(event_id: str):
    """
    Generate and stream an audit-ready PDF: methodology, satellite scene
    sources + dates, triage table, top dispatch priorities, and FEMA-validated
    precision/recall. Reproducible from committed outputs (no live network).
    """
    df = load_event_data(event_id)
    if df is None:
        raise HTTPException(404, f"No data for event '{event_id}'.")

    from backend.reporting import build_event_report, ReportError
    try:
        pdf = build_event_report(event_id, df, get_event_stats(df))
    except ReportError as e:
        raise HTTPException(400, str(e))

    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f"attachment; filename=altis_{event_id}_audit_report.pdf"},
    )


# ── Pipeline runs queue (monitor → pipeline loop) ─────────────────────────────

@app.get("/api/runs")
def get_runs():
    """List queued / running / completed pipeline runs, newest first."""
    return {"runs": list_runs()}


@app.post("/api/runs")
def create_run(body: dict = Body(...)):
    """
    Enqueue a pipeline run. Called manually from the Operations panel and by
    monitor.py when it auto-detects a new flood event — closing the
    detection → analysis loop. Returns the stored run.
    """
    title = str(body.get('title', '')).strip()
    if not title:
        raise HTTPException(400, "A run title is required.")
    run = save_run(
        title=title,
        source=str(body.get('source', 'manual')),
        event_id=str(body.get('event_id', '')),
        status=str(body.get('status', 'queued')),
        bbox=body.get('bbox'),
        note=str(body.get('note', '')),
        detected_at=str(body.get('detected_at', '')),
    )
    return {"ok": True, "run": run}


@app.post("/api/runs/{run_id}/status")
def set_run_status(run_id: str, body: dict = Body(...)):
    """Advance a run's status (queued → running → complete/failed)."""
    status = str(body.get('status', '')).strip()
    if status not in ('queued', 'running', 'complete', 'failed'):
        raise HTTPException(400, "status must be queued|running|complete|failed.")
    update_run_status(run_id, status)
    return {"ok": True, "run_id": run_id, "status": status}


# ── Chat ("Ask about this area") ──────────────────────────────────────────────

@app.post("/api/chat")
def chat(body: dict = Body(...)):
    """
    Grounded Q&A about the event/property currently on screen. Forwards to
    OpenRouter (Claude Haiku 4.5) with the on-screen data as context — the
    frontend sends whatever event/property it currently has loaded so the
    assistant never has to re-derive (or guess) what the user is looking at.
    """
    from backend.chat import ask, ChatError

    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(400, "message is required.")

    # When a portfolio is loaded, pull its stored analysis straight from the
    # DB so the assistant can answer book-of-business questions (exposure,
    # est. loss, worst properties, flags) without the frontend shipping the
    # whole result set on every keystroke.
    portfolio_summary = None
    pid = body.get("portfolio_id")
    if pid:
        results = get_analysis_results(pid, 'live') or []
        analyzed = [r for r in results if r.get('impact_class')]
        if analyzed:
            meta = get_analysis_meta(pid, 'live') or {}

            def _n(v):
                try:
                    return float(v or 0)
                except (TypeError, ValueError):
                    return 0.0

            top = sorted(analyzed, key=lambda r: -_n(r.get('severity_high_usd')))[:5]
            portfolio_summary = {
                'portfolio_id': pid,
                'exposure': meta.get('exposure'),
                'signal_status': meta.get('signal_status'),
                'event_windows': meta.get('windows'),
                'class_counts': {c: sum(1 for r in analyzed if r['impact_class'] == c)
                                 for c in ('Dispatch', 'Review', 'Remote-Approve',
                                           'Remote-Deny', 'No Coverage')},
                'flags': {
                    'subrogation_candidates': sum(1 for r in analyzed if r.get('subrogation_flag')),
                    'surge_verification_suggested': sum(1 for r in analyzed if r.get('surge_check_flag')),
                    'vegetation_damage': sum(1 for r in analyzed if r.get('vegetation_loss')),
                    'sfha_properties': sum(1 for r in analyzed if r.get('sfha_flag')),
                },
                'largest_estimated_losses': [{
                    'address': r.get('address'),
                    'depth_ft': r.get('max_depth_ft'),
                    'severity_range_usd': [r.get('severity_low_usd'), r.get('severity_high_usd')],
                    'class': r.get('impact_class'),
                } for r in top if r.get('severity_high_usd') is not None],
            }

    try:
        reply = ask(
            message=message,
            history=body.get("history") or [],
            event_meta=body.get("event_meta"),
            event_stats=body.get("event_stats"),
            property_row=body.get("property"),
            portfolio_summary=portfolio_summary,
        )
    except ChatError as e:
        raise HTTPException(502, str(e))

    return {"reply": reply}


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
