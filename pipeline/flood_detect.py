"""
flood_detect.py — Core Sentinel-1 SAR flood-detection on Google Earth Engine.

Single source of truth for the detection science, importable from both the
batch script (03_flood_pipeline.py) and the backend's live, on-demand analysis
(backend/live_pipeline.py). Contains NO module-level ee.Initialize() — the
caller is responsible for authenticating EE (the batch script with an
interactive project, the backend with a service account), which is what lets
the same code run for any location on Earth.

Method (identical to the validated demo pipeline):
  - DEM: 3DEP 1m lidar (US) → Copernicus GLO-30 (global) → SRTM 30m, with
    building footprints masked so roofs don't inflate depth.
  - SAR: Sentinel-1 VV median, dominant-orbit auto-select, speckle filter.
  - Threshold: per-scene Otsu with an open-water range guard.
  - Constraints: slope mask (water can't pool on >5° slopes) + JRC permanent
    water removed.
  - Depth: water-surface-elevation (robust high percentile) minus ground.
  - Optical cross-check: Sentinel-2 MNDWI when a cloud-free scene exists.
"""
import time
from collections import Counter

import ee
import pandas as pd

try:
    from config import SAR, OPTICAL
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import SAR, OPTICAL


def load_dem(bbox_coords):
    """
    Best available DEM, masking building footprints.
    Priority: 3DEP 1m lidar (US) > Copernicus GLO-30 (global) > SRTM 30m.
    Returns: (dem_image, resolution_m)
    """
    bbox = ee.Geometry.Rectangle(bbox_coords)

    dem = None
    dem_resolution = 30

    # 1) 3DEP 1m lidar — US only, best vertical accuracy.
    try:
        dem_collection = ee.ImageCollection("USGS/3DEP/1m").filterBounds(bbox)
        if dem_collection.size().getInfo() > 0:
            native_proj = dem_collection.first().projection()
            dem = (dem_collection.mosaic().rename('elevation')
                   .setDefaultProjection(native_proj))
            dem_resolution = 1
            print("  DEM: 3DEP 1m lidar")
    except Exception:
        dem = None

    # 2) Copernicus GLO-30 — truly global 30m, better than SRTM. Mosaic loses
    #    each tile's native projection (which silently breaks slope), so we
    #    re-attach it before any terrain analysis, same as the 3DEP path.
    if dem is None:
        try:
            glo = ee.ImageCollection("COPERNICUS/DEM/GLO30").select('DEM').filterBounds(bbox)
            if glo.size().getInfo() > 0:
                native_proj = glo.first().projection()
                dem = glo.mosaic().rename('elevation').setDefaultProjection(native_proj)
                dem_resolution = 30
                print("  DEM: Copernicus GLO-30 (global 30m)")
        except Exception:
            dem = None

    # 3) SRTM 30m — near-global fallback.
    if dem is None:
        dem = ee.Image("USGS/SRTMGL1_003").select('elevation')
        dem_resolution = 30
        print("  DEM: SRTM 30m (fallback)")

    # Mask buildings (Google Open Buildings v3 — covers the Global South incl.
    # Latin America; empty elsewhere, in which case nothing is masked).
    buildings = (ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons")
                 .filterBounds(bbox))
    building_mask = ee.Image(1).paint(buildings, 0).unmask(1)
    dem = dem.updateMask(building_mask)

    return dem, dem_resolution


def otsu_threshold_gee(image, bbox_coords):
    """Otsu optimal threshold (dB) from the scene's backscatter histogram."""
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

    band_name = ee.String(image.bandNames().get(0))
    return compute_otsu(ee.Dictionary(histogram).get(band_name))


def load_sar_composite(bbox_coords, start_date, end_date, orbit_pass=None,
                       speckle_radius_m=None):
    """
    Sentinel-1 VV median composite, dominant-orbit auto-select + speckle filter.
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

    if speckle_radius_m and speckle_radius_m > 0:
        composite = composite.focal_mean(
            radius=speckle_radius_m, kernelType='circle', units='meters'
        ).rename('VV')

    return composite, count, orbit_pass


def load_optical_water_mask(bbox_coords, start_date, end_date):
    """
    Sentinel-2 MNDWI water mask — independent second sensor for cross-check.
    Returns (water_mask, valid_mask, scene_count); masks are None if no
    cloud-free scene exists in the window.
    """
    bbox = ee.Geometry.Rectangle(bbox_coords)
    collection = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                  .filterBounds(bbox)
                  .filterDate(start_date, end_date)
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', OPTICAL['max_cloud_pct'])))

    count = collection.size().getInfo()
    if count == 0:
        return None, None, 0

    def mask_clouds(img):
        scl = img.select('SCL')
        clear = (scl.neq(3).And(scl.neq(8)).And(scl.neq(9))
                 .And(scl.neq(10)).And(scl.neq(11)))
        mndwi = img.normalizedDifference(['B3', 'B11']).rename('mndwi')
        return img.addBands(mndwi).updateMask(clear)

    masked = collection.map(mask_clouds)
    mndwi_median = masked.select('mndwi').median()
    obs_count = masked.select('mndwi').count()

    water_mask = mndwi_median.gt(OPTICAL['water_mndwi_min']).rename('optical_water')
    valid_mask = obs_count.gte(1).rename('optical_valid')

    return water_mask, valid_mask, count


def build_flood_depth_image(bbox_coords, pre_image, post_image, dem, wse_radius_m,
                            optical_water=None, optical_valid=None):
    """
    Build the multi-band analysis image:
    ['flood', 'depth_ft', 'urban', 'wse_spread_ft', 'rel_elev_ft',
     'optical_water', 'optical_valid'].
    """
    raw_threshold = ee.Number(otsu_threshold_gee(post_image, bbox_coords))

    in_range = raw_threshold.gte(SAR['water_db_min']).And(
        raw_threshold.lte(SAR['water_db_max']))
    threshold = ee.Number(ee.Algorithms.If(
        in_range, raw_threshold, SAR['otsu_fallback_db']))

    slope_mask = ee.Terrain.slope(dem).lt(5)

    pre_water  = pre_image.lt(threshold)
    post_water = post_image.lt(threshold).And(slope_mask)

    permanent_water = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
                       .select('seasonality').gte(8).unmask(0))

    flood_mask = (post_water
                  .And(pre_water.Not())
                  .And(permanent_water.Not())
                  .rename('flood'))

    flooded_elevation = dem.updateMask(flood_mask)
    wse = (flooded_elevation
           .reduceNeighborhood(
               reducer=ee.Reducer.percentile([SAR['wse_percentile']]),
               kernel=ee.Kernel.circle(radius=wse_radius_m, units='meters'),
           )
           .reproject(crs='EPSG:4326', scale=30))

    max_depth_m = SAR['max_plausible_depth_ft'] / 3.28084
    depth_ft = (wse.subtract(dem)
                .updateMask(flood_mask)
                .clamp(0.0, max_depth_m)
                .multiply(3.28084)
                .rename('depth_ft'))

    wse_spread_ft = (flooded_elevation
                     .reduceNeighborhood(
                         reducer=ee.Reducer.stdDev(),
                         kernel=ee.Kernel.circle(radius=wse_radius_m, units='meters'),
                     )
                     .reproject(crs='EPSG:4326', scale=30)
                     .multiply(3.28084)
                     .rename('wse_spread_ft'))

    neigh_min = (dem.reduceNeighborhood(
                     reducer=ee.Reducer.min(),
                     kernel=ee.Kernel.circle(radius=wse_radius_m, units='meters'),
                 )
                 .reproject(crs='EPSG:4326', scale=30))
    rel_elev_ft = (dem.subtract(neigh_min)
                   .multiply(3.28084)
                   .rename('rel_elev_ft'))

    urban = (ee.Image("JRC/GHSL/P2023A/GHS_BUILT_S/2020")
             .select('built_surface').gt(1000)
             .rename('urban').unmask(0))

    combined = (flood_mask.float().unmask(0)
                .addBands(depth_ft.unmask(0))
                .addBands(urban.float().unmask(0))
                .addBands(wse_spread_ft.unmask(0))
                .addBands(rel_elev_ft.unmask(0)))

    if optical_water is not None:
        combined = combined.addBands(optical_water.float().unmask(0))
    else:
        combined = combined.addBands(ee.Image(0).rename('optical_water').float())
    if optical_valid is not None:
        combined = combined.addBands(optical_valid.float().unmask(0))
    else:
        combined = combined.addBands(ee.Image(0).rename('optical_valid').float())

    return combined


def sample_properties(combined_image, properties_df, batch_size=100, throttle=True):
    """
    Sample flood fraction, max depth, urban flag, optical cross-check, WSE
    spread, and relative elevation at each property (50m buffer).
    Returns a DataFrame keyed by property_id.

    `throttle=False` skips the inter-batch sleeps — used for small live
    portfolios (a single batch) where the demo wants a fast turnaround.
    """
    combined_reducer = ee.Reducer.mean().combine(
        reducer2=ee.Reducer.max(), sharedInputs=True)

    all_results = []

    for batch_start in range(0, len(properties_df), batch_size):
        batch_df = properties_df.iloc[batch_start: batch_start + batch_size]
        features = []
        for _, row in batch_df.iterrows():
            point = ee.Geometry.Point([float(row['longitude']), float(row['latitude'])])
            features.append(ee.Feature(point.buffer(50), {
                'property_id': str(row['property_id']),
                'address':     str(row.get('address', '')),
            }))

        try:
            sampled = combined_image.reduceRegions(
                collection=ee.FeatureCollection(features),
                reducer=combined_reducer,
                scale=30
            )
            for feat in sampled.getInfo().get('features', []):
                p = feat.get('properties', {})
                all_results.append(_row_from_sample(p.get('property_id', ''),
                                                    p.get('address', ''), p))
        except Exception as e:
            print(f"  Batch {batch_start} failed: {e}. Retrying individually...")
            time.sleep(5)
            for _, row in batch_df.iterrows():
                try:
                    point = ee.Geometry.Point([float(row['longitude']), float(row['latitude'])])
                    result = combined_image.reduceRegion(
                        reducer=combined_reducer,
                        geometry=point.buffer(50),
                        scale=30
                    ).getInfo()
                    all_results.append(_row_from_sample(
                        str(row['property_id']), str(row.get('address', '')), result))
                    time.sleep(0.3)
                except Exception:
                    all_results.append(_row_from_sample(
                        str(row['property_id']), str(row.get('address', '')), {}))

        if throttle:
            time.sleep(1.5)

    return pd.DataFrame(all_results)


def _row_from_sample(property_id, address, p):
    """Normalize one reduceRegion(s) result dict into the standard row shape."""
    return {
        'property_id':  property_id,
        'address':      address,
        'pct_flooded':  round(max(0.0, float(p.get('flood_mean') or 0)), 4),
        'max_depth_ft': round(max(0.0, float(p.get('depth_ft_max') or 0)), 2),
        'urban_flag':   int(round(float(p.get('urban_mean') or 0))),
        'optical_available': int(
            float(p.get('optical_valid_mean') or 0) >= OPTICAL['min_valid_fraction']),
        'optical_water_pct': round(max(0.0, float(p.get('optical_water_mean') or 0)), 4),
        'wse_spread_ft': round(max(0.0, float(p.get('wse_spread_ft_mean') or 0)), 3),
        'rel_elev_ft':   round(max(0.0, float(p.get('rel_elev_ft_mean') or 0)), 3),
    }
