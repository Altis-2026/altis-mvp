# config.py — Central configuration for the Altis pipeline
import os
from dotenv import load_dotenv

load_dotenv()

# Bump when triage logic, confidence scoring, or the flood-detection algorithm
# changes meaningfully — recorded in each run's manifest for audit/lineage.
PIPELINE_VERSION = '1.0.0'

# ─── GOOGLE EARTH ENGINE ─────────────────────────────────────────────────────
GEE_PROJECT = os.getenv('GEE_PROJECT', 'altis-mvp')

# Path to a GEE service-account JSON key. When set (and the file exists), the
# backend can authenticate non-interactively and run live, on-demand flood
# analysis for ANY location on Earth. When unset, live analysis is disabled and
# the app falls back to the pre-computed demo events + synthetic preview imagery
# — surfaced honestly via GET /api/gee-status rather than pretending to work.
#
# Default search order (first existing wins): the env var, then a few
# conventional locations so local setup is "drop the key here and go".
_BASE_DIR_CFG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GEE_KEY_CANDIDATES = [
    os.getenv('GEE_SERVICE_ACCOUNT_KEY', ''),
    os.path.join(_BASE_DIR_CFG, 'secrets', 'ee-sa-key.json'),
]
GEE_SERVICE_ACCOUNT_KEY = next(
    (p for p in _GEE_KEY_CANDIDATES if p and os.path.exists(p)), None)

# ─── MAPBOX (global geocoding) ───────────────────────────────────────────────
# Mapbox geocodes worldwide (unlike the US-only Census geocoder), so uploaded
# portfolios resolve anywhere — Colombia, Spain, anywhere. Reuses the same token
# the frontend map already uses. Backend reads MAPBOX_TOKEN; if unset, the
# geocoder falls back to the US Census service.
MAPBOX_TOKEN = os.getenv('MAPBOX_TOKEN') or os.getenv('VITE_MAPBOX_TOKEN')

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

# ─── OPTICAL CROSS-CHECK PARAMETERS (Round 2) ────────────────────────────────
# Sentinel-2 MNDWI is used as an independent second sensor to confirm or
# contradict the SAR flood call. SAR alone is prone to false positives in
# urban canyons (radar shadow/double-bounce looks like water) and on smooth
# surfaces (runways, wet pavement). Optical is advisory only — clouds are the
# norm right after a hurricane, so this never blocks detection, it only
# refines confidence in Step 4 when a cloud-free observation exists.
OPTICAL = {
    'max_cloud_pct':       40,    # Skip S2 scenes cloudier than this (CLOUDY_PIXEL_PERCENTAGE)
    'water_mndwi_min':      0.0,  # MNDWI > 0 indicates open water
    'min_valid_fraction':   0.5,  # Min cloud-free fraction of a property buffer to trust optical
    'sar_flood_pct':        0.10, # pct_flooded above which SAR is considered to say "flooded"
    'water_confirm_pct':    0.10, # optical_water_pct above which optical is considered to say "water"
    'water_contradict_pct': 0.05, # optical_water_pct below which optical is considered to say "dry"
    'confirm_bonus':        10,   # Confidence bonus when SAR + optical agree on flood
    'confirm_dry_bonus':     6,   # Confidence bonus when SAR + optical agree on dry
    'contradict_penalty':  -12,   # Confidence penalty when optical contradicts a SAR flood call
}

# ─── DUAL-POLARIZATION CROSS-CHECK (Round 7) ─────────────────────────────────
# Sentinel-1 IW scenes carry both VV and VH polarizations. Water suppresses
# backscatter in BOTH channels, but the noise/artifact modes differ (VH is more
# sensitive to volume scattering, less to specular double-bounce), so running
# the same change detection independently on VH gives a second, partially
# independent flood vote from the SAME satellite pass. True InSAR coherence
# change detection needs SLC phase data, which Google Earth Engine does not
# distribute (GRD only) — this amplitude-based dual-pol check is the honest,
# implementable equivalent and is standard practice in operational flood
# mapping. VH open-water backscatter sits lower than VV.
SAR_VH = {
    'water_db_min':     -30.0,
    'water_db_max':     -18.0,
    'otsu_fallback_db': -24.0,
    'flood_pct':         0.10,  # vh_water_pct >= this → VH votes "flood"
    'dry_pct':           0.03,  # vh_water_pct <  this → VH votes "dry"
    'agree_bonus':        6,    # confidence when VV + VH agree on flood
    'agree_dry_bonus':    4,    # confidence when VV + VH agree on dry
    'disagree_penalty':  -9,    # confidence when VH contradicts the VV call
    'downgrade_to_review': True,  # VV says flood, VH says dry → manual Review
}

# ─── INUNDATION DURATION (Round 7) ───────────────────────────────────────────
# The post-event window is split into consecutive slices; a flood mask is built
# per slice (same threshold + baseline) and each property's flooded fraction is
# sampled per slice. Duration ≈ sum of slice lengths where the property reads
# flooded. Slices without a Sentinel-1 scene abstain, and duration is reported
# as unknown when fewer than 2 slices had data — never fabricated.
DURATION = {
    'n_slices':        3,
    'slice_flood_pct': 0.15,  # per-slice flooded fraction to count as "still inundated"
}

# ─── RAINFALL (Round 7) ──────────────────────────────────────────────────────
# CHIRPS daily precipitation (global, ~5.5km) summed over the event window
# (pre_end → post_end). Context metric for adjusters: distinguishes riverine/
# rainfall events from surge, and corroborates the SAR signal.
RAIN = {
    'dataset': 'UCSB-CHG/CHIRPS/DAILY',
}

# ─── VEGETATION / NON-FLOOD DAMAGE (Round 7) ─────────────────────────────────
# NDVI delta (pre-event median minus post-event median, cloud-masked Sentinel-2)
# catches damage SAR misses: stripped vegetation, debris fields. Positive delta
# = vegetation loss. Advisory only — clouds are the norm post-storm.
VEGETATION = {
    'loss_flag_delta': 0.15,   # ndvi_delta above this flags notable vegetation loss
}

# ─── CLAIM SEVERITY (Round 7) ────────────────────────────────────────────────
# USACE/FEMA-style generic one-story residential depth-damage curve:
# (depth_ft, % of structure value damaged). Piecewise-linear interpolation.
# This produces a defensible reserving RANGE, not an adjuster's estimate —
# the range endpoints come from the depth uncertainty interval.
SEVERITY = {
    'depth_damage_curve': [
        (0.0, 0.0), (0.5, 8.0), (1.0, 14.0), (2.0, 22.0), (3.0, 29.0),
        (4.0, 35.0), (5.0, 40.0), (6.0, 45.0), (8.0, 52.0), (10.0, 58.0),
        (15.0, 70.0), (20.0, 80.0),
    ],
    'min_depth_ft': 0.1,      # below this, no loss estimate is produced
}

# ─── PRE-EVENT FLOOD RISK SCORE (Round 7) ────────────────────────────────────
# Static per-property flood risk (1=minimal … 5=severe) for underwriting/
# renewals — no SAR needed. Combines JRC historical flood occurrence,
# elevation relative to local drainage, and proximity to permanent water.
RISK = {
    'occurrence_weights': [(1.0, 40), (0.25, 30), (0.05, 15)],  # (occ fraction, points)
    'rel_elev_weights':   [(3.0, 30), (8.0, 18), (15.0, 8)],    # (<= ft above drainage, points)
    'near_water_points':  20,
    'score_bins':         [(20, 1), (40, 2), (60, 3), (80, 4)], # <=pts → score, else 5
}

# ─── DEPTH UNCERTAINTY PARAMETERS (Round 3) ──────────────────────────────────
# Estimated depth = water-surface elevation (WSE) minus ground elevation, both
# read from the DEM. Its uncertainty is dominated by the DEM's own vertical
# accuracy, plus the local spread of the water surface. We report a ±1σ (~68%)
# interval per property so a depth is never presented as more precise than the
# elevation data underneath it actually supports.
UNCERTAINTY = {
    # DEM vertical RMSE in metres, keyed by DEM resolution. 3DEP 1m lidar is
    # specified at <=0.1m RMSEz in the open; we use a conservative 0.30m to
    # cover vegetated/built returns. SRTM 30m is ~6m RMSE — large enough that
    # absolute depth from SRTM is barely meaningful, which is exactly why the
    # pipeline prefers 3DEP and why surfacing this interval matters.
    'dem_vertical_rmse_m':  {1: 0.30, 30: 6.0},
    'dem_vertical_rmse_default_m': 6.0,   # conservative fallback for unknown DEM res

    # The water surface is not perfectly flat over a neighborhood. We treat a
    # fraction of the measured flooded-pixel elevation spread (std, in ft) as
    # the WSE sigma. When the pipeline can't supply a measured spread, fall
    # back to a fraction of depth (deeper readings carry more absolute error).
    'wse_spread_to_sigma':  0.5,
    'depth_frac_fallback':  0.15,

    # Interval half-width in sigmas (1.0 ≈ 68%) and a floor so we never claim
    # implausibly tight precision on real residential depths.
    'k_sigma':              1.0,
    'min_ci_ft':            0.3,
}

# ─── ENSEMBLE DISAGREEMENT PARAMETERS (Round 3) ──────────────────────────────
# Three independent members vote on whether a property flooded:
#   - SAR        (Sentinel-1 backscatter / coverage)  — always votes
#   - Optical    (Sentinel-2 MNDWI)                    — votes only when cloud-free
#   - DEM-hydrology (height above local drainage)      — votes on plausibility
# When the members genuinely conflict (one says flooded, another says dry/
# implausible), the safe, defensible action is to send the property to manual
# Review rather than make a confident automated remote decision on a contested
# signal. This is a hard override, complementary to the softer Round-2 optical
# confidence nudge.
ENSEMBLE = {
    'sar_flood_pct':            0.10,  # SAR votes FLOOD when coverage >= this
    'optical_water_pct':        0.10,  # Optical votes FLOOD (water) above this
    'optical_dry_pct':          0.05,  # Optical votes DRY below this (else abstains)
    # DEM-hydrology: relative elevation above the local neighborhood minimum.
    # Near the local low/drainage -> flooding plausible; perched well above it
    # -> flooding implausible (a confident SAR "flood" there is suspect).
    'dem_plausible_rel_ft':     6.0,   # <= this ft above local min: flood plausible
    'dem_implausible_rel_ft':   15.0,  # >= this ft above local min: flood implausible
    'downgrade_to_review':      True,
}