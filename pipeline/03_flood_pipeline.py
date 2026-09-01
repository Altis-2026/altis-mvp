# 03_flood_pipeline.py — SAR flood detection with all production improvements
# Changes from v1:
#   1. 3DEP 1m LiDAR DEM (falls back to SRTM 30m if unavailable)
#   2. Google Open Buildings v3 footprint masking from DEM
#   3. Otsu adaptive threshold (replaces fixed -15dB)
#   4. Slope mask — water cannot pool on slopes > 5 degrees
#   5. Urban density layer output for confidence penalty in Step 4
import sys
import os
import datetime as dt
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ee
import pandas as pd
from config import (GEE_PROJECT, HARVEY, BRAZOS, OUTPUT_DIR, SAR, OPTICAL,
                    UNCERTAINTY, BASELINE, HAND, DUALPOL,
                    DOUBLE_BOUNCE)
from provenance import write_manifest
from uncertainty import depth_interval_ft
import crest_timing

# Detection science lives in a shared, importable module so the live backend
# pipeline runs the identical algorithm. This script just orchestrates it over
# the pre-defined demo events and writes the CSVs.
from flood_detect import sample_properties

# ...and the per-event ASSEMBLY of that science (DEM + orbit choice + baseline +
# VH + cross-orbit stack + HAND + optical) lives in event_image, so validation
# tooling measures the same detector this script ships rather than a copy of it.
from event_image import build_event_image
import structures as struct

# Prefer service-account credentials when available (GEE_SERVICE_ACCOUNT_KEY_JSON
# / GEE_SERVICE_ACCOUNT_KEY, same as backend.live_pipeline.init_ee) — this
# script otherwise assumes an interactive `earthengine authenticate` session,
# which a headless/CI environment never has. Falls back to the interactive
# default so local dev workflows that already ran `earthengine authenticate`
# are unaffected.
try:
    from backend.live_pipeline import init_ee
    init_ee()
except Exception:
    ee.Initialize(project=GEE_PROJECT)


def run_flood_pipeline(event_config):
    event_id, event_name = event_config['event_id'], event_config['event_name']

    print(f"\n{'=' * 60}")
    print(f"  {event_name} — {event_config['study_name']}")
    print(f"{'=' * 60}")

    properties_df = pd.read_csv(os.path.join(OUTPUT_DIR, f"{event_id}_properties.csv"))
    print(f"Loaded {len(properties_df)} properties")

    combined, meta = build_event_image(event_config)

    dem_res     = meta['dem_resolution_m']
    orbit       = meta['sar_orbit_pass']
    pre_count   = meta['pre_event_scene_count']
    post_count  = meta['post_event_scene_count']
    base_start, base_end = meta['baseline_window']
    baseline_n  = meta['baseline_scene_count']
    hand_source = meta['hand_source']
    vh_post_n   = meta['vh_post_scene_count']
    vh_base_n   = meta['vh_baseline_scene_count']

    # ── Did the satellite actually see the crest? A "no flood detected" from a
    #    pass that flew days off the peak is not evidence of a dry property,
    #    and shipping it as though it were is what produces a wrongly denied
    #    claim. Network failure here degrades to 'unknown', which is treated as
    #    "not safe to deny" — never as "fine".
    print("\nChecking whether the acquisitions caught the flood crest "
          "(USGS stream gauges)...")
    try:
        gauge_peaks = crest_timing.fetch_gauge_peaks(
            event_config['bbox'], event_config['post_start'],
            event_config['post_end'])
        acq = [dt.datetime.fromisoformat(t)
               for t in meta.get('acquisition_times_utc', [])]
        crest = crest_timing.assess(acq, gauge_peaks)
    except Exception as exc:
        crest = {'crest_observed': 'unknown',
                 'reason': f'crest check failed: {exc}', 'gauges': []}
    print(f"  crest_observed: {crest['crest_observed'].upper()} — "
          f"{crest.get('reason', '')}")
    if not crest_timing.safe_to_deny(crest):
        print("  → a 'no flood detected' here is NOT safe to turn into a "
              "Remote-Deny; the absence of signal is uninformative.")

    # ── Phase 2: structure attributes. Snapping the sample to the structure's
    #    own footprint replaces a 50m circle that averaged ~33x the building's
    #    area of street, yard and neighbouring parcels.
    print("\nFetching National Structure Inventory attributes...")
    nsi_df = struct.fetch_nsi_structures(event_config['bbox'])
    nsi_match = struct.match_properties_to_structures(properties_df, nsi_df)
    n_matched = int(nsi_match['nsi_matched'].sum())
    print(f"  Matched {n_matched:,}/{len(properties_df):,} properties to a "
          f"structure within {struct.DEFAULT_MAX_MATCH_M:.0f}m")

    sample_df = properties_df.copy()
    sample_df['property_id'] = sample_df['property_id'].astype(str)

    # Read from config BEFORE the match check, not inside it. This is an event
    # setting, not a property of whether NSI happened to return anything — and
    # when it read from inside the block it was simply unbound on the no-match
    # path, crashing the manifest write after the sampling had already been
    # paid for. That path is not an edge case: NSI is CONUS-only, so every
    # analysis outside the US takes it.
    exposure_radius = event_config.get('exposure_radius_m')

    if n_matched:
        sample_df = sample_df.merge(
            nsi_match[['property_id', 'nsi_lat', 'nsi_lon', 'ftprntsqft',
                       'nsi_matched']].assign(
                property_id=lambda d: d['property_id'].astype(str)),
            on='property_id', how='left')
        matched_mask = sample_df['nsi_matched'].fillna(False).astype(bool)
        sample_df['sample_lat'] = sample_df['nsi_lat'].where(matched_mask)
        sample_df['sample_lon'] = sample_df['nsi_lon'].where(matched_mask)

        # exposure_radius_m (optional, per-event): use a fixed buffer instead
        # of the structure's own footprint circle, while still centering on
        # the real structure point rather than the geocoded address.
        #
        # WHY THIS EXISTS: footprint-tight sampling correctly avoids
        # contaminating a property's reading with unrelated nearby water, but
        # it also means a property registers "flooded" only when water sits
        # literally under the ~5-30m structure footprint. Measured directly on
        # the Addicks/Barker area: of 32,607 residential structures, exactly 1
        # has a detected flood pixel AT its own footprint point; widening to a
        # 50m buffer around the same points finds real signal at 15. Flood
        # water pools in yards, driveways, and streets before it reaches a
        # doorway — a claims-relevant exposure standard NFIP and adjusters
        # both use — and a residential building footprint is deliberately
        # sited on a lot's highest ground, so "water under the roof" is a
        # stricter bar than "water reached the property." A 50m buffer is the
        # pre-Phase-2 default this codebase used throughout; this parameter
        # makes choosing it an explicit, documented, per-event decision
        # instead of reverting the Phase 2 default silently.
        if exposure_radius:
            sample_df['sample_radius_m'] = [
                exposure_radius if m else None for m in matched_mask]
        else:
            sample_df['sample_radius_m'] = [
                struct.footprint_radius_m(a) if m else None
                for a, m in zip(sample_df['ftprntsqft'], matched_mask)]

    # Footprint-constrained sampling ideally reduces at Sentinel-1's native
    # 10m pixel spacing, so a ~9m structure isn't averaged over a 30m pixel.
    # In practice, reduceRegions over the full ~15-band combined image (dual-
    # pol, rainfall, NDVI, duration slices, cross-orbit union) at 10m for a
    # large batch of small nearby footprint circles has measured, repeatable
    # server-side cost blowup: single small batches (<=150 properties, one
    # call) finish in a couple of minutes, but 400 properties across 2 batches
    # ran past 30 minutes without returning on identical hardware, twice. The
    # geometry snap to the actual structure — not the resolution — is what
    # fixes the systematic sampling bias, so 30m keeps that fix at a cost that
    # actually completes for a full 1000-property portfolio. Revisit if GEE's
    # server-side behavior on this changes.
    sample_scale = 30
    batch_size = 50
    if not n_matched:
        geometry_label = 'fixed 50m buffer (no NSI match)'
    elif exposure_radius:
        geometry_label = f'fixed {exposure_radius}m buffer on real structure point'
    else:
        geometry_label = 'footprint-constrained'
    print(f"\nSampling properties (scale {sample_scale}m, batch {batch_size}, "
          f"{geometry_label})...")
    flood_df = sample_properties(combined, sample_df, batch_size=batch_size,
                                 scale=sample_scale)

    result_df = properties_df.copy()
    result_df['property_id'] = result_df['property_id'].astype(str)
    # Columns carried from the sampler into the output CSV. This list is
    # deliberately a subset — the batch pipeline does not compute the rain,
    # NDVI or duration bands, and merging those would ship a column of zeros
    # that reads as a measurement.
    #
    # It is also the single easiest place to lose a whole phase of work: the
    # sampler can compute a band perfectly, and omitting one string here drops
    # it before it reaches disk, with no error and no missing-data warning.
    # Phase 4b was measured, sampled, and then silently discarded exactly this
    # way, costing a full 4,000-property run. The guard below turns that into
    # an immediate failure instead of a plausible-looking CSV.
    carry_cols = ['property_id', 'pct_flooded', 'max_depth_ft', 'urban_flag',
                  'optical_available', 'optical_water_pct', 'wse_spread_ft',
                  'rel_elev_ft', 'hand_ft', 'water_fraction',
                  'dpol_water', 'dpol_available',
                  'double_bounce', 'db_available']
    missing = [c for c in carry_cols if c not in flood_df.columns]
    if missing:
        raise RuntimeError(
            f"sample_properties() did not return {missing}. The detector and "
            f"the output schema have diverged — fix rather than dropping the "
            f"column, or the CSV will look complete while missing a signal.")
    result_df = result_df.merge(
        flood_df[carry_cols].assign(
            property_id=lambda d: d['property_id'].astype(str)),
        on='property_id', how='left'
    )
    result_df['dpol_water']      = result_df['dpol_water'].fillna(0.0)
    result_df['dpol_available']  = result_df['dpol_available'].fillna(0).astype(int)
    result_df['double_bounce']   = result_df['double_bounce'].fillna(0.0)
    result_df['db_available']    = result_df['db_available'].fillna(0).astype(int)
    result_df['pct_flooded']         = result_df['pct_flooded'].fillna(0.0)
    result_df['max_depth_ft']        = result_df['max_depth_ft'].fillna(0.0)
    result_df['urban_flag']          = result_df['urban_flag'].fillna(0).astype(int)
    result_df['optical_available']   = result_df['optical_available'].fillna(0).astype(int)
    result_df['optical_water_pct']   = result_df['optical_water_pct'].fillna(0.0)
    result_df['wse_spread_ft']       = result_df['wse_spread_ft'].fillna(0.0)
    result_df['rel_elev_ft']         = result_df['rel_elev_ft'].fillna(0.0)
    # Phase 4a: graded sub-pixel exposure. 0.0 is a real answer here (no water),
    # unlike hand_ft where 0 would mean "at the drainage line".
    result_df['water_fraction']      = result_df['water_fraction'].fillna(0.0)
    # hand_ft is deliberately NOT filled: None means "MERIT Hydro has no value
    # here", and the ensemble must abstain rather than read a filled 0 as
    # "at the drainage line", which is the most flood-prone value there is.
    result_df['dem_resolution_m']    = dem_res

    # ── Phase 2 columns: first-floor height and depth above it.
    nsi_cols = nsi_match.assign(
        property_id=lambda d: d['property_id'].astype(str))
    result_df = result_df.merge(
        nsi_cols[['property_id', 'found_ht', 'found_type', 'num_story',
                  'occtype', 'val_struct', 'val_cont', 'ftprntsqft',
                  'med_yr_blt', 'nsi_match_m', 'nsi_matched']],
        on='property_id', how='left')
    result_df['first_floor_height_ft'] = [
        struct.first_floor_height_ft(v) for v in result_df['found_ht']]
    result_df['foundation_type'] = [
        struct.foundation_label(v) for v in result_df['found_type']]
    result_df['depth_above_ffe_ft'] = [
        struct.depth_above_first_floor(d, h)
        for d, h in zip(result_df['max_depth_ft'], result_df['found_ht'])]
    # Provenance an adjuster can read: which number drove the call, and where
    # the first-floor height came from.
    result_df['first_floor_source'] = [
        'USACE NSI (modeled)' if m else 'unavailable'
        for m in result_df['nsi_matched'].fillna(False)]

    # Round 3: per-depth ±1σ uncertainty interval, from DEM vertical accuracy
    # combined with the measured local water-surface spread.
    intervals = result_df.apply(
        lambda r: depth_interval_ft(r['max_depth_ft'], dem_res, r['wse_spread_ft']),
        axis=1)
    result_df['depth_lower_ft'] = [iv[0] for iv in intervals]
    result_df['depth_upper_ft'] = [iv[1] for iv in intervals]
    result_df['depth_ci_ft']    = [iv[2] for iv in intervals]

    flooded = (result_df['max_depth_ft'] > 0.1).sum()
    urban   = (result_df['urban_flag'] == 1).sum()
    optical_avail = (result_df['optical_available'] == 1).sum()
    contradicted = ((result_df['optical_available'] == 1) &
                     (result_df['pct_flooded'] >= OPTICAL['sar_flood_pct']) &
                     (result_df['optical_water_pct'] < OPTICAL['water_contradict_pct'])).sum()
    print(f"\nSummary:")
    print(f"  Flooded:      {flooded:,} ({flooded/len(result_df)*100:.1f}%)")
    print(f"  Urban zones:  {urban:,} (confidence penalty applied in Step 4)")
    print(f"  Optical cross-check available: {optical_avail:,} properties "
          f"({optical_avail/len(result_df)*100:.1f}%)")
    if optical_avail > 0:
        print(f"  SAR/optical contradictions:    {contradicted:,} "
              f"(likely SAR false positives — penalized in Step 4)")
    if flooded > 0:
        avg = result_df[result_df['max_depth_ft'] > 0.1]['max_depth_ft'].mean()
        print(f"  Avg depth:    {avg:.2f} ft (building-masked {dem_res}m DEM)")

    out = os.path.join(OUTPUT_DIR, f"{event_id}_raw.csv")
    result_df.to_csv(out, index=False)
    print(f"\n✓ Saved → {out}")

    ffe = result_df['depth_above_ffe_ft'].dropna()
    if len(ffe):
        above_ground = result_df.loc[ffe.index, 'max_depth_ft']
        print(f"  First-floor height known: {len(ffe):,} properties "
              f"(mean {result_df['first_floor_height_ft'].dropna().mean():.2f} ft)")
        print(f"  Mean depth above ground {above_ground.mean():.2f} ft vs "
              f"above first floor {ffe.mean():.2f} ft — the difference is the "
              f"bias Phase 2 removes")

    write_manifest(event_id, 'flood_detection', {
        'dem_resolution_m':      dem_res,
        'sar_orbit_pass':        orbit,
        # ── Phase 1 provenance
        'baseline_window':       [base_start, base_end],
        'baseline_scene_count':  baseline_n,
        'baseline_method': (
            f"per-pixel mean/stdDev z-score, threshold {BASELINE['z_threshold']}σ"
            if meta['baseline_active'] else 'unavailable — single pre-event composite'),
        'cross_orbit_passes':    meta['cross_orbit_passes'],
        'cross_orbit_combine':   meta['cross_orbit_combine'],
        'hand_source':           hand_source,
        'hand_thresholds_ft':    [HAND['plausible_ft'], HAND['implausible_ft']],
        # ── Phase 4b provenance. Recorded even when it abstains, so a run with
        #    no dual-pol score can be told apart from a run where it found
        #    nothing — the distinction the dpol_available column carries per
        #    property, kept at the run level too.
        'dualpol_enabled':       bool(DUALPOL.get('enabled', True)),
        'dualpol_active':        bool(meta['dualpol_active']),
        'vh_post_scene_count':   vh_post_n,
        'vh_baseline_scene_count': vh_base_n,
        'double_bounce_enabled': bool(DOUBLE_BOUNCE.get('enabled', True)),
        'double_bounce_active':  bool(meta['baseline_active']),
        'double_bounce_method': (
            f"urban brightening, z >= {DOUBLE_BOUNCE['z_threshold']}σ, "
            f"HAND <= {DOUBLE_BOUNCE.get('max_hand_ft') or HAND['plausible_ft']} ft"
            if meta['baseline_active'] else 'inactive — no multi-temporal baseline'),
        'dualpol_method': (
            f"min(VV,VH) z-evidence over [{DUALPOL['z_min']}, {DUALPOL['z_full']}]σ"
            + (f", co/cross ratio rise >= {DUALPOL['ratio_rise_db']} dB"
               if DUALPOL.get('ratio_gate') else "")
            if meta['dualpol_active'] else 'abstained — no usable VH baseline'),
        # ── Phase 2 provenance
        'structure_source':      'USACE National Structure Inventory',
        'structures_matched':    int(n_matched),
        'structure_match_max_m': struct.DEFAULT_MAX_MATCH_M,
        'sample_geometry': geometry_label,
        'exposure_radius_m_override': exposure_radius,
        'sample_scale_m':        sample_scale,
        'pre_event_scene_count': pre_count,
        'post_event_scene_count': post_count,
        'pre_event_window':      [event_config['pre_start'], event_config['pre_end']],
        'post_event_window':     [event_config['post_start'], event_config['post_end']],
        'acquisition_times_utc': meta.get('acquisition_times_utc', []),
        # Crest-timing disclosure. 'unknown' means we could not check, which is
        # NOT the same as 'observed' and must not be read as one.
        'crest_observed':        crest.get('crest_observed'),
        'crest_reason':          crest.get('reason'),
        'crest_worst_gap_hours': crest.get('worst_gap_hours'),
        'crest_gauges_observed': crest.get('gauges_observed'),
        'crest_gauges_total':    crest.get('gauges_total'),
        'safe_to_remote_deny':   crest_timing.safe_to_deny(crest),
        'wse_radius_m':          event_config['wse_radius_m'],
        'threshold_method':      'Otsu adaptive + range guard',
        'otsu_water_db_range':   [SAR['water_db_min'], SAR['water_db_max']],
        'otsu_fallback_db':      SAR['otsu_fallback_db'],
        'speckle_filter_radius_m': SAR['speckle_radius_m'],
        'wse_estimator':         f"p{SAR['wse_percentile']} neighborhood percentile",
        'max_plausible_depth_ft': SAR['max_plausible_depth_ft'],
        'slope_mask_max_degrees': 5,
        'permanent_water_dataset': 'JRC/GSW1_4/GlobalSurfaceWater (seasonality >= 8mo)',
        'building_mask_dataset': 'GOOGLE/Research/open-buildings/v3',
        'urban_density_dataset': 'JRC/GHSL/P2023A/GHS_BUILT_S/2020',
        'flooded_property_count': int(flooded),
        'urban_property_count':  int(urban),
        'optical_dataset':       'COPERNICUS/S2_SR_HARMONIZED (MNDWI, SCL cloud mask)',
        'optical_max_cloud_pct': OPTICAL['max_cloud_pct'],
        'optical_scene_count':   meta['optical_scene_count'],
        'optical_available_property_count': int(optical_avail),
        'sar_optical_contradiction_count':  int(contradicted),
        'depth_uncertainty':     {
            'dem_vertical_rmse_m': UNCERTAINTY['dem_vertical_rmse_m'].get(
                dem_res, UNCERTAINTY['dem_vertical_rmse_default_m']),
            'interval': '±1σ (~68%): quadrature of DEM vertical accuracy + measured WSE spread',
        },
    })

    return result_df


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', action='append', choices=['harvey', 'brazos'],
                        help='Run only this event (repeatable). Default: both.')
    args = parser.parse_args()
    events = args.event or ['harvey', 'brazos']
    if 'harvey' in events:
        run_flood_pipeline(HARVEY)
    if 'brazos' in events:
        run_flood_pipeline(BRAZOS)
