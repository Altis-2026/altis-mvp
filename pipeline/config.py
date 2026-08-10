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

# Container-friendly alternative: the key's raw JSON *content* (not a path) in
# a single env var. Most hosts (Railway, Render, Fly, Cloud Run, App Runner)
# make it easy to paste a secret value but awkward to mount a secret file, so
# this is the portable path for production — GEE_SERVICE_ACCOUNT_KEY above
# still works unchanged for local dev with a key file on disk. Whichever is
# set wins; the JSON-content form takes priority since it's the deploy path.
GEE_SERVICE_ACCOUNT_KEY_JSON = os.getenv('GEE_SERVICE_ACCOUNT_KEY_JSON')

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
    'sub':                'Addicks/Barker Reservoir area, Harris County, TX  •  August 2017',
    'county':             'Harris County, TX',
    'pre_start':          '2017-08-01',
    'pre_end':            '2017-08-24',
    'post_start':         '2017-08-27',
    'post_end':           '2017-09-10',
    # Bounding box [west, south, east, north] — the Addicks/Barker Reservoir
    # corridor and surrounding west/northwest Houston. Measured SAR flood-mask
    # coverage at the reservoir edge is 0.68-2.65% per scene versus 0.00-0.02%
    # for the original Meyerland/Braeswood box (0.18% over that box's full
    # extent) — open, low-vegetation reservoir-adjacent terrain is SAR's
    # strongest case, dense suburb under tree canopy its worst. See
    # docs/DETECTION_LIMITS.md for the measurements behind this change.
    #
    # Widened from the tight [-95.72, 29.75, -95.60, 29.85] reservoir-edge box.
    # WHY: validation correlates per-ZIP flood rate against per-ZIP NFIP claim
    # rate, so it needs CONTRAST — zips that flooded badly and zips that barely
    # did. The tight box gave 8 zips that ALL cleared the ground-truth
    # threshold, leaving no negative class to correlate against or calibrate
    # on, and 542 of 1000 properties landed in a single zip. This box spans
    # west and northwest Houston either side of the Addicks/Barker pools,
    # covering both the neighborhoods that flooded on reservoir release and
    # surrounding ones that stayed dry.
    'bbox':               [-95.88, 29.66, -95.46, 30.00],
    'study_name':         'West & Northwest Houston (Addicks/Barker corridor), TX',
    'days_since_event':   3,
    'wse_radius_m':       300,    # Water surface elevation focal radius — tighter for rainfall flooding
    # Sample a 50m buffer centered on the real structure point instead of the
    # Phase 2 footprint-tight circle. Measured directly here: of 32,607
    # residential structures in this bbox, only 1 has a detected flood pixel
    # literally under its own footprint; widening to 50m finds real signal at
    # 15. Residential lots are graded so the building sits on the highest
    # ground; flood water reaches the yard, driveway, and street first. A
    # buffer captures that claims-relevant exposure; the tight footprint
    # (the default everywhere else) captures only water under the roof.
    # See docs/DETECTION_LIMITS.md.
    'exposure_radius_m':  50,
    'cost_per_inspection': 750,
    'lat':                29.830,
    'lon':               -95.670,
    'zoom':               10,
}

# ─── HURRICANE IAN — REMOVED ─────────────────────────────────────────────────
# Ian (Port Charlotte / Punta Gorda, Sept 2022) was dropped as a demo and
# benchmark event. It is not a tuning problem and not fixable by relocating the
# study area: Sentinel-1's first usable pass over the area was 2 October, four
# days after the 28 September landfall, by which time the surge had receded.
# Only ASCENDING scenes covered the window. The large dark fractions present in
# those scenes (14-32% below -16 dB) are Port Charlotte's permanent canal
# network and coastline — they show near-zero z-scores against a 12-month
# baseline, i.e. they are dark all the time, not flooded.
#
# The satellite never observed the event, so no amount of detection work
# recovers it. Keeping it as a demo meant repeatedly explaining a number that
# could not improve. Measurements behind this: docs/DETECTION_LIMITS.md.
#
# The revisit-gap limitation Ian illustrates is real and still disclosed in the
# product; it just no longer needs a broken demo event to represent it.

# ─── BRAZOS RIVER FLOODPLAIN (HARVEY) CONFIGURATION ──────────────────────────
# Second US validation area, same storm as HARVEY. The Brazos crested at a
# record ~55 ft at Richmond on 1 September 2017, inundating the floodplain
# through Richmond, Rosenberg, and Simonton.
#
# WHY THIS ONE: it is open riverine floodplain — SAR's strongest detection
# case, the same physical setting as the Lismore event that has always worked
# in this repo — and it is a genuinely independent second sample. Addicks and
# Barker are reservoir-release flooding; the Brazos is river-crest flooding, so
# agreement across both is stronger evidence than either alone. Measured flood
# coverage here is 0.79-2.21% per scene (docs/DETECTION_LIMITS.md section 3).
BRAZOS = {
    'event_name':         'Hurricane Harvey — Brazos River',
    'event_id':           'brazos',
    'label':              'Harvey: Brazos River',
    'sub':                'Fort Bend County, TX  •  August–September 2017',
    'county':             'Fort Bend County, TX',
    'pre_start':          '2017-08-01',
    'pre_end':            '2017-08-24',
    # The Brazos crested days AFTER Harvey's rainfall ended — river flooding
    # lags the storm, unlike the reservoir releases in the HARVEY box. The post
    # window is shifted later to cover the actual crest.
    'post_start':         '2017-08-29',
    'post_end':           '2017-09-12',
    'bbox':               [-95.90, 29.45, -95.60, 29.72],
    'study_name':         'Brazos River floodplain: Richmond, Rosenberg, Simonton TX',
    'days_since_event':   3,
    'wse_radius_m':       300,
    # Same claims-relevant exposure standard as HARVEY — see that entry and
    # docs/DETECTION_LIMITS.md section 6 for the measurements behind it.
    'exposure_radius_m':  50,
    'cost_per_inspection': 750,
    'lat':                29.585,
    'lon':               -95.755,
    'zoom':               11,
}

# ─── NORTHERN RIVERS (LISMORE) FLOODS CONFIGURATION ──────────────────────────
# The Feb-Mar 2022 riverine flooding of the Richmond River floodplain, NSW —
# open-water riverine flooding, SAR's strongest detection case, live-verified
# in this repo's demo portfolios. The dense demo parcels are grid points across
# the floodplain localities (addresses labeled "Parcel N"), scored by the same
# real pipeline as everything else.
LISMORE = {
    'event_name':         'Northern Rivers Floods',
    'event_id':           'lismore',
    'label':              'Northern Rivers Floods',
    'sub':                'Lismore & Richmond River, NSW  •  Feb–Mar 2022',
    'county':             'Northern Rivers, NSW (AU)',
    'pre_start':          '2022-01-28',
    'pre_end':            '2022-02-26',
    'post_start':         '2022-02-28',
    'post_end':           '2022-03-14',
    # Bounding box [west, south, east, north] — Lismore→Coraki→Woodburn→Broadwater
    'bbox':               [153.20, -29.10, 153.42, -28.78],
    'study_name':         'Richmond River floodplain: Lismore, Coraki, Woodburn, Broadwater',
    'days_since_event':   3,
    'wse_radius_m':       300,    # riverine flooding
    'cost_per_inspection': 750,
    'lat':               -28.94,
    'lon':               153.31,
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
        'bbox':  cfg['bbox'],          # zone-summary + globe event-zone box
        'event_date': cfg['post_start'],  # prefill for live analysis settings
    }
    for cfg in (LISMORE, HARVEY, BRAZOS)
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

# ─── MULTI-TEMPORAL SAR BASELINE (Phase 1) ───────────────────────────────────
# Replaces the single pre-event composite with a per-pixel statistical baseline
# built from a long run of pre-event Sentinel-1 scenes.
#
# WHY: a single pre-event median can be skewed by one wet, windy, or
# agriculturally disturbed scene, and there is no way to tell from the output
# that it happened. With a mean and a standard deviation per pixel we can ask a
# strictly better question — "is this pixel's post-event backscatter anomalous
# RELATIVE TO ITS OWN NORMAL VARIABILITY?" — instead of "is it darker than one
# arbitrary earlier picture?".
#
# The variance term also hands us a genuine per-pixel confidence for free:
# permanently noisy pixels (vegetation, agriculture, rough water) need a much
# larger drop before they count as flooded, while consistently stable pixels
# (pavement, rooftops, bare ground) trip on a smaller one.
#
# Baselines are built PER ORBIT. Ascending and descending passes view the same
# ground at different incidence angles and azimuths, so their backscatter
# distributions are genuinely different — pooling them would inflate the
# variance and blunt the whole method.
BASELINE = {
    'enabled':        True,
    # 12 months captures a full seasonal cycle, so a flood isn't confused with
    # normal seasonal wetness. Ends 2 days before the event window opens.
    'months':          12,
    'gap_days':        2,
    # Below this many scenes the per-pixel std is not estimated reliably, and
    # the detector falls back to the single pre-event composite. Sentinel-1's
    # 6-12 day repeat gives ~30-60 scenes/year per orbit, so this is a floor
    # for genuinely data-poor regions, not a normal operating point.
    'min_scenes':      8,
    # How many standard deviations below the baseline mean counts as a real
    # change. 2.0 ≈ the 2.3rd percentile of a normal baseline.
    'z_threshold':     2.0,
    # Floor on the per-pixel std (dB). Without it, a pixel that happens to be
    # near-constant across the baseline divides by ~0 and produces an enormous
    # z-score from a physically trivial change.
    'min_std_db':      0.8,
    # Require BOTH the change test (z-score) and the absolute test (Otsu water
    # threshold). Change alone flags any darkening — harvested fields, dry
    # pavement after rain. Absolute alone is the old single-threshold method.
    # Requiring both is what actually cuts false positives.
    'require_absolute': True,
}

# ─── HAND — HEIGHT ABOVE NEAREST DRAINAGE (Phase 1) ──────────────────────────
# Replaces `rel_elev_ft` (elevation minus the minimum within a 300-600m circle)
# as the DEM-hydrology ensemble vote.
#
# The old heuristic answers "is this pixel low compared to its neighbours?",
# which on flat coastal terrain is nearly meaningless — every parcel is within
# a few feet of its neighbourhood minimum, so the vote abstained constantly.
# HAND answers the hydrologically correct question: "how high is this point
# above the drainage channel that water would actually have to rise from?"
# It is the standard terrain descriptor for flood susceptibility and is used
# operationally by NOAA/OWP and in FEMA-adjacent flood modelling.
#
# Source: MERIT Hydro (`hnd` band), global, ~90m, already in Earth Engine.
# Resolution caveat: 90m is coarser than a parcel, which is exactly why HAND
# votes on PLAUSIBILITY ("could water physically reach here?") and never
# detects flooding on its own.
HAND = {
    'enabled':      True,
    'asset':        'MERIT/Hydro/v1_0_1',
    'band':         'hnd',
    # Thresholds in feet. ~5 m and ~15 m above nearest drainage: below 5m is
    # routinely inundated by extreme events, above 15m essentially is not.
    'plausible_ft':   16.0,
    'implausible_ft': 49.0,
}

# ─── CROSS-ORBIT STACKING (Phase 1) ──────────────────────────────────────────
# The detector previously picked the single dominant orbit and discarded every
# scene from the other pass, throwing away up to half the available
# observations. That directly worsens the revisit-gap problem: a flood peaking
# between two same-orbit passes may well have been seen by the other orbit.
#
# Scenes from different orbits are NOT directly comparable (different incidence
# angle and look direction), so we never merge them into one composite. Each
# orbit gets its own Otsu threshold and its own baseline, produces its own
# independent flood mask, and only the finished BOOLEAN masks are combined.
CROSS_ORBIT = {
    'enabled':    True,
    # 'union'  — flooded if any orbit saw water. Maximises temporal coverage,
    #            which is the point of the exercise; a real flood seen by one
    #            pass is still a real flood.
    # 'agree'   — flooded only where every observing orbit agrees. Higher
    #            precision, but discards the revisit benefit.
    'combine':    'union',
    # Minimum scenes for an orbit to contribute a mask at all.
    'min_scenes_per_orbit': 1,
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
    #
    # SUPERSEDED BY HAND (Phase 1): these thresholds are still used, unchanged,
    # whenever a property has no HAND value — older pre-computed runs, and any
    # location MERIT Hydro doesn't cover. When `hand_ft` is present the
    # HAND thresholds above take precedence, because HAND measures height above
    # the actual drainage network rather than above an arbitrary circle.
    'dem_plausible_rel_ft':     6.0,   # <= this ft above local min: flood plausible
    'dem_implausible_rel_ft':   15.0,  # >= this ft above local min: flood implausible
    'downgrade_to_review':      True,
}