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

# ─── SUB-PIXEL WATER FRACTION (Phase 4a) ─────────────────────────────────────
# The single change with the most leverage on RECALL, which measurement showed
# is the binding constraint — not correctness.
#
# THE PROBLEM: the flood mask is binary per pixel. A property's exposure is the
# mean of that mask over its sampling buffer, so at 30m a ~50m buffer covers
# roughly nine pixels, and if none of them individually clears the open-water
# threshold the property scores EXACTLY zero. Measured on the validation runs:
# 3,978 of 4,000 Brazos properties and 3,948 of 4,000 Harvey properties scored
# exactly 0.0. A calibrator handed a column that is 99.4% one identical value
# has nothing to discriminate on, which is precisely why the Brier skill score
# came out negative in both study areas despite honest zip-level agreement.
#
# THE PHYSICS: SAR backscatter in LINEAR POWER (not dB) mixes linearly by area
# fraction within a resolution cell. For a pixel that is fraction f water and
# (1-f) its normal dry self:
#
#     sigma_obs = f * sigma_water + (1 - f) * sigma_dry
#     =>      f = (sigma_dry - sigma_obs) / (sigma_dry - sigma_water)
#
# We already have a per-pixel sigma_dry for free: the multi-temporal baseline
# mean built in Phase 1. So this is a genuine physical unmixing against that
# pixel's own measured normal state, not a tuned heuristic — a half-flooded
# suburban lot that never reads as "open water" still returns a real, graded
# ~0.5 instead of a zero.
#
# WHY THIS DOES NOT JUST INVENT SIGNAL: the fraction is only computed where the
# darkening is statistically significant against that pixel's own baseline
# variability. The gate is looser than the binary detector's (that is the
# point — it is what recovers the partial cases) but it is still a gate, so
# ordinary speckle does not produce spurious fractions everywhere.
#
# ─────────────────────────────────────────────────────────────────────────────
# MEASURED RESULT: THIS DOES NOT WORK, AND IS DISABLED BY DEFAULT.
#
# It was implemented, run end to end on Brazos (4,000 properties, 15 zips,
# 3,135 real NFIP claims), and measured against the same ground truth as
# everything else. It did exactly what it was designed to do mechanically, and
# that turned out to be worthless:
#
#   signal density   22 -> 1,441 properties nonzero (0.55% -> 36%)
#   distinct scores  23 -> 602
#   depth correlation      +0.366 -> +0.366   (unchanged)
#   Brier                  0.1714 -> 0.1712   (unchanged)
#   Brier skill score     -0.0366 -> -0.0354  (still negative)
#
# The direct test is damning: as a standalone predictor of whether a property's
# zip actually flooded, the water fraction scores **AUC 0.4862** (0.5 is no
# information at all) with Mann-Whitney p=0.92. It is fractionally MORE common
# on dry-truth properties (36.9% nonzero) than on flooded-truth ones (33.5%).
#
# WHY, which is the part worth keeping: after a rain event of Harvey's scale
# the whole region has saturated soil, and wet ground darkens C-band SAR in
# the same direction and a similar magnitude as shallow standing water. The
# loose z gate that is required to recover partial inundation is also loose
# enough to admit soil moisture everywhere. Tightening it back to the binary
# detector's threshold just reproduces the binary detector. Single-polarisation
# amplitude at 30m simply does not separate "wet ground" from "standing water",
# and no amount of tuning inside this method will change that — the information
# is not in the measurement.
#
# The code and its tests are retained deliberately, not deleted: the physics is
# correct (the unmixing recovers known fractions exactly), and the method is
# sound where the confound is absent — dual-pol or polarimetric data, finer
# resolution, or events without basin-wide antecedent rainfall. Turning it on
# is a one-line change plus a re-validation. Shipping it enabled would inflate
# apparent sensitivity 65x while adding zero accuracy, which is precisely the
# kind of plausible-looking number this codebase refuses everywhere else.
#
# See docs/DETECTION_LIMITS.md section 9.
# ─────────────────────────────────────────────────────────────────────────────
SUBPIXEL = {
    'enabled': False,
    # Open-water VV endmember. Physically motivated rather than tuned: calm
    # open water at Sentinel-1's incidence angles sits near -20 dB, well below
    # the -12 dB upper bound the Otsu range guard uses for "could be water".
    'water_endmember_db': -20.0,
    # Significance gate, in standard deviations below the pixel's own baseline
    # mean. Deliberately looser than BASELINE['z_threshold'] (2.0) used by the
    # binary mask — partial inundation produces a partial darkening, which is
    # exactly the signal the strict gate was discarding.
    'z_min': 1.0,
    # Fractions below this are treated as zero. Guards against a long tail of
    # physically meaningless 1-2% values driven by residual speckle.
    'min_fraction': 0.05,
    # A pixel darker than the water endmember is fully water, not >100% water.
    'clamp_to_one': True,
}

# ─── PHASE 4b: DUAL-POLARISATION WATER DISCRIMINATION ────────────────────────
# The direct answer to what killed Phase 4a.
#
# Phase 4a failed for one specific, diagnosable reason: VV amplitude alone
# cannot separate saturated soil from shallow standing water. Both darken
# C-band in the same direction. That is a limit of the MEASUREMENT, not of the
# unmixing maths — so the fix has to add a measurement, not tune a threshold.
#
# Sentinel-1 IW already transmits the second measurement and we were throwing
# it away: every scene carries VH as well as VV, at no extra acquisition cost
# and no extra download (the existing VH cross-check reads it, but only as a
# second independent binary detector, which inherits the same confound).
#
# THE PHYSICS THAT SEPARATES THEM. Radar backscatter has three regimes here:
#   - Open/standing water: specular. The surface reflects energy away from the
#     sensor. VV drops hard AND VH collapses toward the noise floor, because a
#     smooth surface also destroys the depolarisation that produces cross-pol
#     return. Both channels fall; VH falls proportionally further.
#   - Saturated bare soil: the surface stays rough, only its dielectric
#     constant changes. This does NOT suppress depolarisation — VH holds
#     roughly steady, or moves far less than VV does.
#   - Flooded vegetation: double-bounce off trunks/walls. VV RISES sharply.
#     Not what we are gating on here, but it is why VV alone is unreliable in
#     both directions.
#
# So the discriminating question is not "did this pixel darken?" (Phase 4a's
# question, which soil moisture answers yes to) but "did BOTH channels darken
# together, cross-pol at least as hard as co-pol?" — which is true for water
# and false for wet ground.
#
# THE RULE. Per pixel, against each channel's OWN multi-temporal baseline:
#     z_vv = (post_vv - base_vv_mean) / base_vv_std      (negative = darker)
#     z_vh = (post_vh - base_vh_mean) / base_vh_std
# Evidence is the WEAKER of the two normalised drops, not their average or
# their sum. Taking the minimum is the whole design: a channel that fails to
# corroborate CAPS the score rather than being outvoted. Wet soil produces a
# large VV drop and a small VH drop, so min() returns the small number and the
# pixel scores near zero. Standing water drops both, so min() stays large.
# Averaging would let the VV drop carry the pixel and reproduce Phase 4a.
#
# `ratio_gate` adds the second-order check. The co/cross ratio (VV-VH in dB)
# RISES over water — VH collapses further than VV — and stays flat or falls
# over wet soil. Requiring a rise is nearly free and rejects the case where
# both channels drop simply because the whole scene got darker.
#
# HOW THE TWO GATES INTERACT, since it is not obvious and was only noticed by
# writing the test: requiring (VV-VH) to rise is algebraically the same as
# requiring VH to fall by MORE dB than VV. When the two channels happen to
# share a baseline sigma, that already forces evidence_vh >= evidence_vv, so
# min() would always return the VV term and the ratio gate alone would be
# doing all the work. They separate as soon as the sigmas differ — which is
# the normal case, because VH sits closer to the noise floor and is the
# noisier channel. A 5 dB VH drop at sigma=3 is LESS significant than a 4 dB
# VV drop at sigma=1, and min() catches that while the dB-domain ratio gate
# cannot see it at all. Both are kept because each covers what the other
# misses; neither is redundant on real data. See tests/test_dualpol.py.
#
# HONEST STATUS: enabled here so it can be MEASURED, not because it is known
# to work. Phase 4a was equally well motivated and failed. The acceptance test
# is the same one it failed: standalone AUC against NFIP claim truth at Brazos,
# plus Brier and Brier skill score. If it does not beat AUC 0.5 by a margin
# that survives its p-value, it gets set to False with the numbers recorded
# right here, exactly as SUBPIXEL was.
DUALPOL = {
    'enabled': True,
    # Standard deviations below each channel's own baseline mean required
    # before that channel contributes any evidence. Deliberately the same
    # loose gate Phase 4a used (1.0) — the point of this phase is that a
    # second channel, not a stricter threshold, is what rejects soil moisture.
    'z_min': 1.0,
    # Drop, in standard deviations, that counts as full confidence for a
    # channel. Evidence per channel ramps linearly from z_min to z_full.
    'z_full': 4.0,
    # Require the co/cross ratio (VV-VH, dB) to rise by at least this much
    # versus baseline. Water raises it; uniform scene darkening does not.
    # Set to None to disable the second-order check.
    'ratio_gate': True,
    'ratio_rise_db': 0.5,
    # Minimum VH baseline scenes before the VH channel is trusted at all.
    # Below this the score abstains (returns 0) rather than guessing from a
    # noisy std estimate — a bad std makes z_vh arbitrarily large.
    'min_vh_baseline_scenes': 8,
    # Scores below this are treated as zero (residual speckle tail).
    'min_score': 0.05,
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

# ─── CLAIM SEVERITY (Round 7, extended by Phase 3) ───────────────────────────
# USACE/FEMA-style generic one-story residential depth-damage curve:
# (depth_ft, % of structure value damaged). Piecewise-linear interpolation.
# This produces a defensible reserving RANGE, not an adjuster's estimate —
# the range endpoints come from the depth uncertainty interval.
#
# `depth_damage_curve` remains the GENERIC fallback, used whenever the
# structure attributes needed to pick a specific curve are unknown (outside
# CONUS, or no NSI match). SEVERITY_CURVES below supersedes it when they are.
SEVERITY = {
    'depth_damage_curve': [
        (0.0, 0.0), (0.5, 8.0), (1.0, 14.0), (2.0, 22.0), (3.0, 29.0),
        (4.0, 35.0), (5.0, 40.0), (6.0, 45.0), (8.0, 52.0), (10.0, 58.0),
        (15.0, 70.0), (20.0, 80.0),
    ],
    'min_depth_ft': 0.1,      # below this, no loss estimate is produced
}

# ─── PHASE 3: MULTI-CURVE DEPTH-DAMAGE LIBRARY ───────────────────────────────
# One generic curve was the weakest scientific claim in the product. A
# two-storey home loses a far smaller FRACTION of its value to two feet of
# water than a one-storey home does — the water reaches the same rooms, but
# those rooms are a smaller share of the structure. A home with a basement
# starts taking damage BELOW grade, i.e. at negative depth relative to the
# first floor. A generic curve gets all of that wrong in a signed, predictable
# direction.
#
# DEPTH CONVENTION: these curves are indexed on depth above the FIRST FLOOR,
# which is what published depth-damage functions actually take, and what
# Phase 2 (NSI foundation height) lets us compute. Feeding them depth above
# ground — the detector's raw output — overstates damage by roughly the
# foundation height, worst on pier and crawlspace construction.
#
# STRUCTURE curves: % of structure replacement value.
# Shape and anchor points follow the FEMA/USACE residential functions used in
# HAZUS (FEMA Flood Model Technical Manual) — one-storey vs two-storey vs
# manufactured housing, with and without basement. They are TYPICAL published
# values, not a licensed copy of the HAZUS tables, and the basement variants
# extend below zero because a basement floods before water reaches grade.
#
# HONEST LIMIT: these remain generic published curves, not curves fitted to
# this book's own claims. Phase 4 (fitting on NFIP claim outcomes) is what
# replaces borrowed shapes with empirical ones. Until then this is a better
# structural prior, not a calibrated loss model.
SEVERITY_CURVES = {
    # occupancy / stories / basement -> [(depth_above_first_floor_ft, pct)]
    'RES1-1S-NB': [   # single family, 1 storey, no basement
        (0.0, 0.0), (0.5, 9.0), (1.0, 16.0), (2.0, 25.0), (3.0, 33.0),
        (4.0, 40.0), (5.0, 45.0), (6.0, 50.0), (8.0, 58.0), (10.0, 64.0),
        (15.0, 76.0), (20.0, 85.0),
    ],
    'RES1-2S-NB': [   # single family, 2+ storeys, no basement — same water,
                      # smaller share of total structure value
        (0.0, 0.0), (0.5, 5.0), (1.0, 9.0), (2.0, 15.0), (3.0, 20.0),
        (4.0, 25.0), (5.0, 29.0), (6.0, 33.0), (8.0, 40.0), (10.0, 46.0),
        (15.0, 58.0), (20.0, 68.0),
    ],
    'RES1-1S-B': [    # single family, 1 storey, WITH basement — damage starts
                      # below grade, before water reaches the first floor
        (-4.0, 3.0), (-2.0, 6.0), (0.0, 11.0), (0.5, 17.0), (1.0, 23.0),
        (2.0, 31.0), (3.0, 38.0), (4.0, 44.0), (5.0, 49.0), (6.0, 54.0),
        (8.0, 61.0), (10.0, 67.0), (15.0, 78.0), (20.0, 87.0),
    ],
    'RES1-2S-B': [    # single family, 2+ storeys, WITH basement
        (-4.0, 2.0), (-2.0, 4.0), (0.0, 7.0), (0.5, 11.0), (1.0, 15.0),
        (2.0, 20.0), (3.0, 25.0), (4.0, 30.0), (5.0, 34.0), (6.0, 38.0),
        (8.0, 45.0), (10.0, 51.0), (15.0, 63.0), (20.0, 72.0),
    ],
    'RES2': [         # manufactured / mobile home — far more vulnerable, and
                      # effectively a total loss at shallow depths
        (0.0, 0.0), (0.5, 15.0), (1.0, 27.0), (2.0, 45.0), (3.0, 62.0),
        (4.0, 75.0), (5.0, 85.0), (6.0, 92.0), (8.0, 100.0), (10.0, 100.0),
        (15.0, 100.0), (20.0, 100.0),
    ],
    'RES3': [         # multi-family residential
        (0.0, 0.0), (0.5, 6.0), (1.0, 11.0), (2.0, 18.0), (3.0, 24.0),
        (4.0, 30.0), (5.0, 35.0), (6.0, 39.0), (8.0, 47.0), (10.0, 53.0),
        (15.0, 65.0), (20.0, 74.0),
    ],
    'COM': [          # commercial
        (0.0, 0.0), (0.5, 6.0), (1.0, 11.0), (2.0, 19.0), (3.0, 26.0),
        (4.0, 32.0), (5.0, 37.0), (6.0, 42.0), (8.0, 50.0), (10.0, 56.0),
        (15.0, 68.0), (20.0, 78.0),
    ],
}

# CONTENTS curves: % of CONTENTS value, reported separately from structure.
# NFIP settles building and contents as separate coverages, and a carrier
# reserves them separately, so blending them into one number — as the previous
# single-curve estimate did — is not the shape of the answer a claims manager
# needs. Contents damage rises FASTER than structure damage at shallow depths
# (a few inches ruins flooring and furniture while the structure is largely
# intact) and saturates earlier.
SEVERITY_CONTENTS_CURVES = {
    'RES1-1S-NB': [
        (0.0, 0.0), (0.5, 12.0), (1.0, 22.0), (2.0, 37.0), (3.0, 50.0),
        (4.0, 60.0), (5.0, 68.0), (6.0, 75.0), (8.0, 85.0), (10.0, 92.0),
        (15.0, 98.0), (20.0, 100.0),
    ],
    'RES1-2S-NB': [
        (0.0, 0.0), (0.5, 7.0), (1.0, 13.0), (2.0, 22.0), (3.0, 30.0),
        (4.0, 37.0), (5.0, 43.0), (6.0, 49.0), (8.0, 59.0), (10.0, 67.0),
        (15.0, 82.0), (20.0, 90.0),
    ],
    'RES1-1S-B': [
        (-4.0, 5.0), (-2.0, 10.0), (0.0, 17.0), (0.5, 25.0), (1.0, 33.0),
        (2.0, 46.0), (3.0, 57.0), (4.0, 66.0), (5.0, 73.0), (6.0, 79.0),
        (8.0, 88.0), (10.0, 94.0), (15.0, 99.0), (20.0, 100.0),
    ],
    'RES1-2S-B': [
        (-4.0, 3.0), (-2.0, 6.0), (0.0, 11.0), (0.5, 16.0), (1.0, 22.0),
        (2.0, 31.0), (3.0, 39.0), (4.0, 46.0), (5.0, 52.0), (6.0, 58.0),
        (8.0, 68.0), (10.0, 76.0), (15.0, 88.0), (20.0, 94.0),
    ],
    'RES2': [
        (0.0, 0.0), (0.5, 20.0), (1.0, 35.0), (2.0, 57.0), (3.0, 74.0),
        (4.0, 86.0), (5.0, 94.0), (6.0, 98.0), (8.0, 100.0), (10.0, 100.0),
        (15.0, 100.0), (20.0, 100.0),
    ],
    'RES3': [
        (0.0, 0.0), (0.5, 8.0), (1.0, 15.0), (2.0, 26.0), (3.0, 35.0),
        (4.0, 43.0), (5.0, 50.0), (6.0, 56.0), (8.0, 67.0), (10.0, 75.0),
        (15.0, 88.0), (20.0, 94.0),
    ],
    'COM': [
        (0.0, 0.0), (0.5, 9.0), (1.0, 17.0), (2.0, 29.0), (3.0, 39.0),
        (4.0, 48.0), (5.0, 55.0), (6.0, 61.0), (8.0, 72.0), (10.0, 80.0),
        (15.0, 91.0), (20.0, 96.0),
    ],
}

# ─── PHASE 3: DURATION-DEPENDENT DAMAGE ADJUSTMENT ───────────────────────────
# Prolonged submersion does more damage than the same depth draining quickly —
# drywall wicks, framing saturates, mould sets in. The pipeline already
# computes inundation duration from the post-window slices, so this is nearly
# free and is a genuinely differentiated signal: most competitors report peak
# extent only.
#
# SOURCING CAVEAT — READ BEFORE QUOTING THIS EXTERNALLY. The roadmap cites a
# ~2.6x multiplier at equal depth from a duration-dependent depth-damage study
# calibrated on NFIP claims from three US hurricanes. That figure came from a
# search-result summary; the paper itself is paywalled and unreachable from
# this environment, so it has NOT been read in full and its exact conditions
# (which depths, which duration cut, structure types) are unverified.
#
# Applying an unverified 2.6x to a customer's reserve number would be exactly
# the kind of plausible-looking fabrication this codebase refuses elsewhere. So
# the default here is DELIBERATELY CONSERVATIVE and explicitly a placeholder:
# a modest ramp, capped well below the cited figure, applied only where
# duration is actually MEASURED (never assumed), and reported as a labelled
# adjustment rather than folded silently into the headline number. Replace
# these values once the source is read, or once Phase 4 fits duration from
# claims directly.
SEVERITY_DURATION = {
    'enabled':          True,
    # Days of measured inundation -> multiplier on the structure damage pct.
    # 1.0 below the first threshold means "no adjustment" is the default.
    'multipliers':      [(0.0, 1.00), (2.0, 1.00), (4.0, 1.10), (7.0, 1.20),
                         (14.0, 1.30)],
    'max_multiplier':   1.30,
    # Never adjust on an assumed duration. The pipeline reports duration as
    # None when fewer than 2 post-window slices had a usable scene.
    'require_measured': True,
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