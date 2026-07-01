"""
database.py — In-memory event data + SQLite for carrier portfolios.

Event data (Harvey / Ian) loads from outputs/*.csv at startup.
Portfolio data lives in SQLite so it persists across server restarts.
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime
import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
OUTPUT_DIR  = BASE_DIR / 'outputs'
DB_PATH     = BASE_DIR / 'altis.db'
CACHE_DIR   = BASE_DIR / 'cache' / 'sar'

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── In-memory event data (loaded once at startup) ────────────────────────────

_event_cache: dict = {}

def load_event_data(event_id: str) -> pd.DataFrame | None:
    """
    Load and merge final triage + lat/lon for an event.
    Cached in memory after first load.
    """
    if event_id in _event_cache:
        return _event_cache[event_id]

    final_path = OUTPUT_DIR / f"{event_id}_final.csv"
    props_path = OUTPUT_DIR / f"{event_id}_properties.csv"

    if not final_path.exists():
        return None

    df = pd.read_csv(final_path)

    # Merge lat/lon from properties CSV if available and not already in final
    if props_path.exists() and 'latitude' not in df.columns:
        props = pd.read_csv(props_path)[['property_id', 'latitude', 'longitude']]
        df = df.merge(props, on='property_id', how='left')

    # Assign pin colors
    COLOR_MAP = {
        'Dispatch':       '#FF4444',
        'Remote-Approve': '#4CAF82',
        'Remote-Deny':    '#6B8FA3',
        'Review':         '#FFB347',
    }
    df['color'] = df['impact_class'].map(COLOR_MAP).fillna('#6B8FA3')

    # Drop rows without coordinates
    df = df.dropna(subset=['latitude', 'longitude'])

    _event_cache[event_id] = df
    return df


def get_event_stats(df: pd.DataFrame) -> dict:
    total         = len(df)
    dispatch      = int((df['impact_class'] == 'Dispatch').sum())
    remote_approve = int((df['impact_class'] == 'Remote-Approve').sum())
    remote_deny   = int((df['impact_class'] == 'Remote-Deny').sum())
    review        = int((df['impact_class'] == 'Review').sum())
    remote_total  = remote_approve + remote_deny
    return {
        'total':            total,
        'dispatch':         dispatch,
        'remote_approve':   remote_approve,
        'remote_deny':      remote_deny,
        'review':           review,
        'remote_total':     remote_total,
        'estimated_savings': remote_total * 750,
        'pct_remote':       round(remote_total / total * 100, 1) if total > 0 else 0,
    }


# ── SQLite: portfolios ────────────────────────────────────────────────────────

def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolios (
            id TEXT PRIMARY KEY,
            created_at TEXT DEFAULT (datetime('now')),
            total_count INTEGER,
            geocoded_count INTEGER,
            center_lat REAL,
            center_lon REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_properties (
            portfolio_id TEXT,
            property_id TEXT,
            policy_number TEXT,
            address TEXT,
            coverage_amount REAL,
            latitude REAL,
            longitude REAL,
            matched_address TEXT,
            PRIMARY KEY (portfolio_id, property_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_results (
            portfolio_id TEXT,
            event_id TEXT,
            property_id TEXT,
            impact_class TEXT,
            max_depth_ft REAL,
            pct_flooded REAL,
            confidence_score INTEGER,
            adjuster_note TEXT,
            PRIMARY KEY (portfolio_id, event_id, property_id)
        )
    """)
    # Round-7 fields (severity $, rainfall, FEMA zone, duration, cross-checks…)
    # ride in one JSON column so reloading a saved analysis keeps everything.
    try:
        conn.execute("ALTER TABLE analysis_results ADD COLUMN extra_json TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_meta (
            portfolio_id TEXT,
            event_id TEXT,
            meta_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (portfolio_id, event_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_uploads (
            id TEXT PRIMARY KEY,
            created_at TEXT DEFAULT (datetime('now')),
            filename TEXT,
            raw_json TEXT,
            suggested_mapping_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS adjuster_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT (datetime('now')),
            property_id TEXT,
            event_id TEXT,
            portfolio_id TEXT,
            agree INTEGER,
            original_class TEXT,
            corrected_class TEXT,
            note TEXT,
            address TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id TEXT PRIMARY KEY,
            created_at TEXT DEFAULT (datetime('now')),
            detected_at TEXT,
            event_id TEXT,
            title TEXT,
            source TEXT,
            status TEXT,
            bbox_json TEXT,
            note TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_portfolio(portfolio_id: str, properties: list, center: dict,
                   geocoded_count: int):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT OR REPLACE INTO portfolios
        (id, total_count, geocoded_count, center_lat, center_lon)
        VALUES (?, ?, ?, ?, ?)
    """, (portfolio_id, len(properties), geocoded_count,
          center['lat'], center['lon']))

    conn.executemany("""
        INSERT OR REPLACE INTO portfolio_properties
        (portfolio_id, property_id, policy_number, address,
         coverage_amount, latitude, longitude, matched_address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [(portfolio_id, p['property_id'], p.get('policy_number', ''),
           p['address'], p.get('coverage_amount', 0),
           p['latitude'], p['longitude'], p.get('matched_address', ''))
          for p in properties])

    conn.commit()
    conn.close()


def list_portfolios() -> list:
    """Return summary metadata for every saved portfolio, newest first."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, created_at, total_count, geocoded_count, center_lat, center_lon
        FROM portfolios
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_portfolio(portfolio_id: str) -> list | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM portfolio_properties WHERE portfolio_id = ?",
        (portfolio_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows] if rows else None


# Columns stored natively in analysis_results; everything else in a result row
# is preserved via extra_json so richer live-analysis fields survive reloads.
_NATIVE_RESULT_KEYS = {
    'portfolio_id', 'event_id', 'property_id', 'impact_class', 'max_depth_ft',
    'pct_flooded', 'confidence_score', 'adjuster_note',
    # portfolio_properties columns (re-joined on read, don't duplicate):
    'policy_number', 'address', 'coverage_amount', 'latitude', 'longitude',
    'matched_address',
}


def save_analysis_results(portfolio_id: str, event_id: str, results: list):
    import json as _json
    conn = sqlite3.connect(str(DB_PATH))
    conn.executemany("""
        INSERT OR REPLACE INTO analysis_results
        (portfolio_id, event_id, property_id, impact_class,
         max_depth_ft, pct_flooded, confidence_score, adjuster_note, extra_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(portfolio_id, event_id,
           r['property_id'], r['impact_class'], r['max_depth_ft'],
           r['pct_flooded'], r['confidence_score'], r['adjuster_note'],
           _json.dumps({k: v for k, v in r.items()
                        if k not in _NATIVE_RESULT_KEYS}))
          for r in results])
    conn.commit()
    conn.close()


def get_analysis_results(portfolio_id: str, event_id: str) -> list | None:
    import json as _json
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT pp.*, ar.impact_class, ar.max_depth_ft, ar.pct_flooded,
               ar.confidence_score, ar.adjuster_note, ar.extra_json
        FROM portfolio_properties pp
        LEFT JOIN analysis_results ar
          ON ar.portfolio_id = pp.portfolio_id
          AND ar.event_id = ?
          AND ar.property_id = pp.property_id
        WHERE pp.portfolio_id = ?
    """, (event_id, portfolio_id)).fetchall()
    conn.close()
    if not rows:
        return None
    out = []
    for r in rows:
        d = dict(r)
        extra = d.pop('extra_json', None)
        if extra:
            try:
                d.update(_json.loads(extra))
            except (ValueError, TypeError):
                pass
        out.append(d)
    return out


def save_analysis_meta(portfolio_id: str, event_id: str, meta: dict):
    import json as _json
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT OR REPLACE INTO analysis_meta (portfolio_id, event_id, meta_json)
        VALUES (?, ?, ?)
    """, (portfolio_id, event_id, _json.dumps(meta)))
    conn.commit()
    conn.close()


def get_analysis_meta(portfolio_id: str, event_id: str) -> dict | None:
    import json as _json
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT meta_json FROM analysis_meta WHERE portfolio_id = ? AND event_id = ?",
        (portfolio_id, event_id)).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    try:
        return _json.loads(row[0])
    except (ValueError, TypeError):
        return None


def get_analyzed_depth(property_id: str) -> float | None:
    """
    Largest flood depth recorded for a property across any saved portfolio
    analysis. Lets the SAR thumbnail endpoint render the flood signature for
    an *uploaded* property once it has been analyzed against an event — event
    data alone doesn't know about portfolio property ids.
    """
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT MAX(max_depth_ft) FROM analysis_results WHERE property_id = ?",
        (property_id,),
    ).fetchone()
    conn.close()
    return float(row[0]) if row and row[0] is not None else None


# ── SAR thumbnail helpers ─────────────────────────────────────────────────────

def get_cached_thumbnail(property_id: str, is_post: bool) -> str | None:
    """Return cached GEE thumbnail base64 if it exists."""
    label = 'post' if is_post else 'pre'
    path  = CACHE_DIR / f"{property_id}_{label}.b64"
    if path.exists():
        return path.read_text()
    return None


def save_thumbnail_cache(property_id: str, is_post: bool, data_url: str):
    label = 'post' if is_post else 'pre'
    path  = CACHE_DIR / f"{property_id}_{label}.b64"
    path.write_text(data_url)


# ── SQLite: pending uploads (bridge between upload preview + confirm) ────────

def save_pending_upload(filename: str, raw_rows: list, suggested_mapping: dict) -> str:
    """Persist a parsed-but-unconfirmed upload. Returns the new upload_id."""
    upload_id = uuid.uuid4().hex[:12]
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT INTO pending_uploads (id, filename, raw_json, suggested_mapping_json)
        VALUES (?, ?, ?, ?)
    """, (upload_id, filename, json.dumps(raw_rows), json.dumps(suggested_mapping)))
    conn.commit()
    conn.close()
    return upload_id


def get_pending_upload(upload_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM pending_uploads WHERE id = ?", (upload_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d['raw_rows'] = json.loads(d.pop('raw_json'))
    d['suggested_mapping'] = json.loads(d.pop('suggested_mapping_json'))
    return d


def delete_pending_upload(upload_id: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM pending_uploads WHERE id = ?", (upload_id,))
    conn.commit()
    conn.close()


# ── SQLite: adjuster feedback (human-in-the-loop ground truth) ──────────────

def save_feedback(property_id: str, event_id: str, agree: bool,
                  original_class: str = '', corrected_class: str = '',
                  note: str = '', address: str = '', portfolio_id: str = '') -> int:
    """Persist one adjuster verdict on a property. Returns the new row id."""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute("""
        INSERT INTO adjuster_feedback
        (property_id, event_id, portfolio_id, agree, original_class,
         corrected_class, note, address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (property_id, event_id, portfolio_id, 1 if agree else 0,
          original_class, corrected_class, note, address))
    conn.commit()
    fid = cur.lastrowid
    conn.close()
    return fid


def get_feedback_for_event(event_id: str) -> list:
    """Most recent verdict per property for an event, newest first."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM adjuster_feedback
        WHERE event_id = ?
        ORDER BY created_at DESC
    """, (event_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_feedback_summary(event_id: str) -> dict:
    """Counts used by the UI badge: total verdicts, agree, disagree, corrected."""
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(agree), 0) AS agreed,
               COALESCE(SUM(CASE WHEN corrected_class != '' AND corrected_class IS NOT NULL
                                 THEN 1 ELSE 0 END), 0) AS corrected
        FROM adjuster_feedback WHERE event_id = ?
    """, (event_id,)).fetchone()
    conn.close()
    total, agreed, corrected = (row[0] or 0), (row[1] or 0), (row[2] or 0)
    return {'total': total, 'agreed': agreed,
            'disagreed': total - agreed, 'corrected': corrected}


# ── SQLite: pipeline runs (monitor → pipeline queue) ────────────────────────

def save_run(title: str, source: str, event_id: str = '', status: str = 'queued',
             bbox: list = None, note: str = '', detected_at: str = '') -> dict:
    """Enqueue (or record) a pipeline run. Returns the stored row as a dict."""
    run_id = uuid.uuid4().hex[:12]
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT INTO pipeline_runs
        (id, detected_at, event_id, title, source, status, bbox_json, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (run_id, detected_at or datetime.utcnow().isoformat(), event_id, title,
          source, status, json.dumps(bbox) if bbox else None, note))
    conn.commit()
    conn.close()
    return get_run(run_id)


def get_run(run_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d['bbox'] = json.loads(d.pop('bbox_json')) if d.get('bbox_json') else None
    return d


def list_runs(limit: int = 50) -> list:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM pipeline_runs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d['bbox'] = json.loads(d.pop('bbox_json')) if d.get('bbox_json') else None
        out.append(d)
    return out


def update_run_status(run_id: str, status: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE pipeline_runs SET status = ? WHERE id = ?", (status, run_id))
    conn.commit()
    conn.close()
