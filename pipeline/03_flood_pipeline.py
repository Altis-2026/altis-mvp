# 03_flood_pipeline.py — SAR flood detection and depth estimation via GEE
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ee
import pandas as pd
import time
from collections import Counter
from config import GEE_PROJECT, HARVEY, IAN, OUTPUT_DIR

ee.Initialize(project=GEE_PROJECT)


# ─────────────────────────────────────────────────────────────────────────────
# SAR IMAGERY LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_sar_composite(bbox_coords, start_date, end_date, orbit_pass=None):
    """
    Load a Sentinel-1 VV median composite for a bounding box and date range.
    If orbit_pass is None, automatically selects the most common orbit direction
    to ensure consistency between pre and post images.

    Returns: (composite_image, scene_count, orbit_direction_used)
    """
    bbox = ee.Geometry.Rectangle(bbox_coords)

    base_filter = (ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(bbox)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.eq('instrumentMode', 'IW'))
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
        .select('VV'))

    if orbit_pass is None:
        # Detect available orbit directions and use the most common
        all_passes = base_filter.aggregate_array('orbitProperties_pass').getInfo()
        if not all_passes:
            raise ValueError(
                f"No Sentinel-1 images found between {start_date} and {end_date}. "
                f"Check the date range and bounding box in config.py."
            )
        orbit_pass = Counter(all_passes).most_common(1)[0][0]

    collection = base_filter.filter(ee.Filter.eq('orbitProperties_pass', orbit_pass))
    count = collection.size().getInfo()

    if count == 0:
        # Fall back to other orbit direction
        other = 'ASCENDING' if orbit_pass == 'DESCENDING' else 'DESCENDING'
        collection = base_filter.filter(ee.Filter.eq('orbitProperties_pass', other))
        count = collection.size().getInfo()
        orbit_pass = other

    if count == 0:
        raise ValueError(
            f"No Sentinel-1 images found between {start_date} and {end_date} "
            f"for either orbit direction."
        )

    composite = collection.median()
    return composite, count, orbit_pass


# ─────────────────────────────────────────────────────────────────────────────
# FLOOD DETECTION AND DEPTH ESTIMATION
# ─────────────────────────────────────────────────────────────────────────────

def build_flood_depth_image(bbox_coords, pre_image, post_image, wse_radius_m):
    """
    Generate a 2-band image: ['flood', 'depth_ft']
    - flood:    binary (1 = newly flooded, 0 = not flooded)
    - depth_ft: estimated flood depth in feet (0 where not flooded)

    Method:
    1. Water detection via SAR backscatter thresholding (water < -15 dB)
    2. Change detection: post-event water minus pre-event water
    3. Remove permanent water bodies using JRC Global Surface Water
    4. Depth via Water Surface Elevation (WSE) minus DEM elevation
       WSE estimated using focal_max of flooded pixel elevations
    """
    WATER_THRESHOLD_DB = -15.0

    # Step 1: Detect water pixels in pre and post imagery
    pre_water  = pre_image.lt(WATER_THRESHOLD_DB)
    post_water = post_image.lt(WATER_THRESHOLD_DB)

    # Step 2: Load permanent water mask and exclude it from flood detection
    permanent_water = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
        .select('seasonality')
        .gte(8)          # Present 8+ months per year = permanent
        .unmask(0))      # Areas with no JRC data are treated as non-permanent

    # Step 3: New flooding = water in post AND not water in pre AND not permanent
    flood_mask = (post_water
        .And(pre_water.Not())
        .And(permanent_water.Not())
        .rename('flood'))

    # Step 4: Load SRTM DEM (30m)
    dem = ee.Image("USGS/SRTMGL1_003").select('elevation')

    # Step 5: Get ground elevation only at flooded pixels
    flooded_elevation = dem.updateMask(flood_mask)

    # Step 6: Estimate Water Surface Elevation
    # The focal maximum of flooded pixel elevations within a local radius
    # approximates the WSE: water fills from lower elevations up to the
    # elevation of the highest point it has reached at the flood boundary
    wse = flooded_elevation.focal_max(
        radius=wse_radius_m,
        units='meters',
        iterations=2
    ).reproject(crs='EPSG:4326', scale=30)

    # Step 7: Depth = WSE - ground elevation, clipped to 0 minimum
    depth_meters = (wse
        .subtract(dem)
        .updateMask(flood_mask)
        .max(ee.Image(0.0))    # No negative depths
        .rename('depth_m'))

    # Step 8: Convert to feet
    depth_feet = depth_meters.multiply(3.28084).rename('depth_ft')

    # Step 9: Unmask depth with 0 so dry properties return 0 not null
    flood_float = flood_mask.float().unmask(0)
    depth_unmasked = depth_feet.unmask(0)

    # Step 10: Combine into single 2-band image for efficient sampling
    combined = flood_float.addBands(depth_unmasked)

    return combined


# ─────────────────────────────────────────────────────────────────────────────
# PROPERTY-LEVEL SAMPLING
# ─────────────────────────────────────────────────────────────────────────────

def sample_properties(combined_image, properties_df, batch_size=100):
    """
    Sample flood fraction and max depth at each property location.

    Uses a 50m buffer around each property centroid (covers typical
    residential building footprint + immediate surroundings).

    combined_image must be a 2-band image: ['flood', 'depth_ft']

    Returns DataFrame with columns:
    property_id, address, pct_flooded (0-1), max_depth_ft
    """
    print(f"  Sampling {len(properties_df)} properties in batches of {batch_size}...")

    # Combined reducer: mean for flood band, max for depth band
    # With sharedInputs=True, both reducers are applied to both bands.
    # Output keys: flood_mean, flood_max, depth_ft_mean, depth_ft_max
    # We use: flood_mean (= fraction flooded) and depth_ft_max (= max depth)
    combined_reducer = ee.Reducer.mean().combine(
        reducer2=ee.Reducer.max(),
        sharedInputs=True
    )

    all_results = []

    for batch_start in range(0, len(properties_df), batch_size):
        batch_df = properties_df.iloc[batch_start : batch_start + batch_size]

        # Build GEE FeatureCollection for this batch
        features = []
        for _, row in batch_df.iterrows():
            point   = ee.Geometry.Point([float(row['longitude']), float(row['latitude'])])
            buffered = point.buffer(50)  # 50m buffer
            features.append(ee.Feature(buffered, {
                'property_id': str(row['property_id']),
                'address':     str(row['address'])
            }))

        fc = ee.FeatureCollection(features)

        try:
            sampled = combined_image.reduceRegions(
                collection=fc,
                reducer=combined_reducer,
                scale=30
            )

            result_info = sampled.getInfo()

            for feat in result_info.get('features', []):
                p = feat.get('properties', {})
                all_results.append({
                    'property_id':  p.get('property_id', ''),
                    'address':      p.get('address', ''),
                    'pct_flooded':  round(max(0.0, float(p.get('flood_mean') or 0)), 4),
                    'max_depth_ft': round(max(0.0, float(p.get('depth_ft_max') or 0)), 2),
                })

        except Exception as e:
            print(f"  Batch {batch_start}–{batch_start + batch_size} failed: {e}")
            print(f"  Retrying one property at a time...")
            time.sleep(8)

            for _, row in batch_df.iterrows():
                try:
                    point    = ee.Geometry.Point([float(row['longitude']), float(row['latitude'])])
                    buffered = point.buffer(50)

                    result = combined_image.reduceRegion(
                        reducer=combined_reducer,
                        geometry=buffered,
                        scale=30
                    ).getInfo()

                    all_results.append({
                        'property_id':  str(row['property_id']),
                        'address':      str(row['address']),
                        'pct_flooded':  round(max(0.0, float(result.get('flood_mean') or 0)), 4),
                        'max_depth_ft': round(max(0.0, float(result.get('depth_ft_max') or 0)), 2),
                    })
                    time.sleep(0.3)

                except Exception as e2:
                    # Include property with zero values rather than dropping it
                    all_results.append({
                        'property_id':  str(row['property_id']),
                        'address':      str(row['address']),
                        'pct_flooded':  0.0,
                        'max_depth_ft': 0.0,
                    })

        processed = min(batch_start + batch_size, len(properties_df))
        print(f"  Progress: {processed}/{len(properties_df)} properties sampled")
        time.sleep(1.5)  # Pause between batches — keeps GEE happy

    return pd.DataFrame(all_results)


# ─────────────────────────────────────────────────────────────────────────────
# FULL EVENT PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def run_flood_pipeline(event_config):
    """
    Run the complete SAR flood analysis for one event.
    Reads from outputs/{event_id}_properties.csv
    Writes to  outputs/{event_id}_raw.csv
    """
    event_id   = event_config['event_id']
    event_name = event_config['event_name']

    print(f"\n{'=' * 60}")
    print(f"  {event_name}")
    print(f"  Study area: {event_config['study_name']}")
    print(f"{'=' * 60}")

    # 1. Load property list
    props_path = os.path.join(OUTPUT_DIR, f"{event_id}_properties.csv")
    properties_df = pd.read_csv(props_path)
    print(f"\nLoaded {len(properties_df)} properties from {props_path}")

    # 2. Load pre-event SAR composite
    print("\nLoading pre-event Sentinel-1 imagery...")
    pre_image, pre_count, orbit = load_sar_composite(
        event_config['bbox'],
        event_config['pre_start'],
        event_config['pre_end']
    )
    print(f"  Pre-event: {pre_count} scenes ({event_config['pre_start']} to {event_config['pre_end']}), orbit: {orbit}")

    # 3. Load post-event SAR composite (same orbit direction for consistency)
    print("Loading post-event Sentinel-1 imagery...")
    post_image, post_count, _ = load_sar_composite(
        event_config['bbox'],
        event_config['post_start'],
        event_config['post_end'],
        orbit_pass=orbit      # Force same orbit direction as pre-event
    )
    print(f"  Post-event: {post_count} scenes ({event_config['post_start']} to {event_config['post_end']})")

    # 4. Build flood depth image
    print("\nBuilding flood extent and depth map...")
    combined_image = build_flood_depth_image(
        event_config['bbox'],
        pre_image,
        post_image,
        event_config['wse_radius_m']
    )
    print("  Flood map computed (lazy evaluation — will run during sampling)")

    # 5. Sample at property locations
    print("\nSampling flood data at property locations...")
    flood_df = sample_properties(combined_image, properties_df, batch_size=100)

    # 6. Merge with original property data
    result_df = properties_df.merge(
        flood_df[['property_id', 'pct_flooded', 'max_depth_ft']],
        on='property_id',
        how='left'
    )
    result_df['pct_flooded']  = result_df['pct_flooded'].fillna(0.0)
    result_df['max_depth_ft'] = result_df['max_depth_ft'].fillna(0.0)

    # 7. Print summary
    flooded = (result_df['max_depth_ft'] > 0.1).sum()
    avg_depth = result_df[result_df['max_depth_ft'] > 0.1]['max_depth_ft'].mean()
    print(f"\nResults for {event_name}:")
    print(f"  Total properties:         {len(result_df):,}")
    print(f"  Properties with flooding: {flooded:,} ({flooded/len(result_df)*100:.1f}%)")
    if flooded > 0:
        print(f"  Avg depth (flooded only): {avg_depth:.2f} ft")

    # 8. Save raw output
    output_path = os.path.join(OUTPUT_DIR, f"{event_id}_raw.csv")
    result_df.to_csv(output_path, index=False)
    print(f"\n✓ Raw data saved → {output_path}")

    return result_df


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    harvey_raw = run_flood_pipeline(HARVEY)
    ian_raw    = run_flood_pipeline(IAN)

    print("\n" + "=" * 60)
    print("✓ Day 1 complete. Both raw CSVs are in outputs/")
    print(f"  harvey_raw.csv: {len(harvey_raw)} properties")
    print(f"  ian_raw.csv:    {len(ian_raw)} properties")
    print("=" * 60)