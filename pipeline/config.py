# config.py — Central configuration for the Altis pipeline
import os
from dotenv import load_dotenv

load_dotenv()

# Bump when triage logic, confidence scoring, or the flood-detection algorithm
# changes meaningfully — recorded in each run's manifest for audit/lineage.
PIPELINE_VERSION = '1.0.0'

# ─── GOOGLE EARTH ENGINE ─────────────────────────────────────────────────────
GEE_PROJECT = 'altis-mvp'  # <-- change this to your project ID

# ─── ANTHROPIC ───────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

# ─── OUTPUT DIRECTORY (absolute path, works regardless of where script is run)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')

# ─── HURRICANE HARVEY CONFIGURATION ──────────────────────────────────────────
HARVEY = {
    'event_name':         'Hurricane Harvey',
    'event_id':           'harvey',
    'label':              'Hurricane Harvey',
    'sub':                'Harris County, TX  •  August 2017',
    'county':             'Harris County, TX',
    'pre_start':          '2017-08-01',
    'pre_end':            '2017-08-24',
    'post_start':         '2017-08-27',
    'post_end':           '2017-09-10',
    # Bounding box [west, south, east, north] — Meyerland + Braeswood (most documented Harvey flooding)
    'bbox':               [-95.60, 29.62, -95.38, 29.80],
    'study_name':         'Meyerland and Braeswood, Houston TX',
    'days_since_event':   3,
    'wse_radius_m':       300,    # Water surface elevation focal radius — tighter for rainfall flooding
    'cost_per_inspection': 750,
    'lat':                29.700,
    'lon':               -95.500,
    'zoom':               10,
}

# ─── HURRICANE IAN CONFIGURATION ─────────────────────────────────────────────
IAN = {
    'event_name':         'Hurricane Ian',
    'event_id':           'ian',
    'label':              'Hurricane Ian',
    'sub':                'Charlotte County, FL  •  September 2022',
    'county':             'Charlotte County, FL',
    'pre_start':          '2022-09-01',
    'pre_end':            '2022-09-25',
    'post_start':         '2022-09-28',
    'post_end':           '2022-10-12',
    # Bounding box [west, south, east, north] — Port Charlotte + Punta Gorda
    'bbox':               [-82.15, 26.88, -81.92, 27.08],
    'study_name':         'Port Charlotte and Punta Gorda, FL',
    'days_since_event':   2,
    'wse_radius_m':       600,    # Wider for storm surge (more spatially uniform WSE)
    'cost_per_inspection': 750,
    'lat':                26.970,
    'lon':               -82.050,
    'zoom':               10,
}

# ─── EVENT REGISTRY ───────────────────────────────────────────────────────────
# Single source of truth for which events the backend/frontend can serve.
# Add a new storm by adding its config dict above and registering it here.
EVENTS = {
    cfg['event_id']: {
        'id':    cfg['event_id'],
        'label': cfg['label'],
        'sub':   cfg['sub'],
        'lat':   cfg['lat'],
        'lon':   cfg['lon'],
        'zoom':  cfg['zoom'],
    }
    for cfg in (HARVEY, IAN)
}

# ─── TRIAGE THRESHOLDS ────────────────────────────────────────────────────────
TRIAGE = {
    'dispatch_depth_ft':          3.0,   # Depth above which always dispatch
    'dispatch_low_depth_ft':      1.0,   # Dispatch at this depth IF coverage also high
    'dispatch_pct':               0.50,  # Coverage threshold paired with low depth dispatch
    'remote_deny_depth_ft':       0.30,  # Max depth for remote deny
    'remote_deny_pct':            0.05,  # Max coverage for remote deny (5%)
    'remote_deny_conf':           80,    # Min confidence for remote deny
    'remote_approve_min_depth':   0.50,  # Min depth for remote approve
    'remote_approve_max_depth':   3.0,   # Max depth for remote approve
    'remote_approve_min_pct':     0.20,  # Min coverage for remote approve (20%)
    'remote_approve_conf':        78,    # Min confidence for remote approve
}

# ─── SAR DETECTION PARAMETERS ─────────────────────────────────────────────────
# Tunable knobs for the Sentinel-1 flood-detection step (03_flood_pipeline.py).
SAR = {
    # Speckle filter: Sentinel-1 GRD is noisy; a focal-mean smooth before
    # thresholding is UN-SPIDER recommended practice and sharply cuts false
    # positives. Radius in meters; set to 0 to disable.
    'speckle_radius_m':       50,

    # Otsu range guard. Open-water VV backscatter sits roughly in this band.
    # If the per-scene Otsu threshold lands outside it (e.g. a unimodal scene
    # with little/no water), we fall back to a safe default instead of using a
    # garbage threshold that floods the whole map.
    'water_db_min':          -22.0,
    'water_db_max':          -12.0,
    'otsu_fallback_db':      -16.0,

    # Water-surface-elevation (WSE) estimator. The waterline sits at the HIGHER
    # elevations among flooded pixels, but a plain focal_max is dominated by a
    # single spurious high pixel and produces absurd depths. A high percentile
    # is robust to those outliers while still tracking the waterline.
    'wse_percentile':         90,

    # Physical depth cap. Residential flood depths above this are almost
    # certainly DEM/WSE artifacts, not real water. Depth is clamped here so a
    # single-family home never reports a 40-foot flood.
    'max_plausible_depth_ft': 20.0,
}