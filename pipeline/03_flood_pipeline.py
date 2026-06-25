# 03_flood_pipeline.py — SAR flood detection with all production improvements
# Changes from v1:
#   1. 3DEP 1m LiDAR DEM (falls back to SRTM 30m if unavailable)
#   2. Google Open Buildings v3 footprint masking from DEM
#   3. Otsu adaptive threshold (replaces fixed -15dB)
#   4. Slope mask — water cannot pool on slopes > 5 degrees
#   5. Urban density layer output for confidence penalty in Step 4
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ee
import pandas as pd
from config import GEE_PROJECT, HARVEY, IAN, OUTPUT_DIR, SAR, OPTICAL, UNCERTAINTY
from provenance import write_manifest
from uncertainty import depth_interval_ft

# Detection science lives in a shared, importable module so the live backend
# pipeline runs the identical algorithm. This script just orchestrates it over
# the pre-defined demo events and writes the CSVs.
from flood_detect import (
    load_dem, otsu_threshold_gee, load_sar_composite,
    load_optical_water_mask, build_flood_depth_image, sample_properties,
)

ee.Initialize(project=GEE_PROJECT)


def run_flood_pipeline(event_config):
    event_id, event_name = event_config['event_id'], event_config['event_name']

    print(f"\n{'=' * 60}")
    print(f"  {event_name} — {event_config['study_name']}")
    print(f"{'=' * 60}")

    properties_df = pd.read_csv(os.path.join(OUTPUT_DIR, f"{event_id}_properties.csv"))
    print(f"Loaded {len(properties_df)} properties")

    print("\nLoading DEM (3DEP with building mask)...")
    dem, dem_res = load_dem(event_config['bbox'])

    print("Loading pre-event Sentinel-1...")
    pre_image, pre_count, orbit = load_sar_composite(
        event_config['bbox'], event_config['pre_start'], event_config['pre_end'])
    print(f"  {pre_count} scenes, orbit: {orbit}")

    print("Loading post-event Sentinel-1...")
    post_image, post_count, _ = load_sar_composite(
        event_config['bbox'], event_config['post_start'], event_config['post_end'],
        orbit_pass=orbit)
    print(f"  {post_count} scenes")

    print("Loading Sentinel-2 optical cross-check (post-event window)...")
    optical_water, optical_valid, optical_count = load_optical_water_mask(
        event_config['bbox'], event_config['post_start'], event_config['post_end'])
    if optical_count > 0:
        print(f"  {optical_count} cloud-filtered S2 scenes available for cross-check")
    else:
        print("  No cloud-free S2 scenes in window — optical cross-check unavailable "
              "(expected immediately after a storm; SAR-only result is unaffected)")

    print("\nBuilding flood map (Otsu threshold + slope mask)...")
    combined = build_flood_depth_image(
        event_config['bbox'], pre_image, post_image, dem, event_config['wse_radius_m'],
        optical_water=optical_water, optical_valid=optical_valid)

    print("\nSampling properties...")
    flood_df = sample_properties(combined, properties_df, batch_size=100)

    result_df = properties_df.merge(
        flood_df[['property_id', 'pct_flooded', 'max_depth_ft', 'urban_flag',
                  'optical_available', 'optical_water_pct', 'wse_spread_ft',
                  'rel_elev_ft']],
        on='property_id', how='left'
    )
    result_df['pct_flooded']         = result_df['pct_flooded'].fillna(0.0)
    result_df['max_depth_ft']        = result_df['max_depth_ft'].fillna(0.0)
    result_df['urban_flag']          = result_df['urban_flag'].fillna(0).astype(int)
    result_df['optical_available']   = result_df['optical_available'].fillna(0).astype(int)
    result_df['optical_water_pct']   = result_df['optical_water_pct'].fillna(0.0)
    result_df['wse_spread_ft']       = result_df['wse_spread_ft'].fillna(0.0)
    result_df['rel_elev_ft']         = result_df['rel_elev_ft'].fillna(0.0)
    result_df['dem_resolution_m']    = dem_res

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

    write_manifest(event_id, 'flood_detection', {
        'dem_resolution_m':      dem_res,
        'sar_orbit_pass':        orbit,
        'pre_event_scene_count': pre_count,
        'post_event_scene_count': post_count,
        'pre_event_window':      [event_config['pre_start'], event_config['pre_end']],
        'post_event_window':     [event_config['post_start'], event_config['post_end']],
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
        'optical_scene_count':   optical_count,
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
    run_flood_pipeline(HARVEY)
    run_flood_pipeline(IAN)
