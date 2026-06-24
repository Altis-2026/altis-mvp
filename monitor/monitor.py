#!/usr/bin/env python3
"""
monitor.py — Altis event monitoring service.

Watches three sources for new US flood events:
  1. NHC RSS feed (tropical cyclones)
  2. USGS Water Services API (stream gauge flood stage)
  3. Copernicus EMS (emergency SAR activations)

When a significant event is detected:
  - Logs it to events.log
  - Defines a bounding box for the affected area
  - (MVP) Prints alert; (v2) triggers GEE pipeline automatically

Run hourly via cron:
    0 * * * * /usr/bin/python3 /path/to/altis-mvp/monitor/monitor.py

Or run continuously:
    python monitor/monitor.py --loop
"""
import argparse
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

BASE_DIR  = Path(__file__).parent.parent
LOG_FILE  = BASE_DIR / 'monitor' / 'events.log'
STATE_FILE = BASE_DIR / 'monitor' / 'state.json'

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Make the backend DB layer importable so detection can enqueue a pipeline run.
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('altis.monitor')

# ── NHC RSS ───────────────────────────────────────────────────────────────────

NHC_RSS_URLS = [
    "https://www.nhc.noaa.gov/nhc_atl.xml",   # Atlantic basin
    "https://www.nhc.noaa.gov/nhc_epac.xml",   # East Pacific
]

SIGNIFICANT_ADVISORIES = {'hurricane', 'tropical storm', 'subtropical storm'}

def check_nhc() -> list[dict]:
    """
    Parse NHC RSS feeds and return any active advisory for significant systems.
    Returns list of event dicts.
    """
    events = []
    for url in NHC_RSS_URLS:
        try:
            resp = requests.get(url, timeout=15, headers={'User-Agent': 'Altis-Monitor/1.0'})
            resp.raise_for_status()

            root = ET.fromstring(resp.text)
            ns   = {'geo': 'http://www.w3.org/2003/01/geo/wgs84_pos#'}

            for item in root.findall('.//item'):
                title    = item.findtext('title', '').lower()
                link     = item.findtext('link', '')
                pub_date = item.findtext('pubDate', '')

                if not any(sig in title for sig in SIGNIFICANT_ADVISORIES):
                    continue

                # Extract coordinates if present in geo tags
                lat = item.findtext('geo:lat', namespaces=ns)
                lon = item.findtext('geo:long', namespaces=ns)

                events.append({
                    'source':    'NHC',
                    'type':      'tropical_cyclone',
                    'title':     item.findtext('title', ''),
                    'link':      link,
                    'pub_date':  pub_date,
                    'lat':       float(lat) if lat else None,
                    'lon':       float(lon) if lon else None,
                    'detected':  datetime.utcnow().isoformat(),
                })

            log.debug(f"NHC: checked {url} OK")

        except Exception as e:
            log.warning(f"NHC feed error ({url}): {e}")

    return events


# ── USGS Flood Gauges ─────────────────────────────────────────────────────────

# US states most affected by major flood events
WATCH_STATES = ['TX', 'FL', 'LA', 'NC', 'SC', 'GA', 'AL', 'MS', 'VA', 'MD']

FLOOD_THRESHOLD_FT = 15.0  # Alert when any gauge exceeds this stage

def check_usgs_gauges() -> list[dict]:
    """
    Query USGS Water Services API for stream gauges at or above major flood stage.
    Returns list of event dicts for significantly elevated gauges.
    """
    events = []
    for state in WATCH_STATES:
        try:
            url  = "https://waterservices.usgs.gov/nwis/iv/"
            resp = requests.get(url, params={
                'format':       'json',
                'stateCd':      state,
                'parameterCd':  '00065',   # Gauge height in feet
                'siteStatus':   'active',
            }, timeout=20, headers={'User-Agent': 'Altis-Monitor/1.0'})
            resp.raise_for_status()

            data = resp.json()
            ts   = data.get('value', {}).get('timeSeries', [])

            for series in ts:
                values = series.get('values', [{}])[0].get('value', [])
                if not values:
                    continue

                try:
                    stage_ft = float(values[-1]['value'])
                except (ValueError, TypeError):
                    continue

                if stage_ft < FLOOD_THRESHOLD_FT:
                    continue

                site   = series.get('sourceInfo', {})
                site_no = site.get('siteCode', [{}])[0].get('value', '')
                name    = site.get('siteName', '')
                geo     = site.get('geoLocation', {}).get('geogLocation', {})

                log.info(f"USGS ALERT: {name} ({state}) at {stage_ft:.1f}ft")

                events.append({
                    'source':    'USGS',
                    'type':      'river_flood',
                    'title':     f"{name} at {stage_ft:.1f}ft stage",
                    'site_no':   site_no,
                    'state':     state,
                    'stage_ft':  stage_ft,
                    'lat':       float(geo.get('latitude', 0)) or None,
                    'lon':       float(geo.get('longitude', 0)) or None,
                    'detected':  datetime.utcnow().isoformat(),
                })

        except Exception as e:
            log.warning(f"USGS gauge error ({state}): {e}")

    return events


# ── Copernicus EMS ────────────────────────────────────────────────────────────

COPERNICUS_URL = "https://emergency.copernicus.eu/mapping/list-of-components/EMSR"

def check_copernicus_ems() -> list[dict]:
    """
    Check Copernicus Emergency Management Service for recent US activations.
    These indicate events where emergency SAR tasking has been ordered.
    MVP: basic check for recent US activations in the page text.
    """
    events = []
    try:
        resp = requests.get(
            "https://emergency.copernicus.eu/mapping/list-of-activations-rapid",
            timeout=20,
            headers={'User-Agent': 'Altis-Monitor/1.0'}
        )
        # Simple text search for recent US events in HTML
        text = resp.text.lower()
        if 'united states' in text or 'usa' in text or ' tx' in text or ' fl' in text:
            # Would parse the table in production; for MVP just flag for manual check
            log.info("Copernicus EMS: US-related content detected — manual review recommended")

    except Exception as e:
        log.warning(f"Copernicus EMS check failed: {e}")

    return events


# ── State management ──────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {'seen_events': [], 'last_check': None}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def is_new_event(event: dict, seen: list[str]) -> bool:
    """Deduplicate by source + title hash."""
    key = f"{event['source']}:{event['title']}"
    return key not in seen


# ── Bounding box suggestion ───────────────────────────────────────────────────

def suggest_bbox(event: dict) -> Optional[list[float]]:
    """
    Suggest a bounding box for the affected area based on event coordinates.
    Returns [west, south, east, north] or None.

    In v2, this would use HURDAT track data for tropical storms.
    For the MVP, we return a generous 2° buffer around the detected point.
    """
    lat, lon = event.get('lat'), event.get('lon')
    if not lat or not lon:
        return None

    buf = 2.0  # degrees (~220km radius)
    return [lon - buf, lat - buf, lon + buf, lat + buf]


# ── Pipeline enqueue (close the detection → analysis loop) ────────────────────

def enqueue_run(event: dict, bbox: Optional[list[float]]) -> Optional[str]:
    """
    Persist a queued pipeline run for a freshly detected event so it shows up in
    the Operations panel and can be promoted to an actual GEE run. Writes
    straight to the shared SQLite DB (no HTTP), so the monitor closes the loop
    even when the API server isn't running. Returns the run id, or None if the
    backend isn't importable in this environment.
    """
    try:
        from backend import database as db
        db.init_db()
        run = db.save_run(
            title=event.get('title', 'Detected flood event'),
            source=f"monitor:{event.get('source', '?').lower()}",
            status='queued',
            bbox=bbox,
            note=f"{event.get('type', 'event')} — auto-detected; "
                 f"lat={event.get('lat')}, lon={event.get('lon')}",
            detected_at=event.get('detected', ''),
        )
        return run['id']
    except Exception as e:
        log.warning(f"Could not enqueue pipeline run: {e}")
        return None


# ── Main monitoring loop ──────────────────────────────────────────────────────

def run_once(enqueue: bool = True):
    """Run one monitoring check cycle. When enqueue is True, newly detected
    events are written to the pipeline-runs queue (the monitor → pipeline loop)."""
    log.info("=" * 50)
    log.info("Altis Monitor — check cycle starting")
    log.info("=" * 50)

    state       = load_state()
    seen        = set(state.get('seen_events', []))
    new_events  = []

    # Check all sources
    nhc_events  = check_nhc()
    usgs_events = check_usgs_gauges()
    # copernicus_events = check_copernicus_ems()  # Enable for production

    all_events = nhc_events + usgs_events

    for event in all_events:
        key = f"{event['source']}:{event['title']}"
        if is_new_event(event, seen):
            new_events.append(event)
            seen.add(key)
            log.info(f"NEW EVENT DETECTED:")
            log.info(f"  Source:   {event['source']}")
            log.info(f"  Type:     {event['type']}")
            log.info(f"  Title:    {event['title']}")
            log.info(f"  Location: lat={event.get('lat')}, lon={event.get('lon')}")

            bbox = suggest_bbox(event)
            if bbox:
                log.info(f"  Suggested bbox: {bbox}")
            else:
                log.info(f"  ► No coordinates available — check NHC/USGS source")

            if enqueue:
                run_id = enqueue_run(event, bbox)
                if run_id:
                    log.info(f"  ► Queued pipeline run {run_id} (status=queued) "
                             f"— visible in the Altis Operations panel")
            else:
                log.info(f"  ► Add to pipeline/config.py and run pipeline manually")

            log.info("")

    if not new_events:
        log.info(f"No new significant events. Checked {len(all_events)} total signals.")

    # Update state
    state['seen_events'] = list(seen)[-500:]  # Keep last 500
    state['last_check']  = datetime.utcnow().isoformat()
    save_state(state)

    log.info(f"Check complete. {len(new_events)} new events detected.")
    return new_events


def run_loop(interval_minutes: int = 60, enqueue: bool = True):
    """Run monitor in continuous loop."""
    log.info(f"Altis Monitor starting — checking every {interval_minutes} minutes")
    while True:
        try:
            run_once(enqueue=enqueue)
        except Exception as e:
            log.error(f"Monitor cycle failed: {e}")
        log.info(f"Sleeping {interval_minutes} minutes until next check...")
        time.sleep(interval_minutes * 60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Altis event monitor')
    parser.add_argument('--loop', action='store_true', help='Run continuously (default: run once)')
    parser.add_argument('--interval', type=int, default=60, help='Check interval in minutes')
    parser.add_argument('--no-enqueue', action='store_true',
                        help="Don't queue a pipeline run for detected events")
    args = parser.parse_args()

    enqueue = not args.no_enqueue
    if args.loop:
        run_loop(args.interval, enqueue=enqueue)
    else:
        events = run_once(enqueue=enqueue)
        sys.exit(0 if not events else 1)
