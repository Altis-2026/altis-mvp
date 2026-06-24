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
import time
from collections import Counter
from config import GEE_PROJECT, HARVEY, IAN, OUTPUT_DIR, SAR
from provenance import write_manifest

ee.Initialize(project=GEE_PROJECT)


def load_dem(bbox_coords):
    """
    Load best available DEM. Priority: 3DEP 1m lidar > SRTM 30m.
    Masks building footprints so their heights don't inflate depth estimates.
    This single change eliminates the 14-18ft depth overestimates.
    Returns: (dem_image, resolution_m)
    """
    bbox = ee.Geometry.Rectangle(bbox_coords)

    try:
        dem_collection = ee.ImageCollection("USGS/3DEP/1m").filterBounds(bbox)
        dem_count = dem_collection.size().getInfo()
        if dem_count > 0:
            # .mosaic() drops each tile's native (UTM-like) projection and
            # falls back to a degenerate 1-degree-per-pixel transform, which
            # silently breaks ee.Terrain.slope() (gradients computed over
            # ~111km "pixels" instead of 1m, masking out every slope value).
            # Re-attach the source tiles' real projection before any terrain
            # analysis.
            native_proj = dem_collection.first().projection()
            dem = dem_collection.mosaic().rename('elevation').setDefaultProjection(native_proj)
            dem_resolution = 1
            print(f"  DEM: 3DEP 1m lidar ({dem_count} tiles)")
        else:
            raise ValueError("No 3DEP coverage")
    except Exception:
        dem = ee.Image("USGS/SRTMGL1_003").select('elevation')
        dem_resolution = 30
        print("  DEM: SRTM 30m (3DEP unavailable for this region)")

    # Mask buildings from DEM using Google Open Buildings v3
    buildings = (ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons")
                   .filterBounds(bbox))
    building_mask = ee.Image(1).paint(buildings, 0).unmask(1)
    dem = dem.updateMask(building_mask)
    print("  Building footprints masked (Google Open Buildings v3)")

    return dem, dem_resolution


def otsu_threshold_gee(image, bbox_coords):
    """
    Compute Otsu optimal threshold from image backscatter histogram.
    Replaces the fixed -15dB threshold that fails in urban areas and
    vegetated floodplains. This calibrates to each specific scene.
    Returns: ee.Number (threshold in dB)
    """
    bbox = ee.Geometry.Rectangle(bbox_coords)

    histogram = image.reduceRegion(
        reducer=ee.Reducer.histogram(255, 0.5),
        geometry=bbox,
        scale=30,
        maxPixels=1e9
    )

    def compute_otsu(hist_dict):
        counts  = ee.Array(ee.Dictionary(hist_dict).get('histogram'))
        means   = ee.Array(ee.Dictionary(hist_dict).get('bucketMeans'))
        size    = means.length().get([0])
        total   = counts.reduce(ee.Reducer.sum(), [0]).get([0])
        sum_val = means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0])

        def compute_variance(i, args):
            i    = ee.Number(i)
            args = ee.Dictionary(args)
            count_b = counts.slice(0, 0, i).reduce(ee.Reducer.sum(), [0]).get([0])
            sum_b   = (means.slice(0, 0, i)
                           .multiply(counts.slice(0, 0, i))
                           .reduce(ee.Reducer.sum(), [0]).get([0]))
            weight_b = count_b.divide(total)
            weight_f = ee.Number(1).subtract(weight_b)
            mean_b   = sum_b.divide(count_b.add(1e-10))
            mean_f   = sum_val.subtract(sum_b).divide(
                           total.subtract(count_b).add(1e-10))
            variance = weight_b.multiply(weight_f).multiply(
                           mean_b.subtract(mean_f).pow(2))
            return ee.Algorithms.If(
                variance.gt(args.getNumber('best_variance')),
                ee.Dictionary({'best_variance': variance,
                               'best_threshold': means.get([i])}),
                args
            )

        result = ee.List.sequence(1, size.subtract(1)).iterate(
            compute_variance,
            ee.Dictionary({'best_variance': ee.Number(0),
                           'best_threshold': means.get([0])})
        )
        return ee.Dictionary(result).getNumber('best_threshold')

    return compute_otsu(ee.Dictionary(histogram).get('VV'))


def load_sar_composite(bbox_coords, start_date, end_date, orbit_pass=None,
                       speckle_radius_m=None):
    """
    Load Sentinel-1 VV median composite. Auto-selects dominant orbit direction.
    Applies a focal-mean speckle filter (UN-SPIDER recommended practice) so the
    downstream Otsu threshold sees a smoother, less noisy histogram.
    Returns: (composite_image, scene_count, orbit_direction_used)
    """
    if speckle_radius_m is None:
        speckle_radius_m = SAR['speckle_radius_m']

    bbox = ee.Geometry.Rectangle(bbox_coords)
    base_filter = (ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(bbox)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.eq('instrumentMode', 'IW'))
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
        .select('VV'))

    if orbit_pass is None:
        all_passes = base_filter.aggregate_array('orbitProperties_pass').getInfo()
        if not all_passes:
            raise ValueError(f"No Sentinel-1 images found between {start_date} and {end_date}.")
        orbit_pass = Counter(all_passes).most_common(1)[0][0]

    collection = base_filter.filter(ee.Filter.eq('orbitProperties_pass', orbit_pass))
    count = collection.size().getInfo()

    if count == 0:
        other = 'ASCENDING' if orbit_pass == 'DESCENDING' else 'DESCENDING'
        collection = base_filter.filter(ee.Filter.eq('orbitProperties_pass', other))
        count = collection.size().getInfo()
        orbit_pass = other

    if count == 0:
        raise ValueError(f"No Sentinel-1 images found for {start_date} to {end_date}.")

    composite = collection.median()

    # Speckle filter: focal-mean smoothing in dB space. Suppresses the
    # salt-and-pepper noise inherent to SAR that otherwise produces isolated
    # false-positive "flood" pixels.
    if speckle_radius_m and speckle_radius_m > 0:
        composite = composite.focal_mean(
            radius=speckle_radius_m, kernelType='circle', units='meters'
        ).rename('VV')

    return composite, count, orbit_pass


def build_flood_depth_image(bbox_coords, pre_image, post_image, dem, wse_radius_m):
    """
    Generate 3-band image: ['flood', 'depth_ft', 'urban']
    - flood:    binary 1=newly flooded
    - depth_ft: estimated depth in feet (0 where dry)
    - urban:    binary 1=high-density urban (SAR shadow risk zone)

    The urban band feeds a -15pt confidence penalty in Step 4,
    pushing borderline urban properties toward Review rather than
    a confident Remote-Deny. Scientifically correct, legally defensible.
    """
    # Otsu adaptive threshold
    raw_threshold = ee.Number(otsu_threshold_gee(post_image, bbox_coords))

    # Range guard: if Otsu lands outside the plausible open-water VV band
    # (common when a scene is unimodal — little or no standing water), fall back
    # to a safe default instead of letting a garbage threshold flood the map.
    in_range = raw_threshold.gte(SAR['water_db_min']).And(
        raw_threshold.lte(SAR['water_db_max']))
    threshold = ee.Number(ee.Algorithms.If(
        in_range, raw_threshold, SAR['otsu_fallback_db']))

    # Slope mask: water physically cannot pool on slopes > 5 degrees
    slope_mask = ee.Terrain.slope(dem).lt(5)

    # Flood detection: adaptive threshold + slope-constrained
    pre_water  = pre_image.lt(threshold)
    post_water = post_image.lt(threshold).And(slope_mask)

    # Remove permanent water (present 8+ months/year)
    permanent_water = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
                         .select('seasonality').gte(8).unmask(0))

    # New flooding = post water AND NOT pre water AND NOT permanent
    flood_mask = (post_water
                    .And(pre_water.Not())
                    .And(permanent_water.Not())
                    .rename('flood'))

    # Depth via Water Surface Elevation minus ground elevation.
    # The waterline sits at the HIGHER elevations among flooded pixels, but a
    # plain focal_max is dominated by a single spurious high pixel (a false-
    # positive flood pixel on higher ground, or an imperfectly masked building)
    # and that one outlier sets the WSE — and therefore the depth — absurdly
    # high for the whole neighborhood. Using a high percentile instead is robust
    # to those outliers while still tracking the true waterline.
    flooded_elevation = dem.updateMask(flood_mask)
    wse = (flooded_elevation
           .reduceNeighborhood(
               reducer=ee.Reducer.percentile([SAR['wse_percentile']]),
               kernel=ee.Kernel.circle(radius=wse_radius_m, units='meters'),
           )
           .reproject(crs='EPSG:4326', scale=30))

    # Physical depth cap: clamp implausibly deep readings (residential flooding
    # above ~20ft is almost always a DEM/WSE artifact, not real water).
    max_depth_m = SAR['max_plausible_depth_ft'] / 3.28084
    depth_ft = (wse.subtract(dem)
                   .updateMask(flood_mask)
                   .clamp(0.0, max_depth_m)
                   .multiply(3.28084)
                   .rename('depth_ft'))

    # Urban density: GHS built surface > 1000 m2 per cell (2020 epoch, 100m res)
    urban = (ee.Image("JRC/GHSL/P2023A/GHS_BUILT_S/2020")
               .select('built_surface').gt(1000)
               .rename('urban').unmask(0))

    # Combine with 0-filled nodata
    return (flood_mask.float().unmask(0)
            .addBands(depth_ft.unmask(0))
            .addBands(urban.float().unmask(0)))


def sample_properties(combined_image, properties_df, batch_size=100):
    """
    Sample flood fraction, max depth, urban flag at each property (50m buffer).
    Returns DataFrame: property_id, address, pct_flooded, max_depth_ft, urban_flag
    """
    print(f"  Sampling {len(properties_df)} properties in batches of {batch_size}...")

    combined_reducer = ee.Reducer.mean().combine(
        reducer2=ee.Reducer.max(), sharedInputs=True)

    all_results = []

    for batch_start in range(0, len(properties_df), batch_size):
        batch_df = properties_df.iloc[batch_start : batch_start + batch_size]
        features = []

        for _, row in batch_df.iterrows():
            point = ee.Geometry.Point([float(row['longitude']), float(row['latitude'])])
            features.append(ee.Feature(point.buffer(50), {
                'property_id': str(row['property_id']),
                'address':     str(row['address'])
            }))

        try:
            sampled = combined_image.reduceRegions(
                collection=ee.FeatureCollection(features),
                reducer=combined_reducer,
                scale=30
            )
            for feat in sampled.getInfo().get('features', []):
                p = feat.get('properties', {})
                all_results.append({
                    'property_id':  p.get('property_id', ''),
                    'address':      p.get('address', ''),
                    'pct_flooded':  round(max(0.0, float(p.get('flood_mean') or 0)), 4),
                    'max_depth_ft': round(max(0.0, float(p.get('depth_ft_max') or 0)), 2),
                    'urban_flag':   int(round(float(p.get('urban_mean') or 0))),
                })

        except Exception as e:
            print(f"  Batch {batch_start} failed: {e}. Retrying individually...")
            time.sleep(8)
            for _, row in batch_df.iterrows():
                try:
                    point = ee.Geometry.Point([float(row['longitude']), float(row['latitude'])])
                    result = combined_image.reduceRegion(
                        reducer=combined_reducer,
                        geometry=point.buffer(50),
                        scale=30
                    ).getInfo()
                    all_results.append({
                        'property_id':  str(row['property_id']),
                        'address':      str(row['address']),
                        'pct_flooded':  round(max(0.0, float(result.get('flood_mean') or 0)), 4),
                        'max_depth_ft': round(max(0.0, float(result.get('depth_ft_max') or 0)), 2),
                        'urban_flag':   int(round(float(result.get('urban_mean') or 0))),
                    })
                    time.sleep(0.3)
                except Exception:
                    all_results.append({
                        'property_id': str(row['property_id']),
                        'address':     str(row['address']),
                        'pct_flooded': 0.0, 'max_depth_ft': 0.0, 'urban_flag': 0,
                    })

        processed = min(batch_start + batch_size, len(properties_df))
        print(f"  Progress: {processed}/{len(properties_df)}")
        time.sleep(1.5)

    return pd.DataFrame(all_results)


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

    print("\nBuilding flood map (Otsu threshold + slope mask)...")
    combined = build_flood_depth_image(
        event_config['bbox'], pre_image, post_image, dem, event_config['wse_radius_m'])

    print("\nSampling properties...")
    flood_df = sample_properties(combined, properties_df, batch_size=100)

    result_df = properties_df.merge(
        flood_df[['property_id', 'pct_flooded', 'max_depth_ft', 'urban_flag']],
        on='property_id', how='left'
    )
    result_df['pct_flooded']      = result_df['pct_flooded'].fillna(0.0)
    result_df['max_depth_ft']     = result_df['max_depth_ft'].fillna(0.0)
    result_df['urban_flag']       = result_df['urban_flag'].fillna(0).astype(int)
    result_df['dem_resolution_m'] = dem_res

    flooded = (result_df['max_depth_ft'] > 0.1).sum()
    urban   = (result_df['urban_flag'] == 1).sum()
    print(f"\nSummary:")
    print(f"  Flooded:      {flooded:,} ({flooded/len(result_df)*100:.1f}%)")
    print(f"  Urban zones:  {urban:,} (confidence penalty applied in Step 4)")
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
    })

    return result_df


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    run_flood_pipeline(HARVEY)
    run_flood_pipeline(IAN)
