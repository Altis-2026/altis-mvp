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
    from config import (SAR, OPTICAL, SAR_VH, DURATION, RAIN,
                        BASELINE, HAND, CROSS_ORBIT)
except ImportError:  # pragma: no cover - import path guard
    from pipeline.config import (SAR, OPTICAL, SAR_VH, DURATION, RAIN,
                                 BASELINE, HAND, CROSS_ORBIT)


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


def baseline_window(post_start, months=None, gap_days=None):
    """
    Derive the (start, end) dates of the multi-temporal baseline window that
    ends shortly before an event's post window opens.

    Pure date arithmetic, no Earth Engine — kept separate so it stays unit
    testable without a network round trip.
    """
    from datetime import datetime, timedelta

    months = BASELINE['months'] if months is None else months
    gap_days = BASELINE['gap_days'] if gap_days is None else gap_days

    end = datetime.strptime(post_start, "%Y-%m-%d") - timedelta(days=gap_days)
    start = end - timedelta(days=int(round(months * 30.44)))
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def load_sar_baseline(bbox_coords, baseline_start, baseline_end, orbit_pass,
                      speckle_radius_m=None):
    """
    Per-pixel statistical baseline (mean + standard deviation, dB) from a long
    run of pre-event Sentinel-1 scenes on ONE orbit.

    This is the multi-temporal replacement for a single pre-event composite.
    Returns (mean_image|None, std_image|None, scene_count). When fewer than
    BASELINE['min_scenes'] scenes exist the caller is expected to fall back to
    the single-composite path — we return the count so that decision is made
    explicitly rather than by silently thresholding on a bad std estimate.

    Same orbit only: ascending and descending passes see different backscatter
    for the same ground, so a pooled baseline would have inflated variance and
    a meaningless mean.
    """
    if speckle_radius_m is None:
        speckle_radius_m = SAR['speckle_radius_m']

    bbox = ee.Geometry.Rectangle(bbox_coords)
    collection = (ee.ImageCollection("COPERNICUS/S1_GRD")
                  .filterBounds(bbox)
                  .filterDate(baseline_start, baseline_end)
                  .filter(ee.Filter.eq('instrumentMode', 'IW'))
                  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
                  .filter(ee.Filter.eq('orbitProperties_pass', orbit_pass))
                  .select('VV'))

    count = collection.size().getInfo()
    if count < 1:
        return None, None, 0

    # Speckle-filter each scene BEFORE reducing, so the variance we measure is
    # real scene-to-scene variability rather than per-scene speckle noise.
    if speckle_radius_m and speckle_radius_m > 0:
        collection = collection.map(
            lambda img: img.focal_mean(radius=speckle_radius_m,
                                       kernelType='circle', units='meters')
                           .rename('VV').copyProperties(img, ['system:time_start']))

    mean = collection.mean().rename('baseline_mean')
    std = collection.reduce(ee.Reducer.stdDev()).rename('baseline_std')
    return mean, std, count


def load_hand(bbox_coords):
    """
    Height Above Nearest Drainage, in FEET, from MERIT Hydro.

    Returns (image|None, source_label). MERIT Hydro's `hnd` band is global at
    ~90m and stored in metres. Water bodies and ocean are masked in the source,
    which is correct behaviour here: a pixel with no HAND value is one where
    the "how high above drainage" question has no meaning, and the ensemble
    vote should abstain rather than assume.
    """
    if not HAND.get('enabled', True):
        return None, 'disabled'
    try:
        bbox = ee.Geometry.Rectangle(bbox_coords)
        hand_m = ee.Image(HAND['asset']).select(HAND['band']).clip(bbox)
        return hand_m.multiply(3.28084).rename('hand_ft'), HAND['asset']
    except Exception as e:  # pragma: no cover - EE availability guard
        print(f"  HAND unavailable ({e}); falling back to relative elevation.")
        return None, 'unavailable'


def load_sar_orbits(bbox_coords, start_date, end_date, speckle_radius_m=None,
                    min_scenes=None):
    """
    VV median composite for EVERY orbit pass with data in the window, instead
    of only the dominant one.

    Returns {orbit_pass: (composite, scene_count)}. Keeping the orbits separate
    is the whole point — each one carries its own incidence geometry, so each
    gets its own Otsu threshold and its own baseline downstream, and only the
    finished boolean flood masks are ever combined.

    This is what shrinks the revisit gap: a flood that peaks between two
    ascending passes may still have been caught by a descending one.
    """
    if speckle_radius_m is None:
        speckle_radius_m = SAR['speckle_radius_m']
    if min_scenes is None:
        min_scenes = CROSS_ORBIT['min_scenes_per_orbit']

    bbox = ee.Geometry.Rectangle(bbox_coords)
    base = (ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(bbox)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.eq('instrumentMode', 'IW'))
            .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
            .select('VV'))

    passes = base.aggregate_array('orbitProperties_pass').getInfo() or []
    out = {}
    for orbit in sorted(set(passes)):
        subset = base.filter(ee.Filter.eq('orbitProperties_pass', orbit))
        n = subset.size().getInfo()
        if n < min_scenes:
            continue
        composite = subset.median()
        if speckle_radius_m and speckle_radius_m > 0:
            composite = composite.focal_mean(
                radius=speckle_radius_m, kernelType='circle', units='meters'
            ).rename('VV')
        out[orbit] = (composite, n)
    return out


def load_sar_vh_composite(bbox_coords, start_date, end_date, orbit_pass):
    """
    Sentinel-1 VH median composite for the dual-polarization cross-check.
    Same orbit/window discipline as the VV composite; returns (image|None,
    scene_count). None when no VH-capable scene exists — the check abstains
    rather than guessing. (True InSAR coherence needs SLC phase data, which
    GEE doesn't distribute; this amplitude-based dual-pol check is the honest
    equivalent used in operational flood mapping.)
    """
    bbox = ee.Geometry.Rectangle(bbox_coords)
    collection = (ee.ImageCollection("COPERNICUS/S1_GRD")
                  .filterBounds(bbox)
                  .filterDate(start_date, end_date)
                  .filter(ee.Filter.eq('instrumentMode', 'IW'))
                  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
                  .filter(ee.Filter.eq('orbitProperties_pass', orbit_pass))
                  .select('VH'))
    count = collection.size().getInfo()
    if count == 0:
        return None, 0

    composite = collection.median()
    if SAR['speckle_radius_m']:
        composite = composite.focal_mean(
            radius=SAR['speckle_radius_m'], kernelType='circle', units='meters'
        ).rename('VH')
    return composite, count


def load_sar_slice(bbox_coords, start_date, end_date, orbit_pass):
    """
    VV median composite for one inundation-duration slice — STRICT orbit
    (no cross-orbit fallback, so every slice is statistically comparable to
    the main post composite whose threshold it shares). Returns
    (image|None, scene_count); None means the slice abstains.
    """
    bbox = ee.Geometry.Rectangle(bbox_coords)
    collection = (ee.ImageCollection("COPERNICUS/S1_GRD")
                  .filterBounds(bbox)
                  .filterDate(start_date, end_date)
                  .filter(ee.Filter.eq('instrumentMode', 'IW'))
                  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
                  .filter(ee.Filter.eq('orbitProperties_pass', orbit_pass))
                  .select('VV'))
    count = collection.size().getInfo()
    if count == 0:
        return None, 0
    composite = collection.median()
    if SAR['speckle_radius_m']:
        composite = composite.focal_mean(
            radius=SAR['speckle_radius_m'], kernelType='circle', units='meters'
        ).rename('VV')
    return composite, count


def load_rainfall_sum(bbox_coords, start_date, end_date):
    """
    CHIRPS daily precipitation summed over [start, end], in millimetres.
    Global coverage over land; returns (image 'rain_mm', day_count).
    """
    collection = (ee.ImageCollection(RAIN['dataset'])
                  .filterDate(start_date, end_date)
                  .select('precipitation'))
    return collection.sum().rename('rain_mm'), collection.size()


def load_ndvi_median(bbox_coords, start_date, end_date):
    """
    Cloud-masked Sentinel-2 NDVI median over a window — one half of the
    pre-vs-post vegetation-loss delta. Returns (image|None, scene_count).
    """
    bbox = ee.Geometry.Rectangle(bbox_coords)
    collection = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                  .filterBounds(bbox)
                  .filterDate(start_date, end_date)
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', OPTICAL['max_cloud_pct'])))
    count = collection.size().getInfo()
    if count == 0:
        return None, 0

    def ndvi(img):
        scl = img.select('SCL')
        clear = (scl.neq(3).And(scl.neq(8)).And(scl.neq(9))
                 .And(scl.neq(10)).And(scl.neq(11)))
        return img.normalizedDifference(['B8', 'B4']).rename('ndvi').updateMask(clear)

    return collection.map(ndvi).median(), count


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


def guarded_otsu(image, bbox_coords, evaluate=False):
    """
    Per-scene Otsu threshold with the open-water range guard applied.

    Factored out because cross-orbit stacking needs one threshold PER ORBIT —
    ascending and descending scenes have different backscatter distributions,
    so sharing a single threshold between them would systematically bias one
    of the two.

    `evaluate=True` resolves the threshold to a plain Python float via a single
    getInfo() and returns it as a CONSTANT.

    WHY THAT MATTERS: the Otsu threshold is a reduceRegion over the entire
    study-area bbox. Left symbolic, it sits inside the flood-mask graph, so
    Earth Engine recomputes that whole-bbox histogram on every sampling batch.
    At 20 batches that is merely wasteful; on a widened bbox at 80+ batches it
    is the difference between a run finishing and a run that has to be killed
    (observed twice, past 30 minutes with no output). Resolving it once up
    front costs one round trip and changes no result — the threshold is a
    per-scene constant either way.
    """
    raw = ee.Number(otsu_threshold_gee(image, bbox_coords))
    in_range = raw.gte(SAR['water_db_min']).And(raw.lte(SAR['water_db_max']))
    threshold = ee.Number(ee.Algorithms.If(in_range, raw, SAR['otsu_fallback_db']))
    if evaluate:
        return ee.Number(float(threshold.getInfo()))
    return threshold


def orbit_flood_mask(post_img, threshold, slope_mask, permanent_water,
                     pre_img=None, baseline_mean=None, baseline_std=None):
    """
    One orbit's flood mask, using the multi-temporal baseline when available.

    Two independent tests, and by default a pixel must pass BOTH:

      1. CHANGE — is this pixel anomalously dark relative to its own baseline
         distribution? z = (post - baseline_mean) / baseline_std, flood when
         z <= -z_threshold. This is the multi-temporal upgrade: the comparison
         is against a whole year of that pixel's own history, so a single
         unrepresentative pre-event scene can no longer swing the call, and a
         naturally noisy pixel is held to a proportionally higher bar.

      2. ABSOLUTE — is the backscatter actually in the open-water range?
         Change alone flags any darkening, including harvested fields and
         drying pavement, so the absolute Otsu test is what keeps the change
         test honest.

    Falls back to the original single-pre-scene change detection
    (post < threshold AND NOT pre < threshold) when no baseline was built.
    """
    absolute = post_img.lt(threshold)

    if baseline_mean is not None and baseline_std is not None:
        std = baseline_std.max(BASELINE['min_std_db'])
        z_score = post_img.subtract(baseline_mean).divide(std)
        change = z_score.lte(-BASELINE['z_threshold'])
        mask = change.And(absolute) if BASELINE['require_absolute'] else change
    elif pre_img is not None:
        mask = absolute.And(pre_img.lt(threshold).Not())
    else:
        mask = absolute

    return mask.And(slope_mask).And(permanent_water.Not())


def build_flood_depth_image(bbox_coords, pre_image, post_image, dem, wse_radius_m,
                            optical_water=None, optical_valid=None,
                            pre_vh=None, post_vh=None, rain=None,
                            ndvi_pre=None, ndvi_post=None, post_slices=None,
                            hand=None, baseline_mean=None, baseline_std=None,
                            orbit_stack=None, precompute_thresholds=False):
    """
    Build the multi-band analysis image:
    ['flood', 'depth_ft', 'urban', 'wse_spread_ft', 'rel_elev_ft', 'hand_ft',
     'optical_water', 'optical_valid'] plus, when inputs are supplied:
    'vh_flood'/'vh_valid' (dual-pol cross-check), 'rain_mm' (CHIRPS event
    total), 'ndvi_delta'/'ndvi_valid' (vegetation loss), 'near_water'
    (proximity to permanent water — subrogation/false-positive context) and
    'flood_s{i}' per post-window slice (inundation duration).
    `post_slices` is a list of (vv_composite|None) per slice.

    Phase 1 additions, all optional and all backward compatible — omit them and
    the detector behaves exactly as before:
      `hand`          — HAND image (feet) for the DEM-hydrology vote.
      `baseline_mean` / `baseline_std` — multi-temporal baseline for the
                        primary orbit.
      `orbit_stack`   — {orbit_pass: {'post', 'pre', 'baseline_mean',
                        'baseline_std'}} for cross-orbit stacking. Each orbit
                        is thresholded and masked independently; only the
                        finished boolean masks are combined.
      `precompute_thresholds` — resolve each orbit's Otsu threshold to a
                        constant up front (one getInfo each) instead of leaving
                        it in the graph. Strongly recommended for batch runs
                        over a large bbox; see guarded_otsu().
    """
    threshold = guarded_otsu(post_image, bbox_coords,
                             evaluate=precompute_thresholds)

    slope_mask = ee.Terrain.slope(dem).lt(5)

    pre_water = pre_image.lt(threshold)

    permanent_water = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
                       .select('seasonality').gte(8).unmask(0))

    # ── Primary orbit mask (multi-temporal baseline when supplied).
    primary_mask = orbit_flood_mask(
        post_image, threshold, slope_mask, permanent_water,
        pre_img=pre_image, baseline_mean=baseline_mean, baseline_std=baseline_std)

    # ── Cross-orbit stacking: fold in every additional orbit's independent
    #    mask. Union maximises temporal coverage (the revisit-gap fix); 'agree'
    #    trades that coverage for precision.
    masks = [primary_mask]
    for spec in (orbit_stack or {}).values():
        post_o = spec.get('post')
        if post_o is None:
            continue
        masks.append(orbit_flood_mask(
            post_o, guarded_otsu(post_o, bbox_coords,
                                 evaluate=precompute_thresholds),
            slope_mask, permanent_water,
            pre_img=spec.get('pre'),
            baseline_mean=spec.get('baseline_mean'),
            baseline_std=spec.get('baseline_std')))

    if len(masks) == 1:
        flood_mask = masks[0].rename('flood')
    elif CROSS_ORBIT['combine'] == 'agree':
        combined_mask = masks[0]
        for m in masks[1:]:
            combined_mask = combined_mask.And(m)
        flood_mask = combined_mask.rename('flood')
    else:  # 'union'
        combined_mask = masks[0]
        for m in masks[1:]:
            combined_mask = combined_mask.Or(m)
        flood_mask = combined_mask.rename('flood')

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

    # ── HAND (feet above nearest drainage) — the DEM-hydrology vote's input.
    #    -1 marks "no HAND value here" so sampling can tell abstain from zero;
    #    HAND of 0 is meaningful (you are AT the drainage line) and must not be
    #    conflated with missing data.
    if hand is not None:
        combined = combined.addBands(hand.float().unmask(-1).rename('hand_ft'))
    else:
        combined = combined.addBands(ee.Image(-1).rename('hand_ft').float())

    if optical_water is not None:
        combined = combined.addBands(optical_water.float().unmask(0))
    else:
        combined = combined.addBands(ee.Image(0).rename('optical_water').float())
    if optical_valid is not None:
        combined = combined.addBands(optical_valid.float().unmask(0))
    else:
        combined = combined.addBands(ee.Image(0).rename('optical_valid').float())

    # ── Dual-polarization (VH) cross-check — same change-detection recipe,
    #    independent channel. Its own Otsu with a VH-appropriate range guard.
    if pre_vh is not None and post_vh is not None:
        vh_raw = ee.Number(otsu_threshold_gee(post_vh, bbox_coords))
        vh_in_range = vh_raw.gte(SAR_VH['water_db_min']).And(
            vh_raw.lte(SAR_VH['water_db_max']))
        vh_thr = ee.Number(ee.Algorithms.If(
            vh_in_range, vh_raw, SAR_VH['otsu_fallback_db']))
        vh_flood = (post_vh.lt(vh_thr).And(slope_mask)
                    .And(pre_vh.lt(vh_thr).Not())
                    .And(permanent_water.Not())
                    .rename('vh_flood'))
        combined = (combined.addBands(vh_flood.float().unmask(0))
                    .addBands(ee.Image(1).rename('vh_valid').float()))
    else:
        combined = (combined.addBands(ee.Image(0).rename('vh_flood').float())
                    .addBands(ee.Image(0).rename('vh_valid').float()))

    # ── Event-total rainfall (context metric, CHIRPS mm)
    if rain is not None:
        combined = combined.addBands(rain.float().unmask(0))
    else:
        combined = combined.addBands(ee.Image(0).rename('rain_mm').float())

    # ── Vegetation loss (NDVI pre minus post; positive = loss)
    if ndvi_pre is not None and ndvi_post is not None:
        ndvi_delta = ndvi_pre.subtract(ndvi_post).rename('ndvi_delta')
        combined = (combined.addBands(ndvi_delta.float().unmask(0))
                    .addBands(ee.Image(1).rename('ndvi_valid').float()))
    else:
        combined = (combined.addBands(ee.Image(0).rename('ndvi_delta').float())
                    .addBands(ee.Image(0).rename('ndvi_valid').float()))

    # ── Proximity to permanent water (JRC occurrence ≥ 50% within ~300m).
    #    Context for subrogation candidates and river-adjacency false positives.
    occurrence = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
                  .select('occurrence').unmask(0).gte(50))
    near_water = (occurrence.focal_max(radius=300, kernelType='circle', units='meters')
                  .rename('near_water'))
    combined = combined.addBands(near_water.float().unmask(0))

    # ── Inundation-duration slices: flood mask per post-window slice, sharing
    #    the main threshold + pre-event baseline so slices are comparable.
    for i, slice_img in enumerate(post_slices or []):
        name = f'flood_s{i}'
        if slice_img is not None:
            s_flood = (slice_img.lt(threshold).And(slope_mask)
                       .And(pre_water.Not())
                       .And(permanent_water.Not())
                       .rename(name))
            combined = combined.addBands(s_flood.float().unmask(0))
        else:
            # -1 marks "no scene in this slice" so sampling can distinguish
            # abstain from dry.
            combined = combined.addBands(ee.Image(-1).rename(name).float())

    return combined


DEFAULT_SAMPLE_RADIUS_M = 50
DEFAULT_SAMPLE_SCALE_M = 30


def _sample_geometry(row, default_radius_m):
    """
    The region sampled for one property.

    Structure-constrained when the caller has supplied Phase-2 columns:
      `sample_lat`/`sample_lon` — the matched structure's own location, rather
          than the geocoded address point, which on a large parcel can sit at
          the driveway entrance rather than the building.
      `sample_radius_m` — the structure's equal-area footprint radius.

    Falls back to the geocoded point and the fixed default radius otherwise, so
    portfolios with no structure match behave exactly as before.
    """
    lon = row.get('sample_lon')
    lat = row.get('sample_lat')
    if lon is None or lat is None or pd.isna(lon) or pd.isna(lat):
        lon, lat = row['longitude'], row['latitude']

    radius = row.get('sample_radius_m')
    try:
        radius = float(radius)
        if pd.isna(radius) or radius <= 0:
            radius = default_radius_m
    except (TypeError, ValueError):
        radius = default_radius_m

    return ee.Geometry.Point([float(lon), float(lat)]).buffer(float(radius))


def sample_properties(combined_image, properties_df, batch_size=100, throttle=True,
                      scale=DEFAULT_SAMPLE_SCALE_M,
                      default_radius_m=DEFAULT_SAMPLE_RADIUS_M):
    """
    Sample flood fraction, max depth, urban flag, optical cross-check, WSE
    spread, relative elevation and HAND at each property.
    Returns a DataFrame keyed by property_id.

    `throttle=False` skips the inter-batch sleeps — used for small live
    portfolios (a single batch) where the demo wants a fast turnaround.

    `scale` is the reduction scale in metres. The 30 m default is retained for
    the fixed-buffer path; footprint-constrained sampling should pass 10 m
    (Sentinel-1 GRD IW native pixel spacing), since averaging a ~9 m-radius
    structure over 30 m pixels would defeat the purpose of snapping to it.
    """
    combined_reducer = ee.Reducer.mean().combine(
        reducer2=ee.Reducer.max(), sharedInputs=True)

    all_results = []

    for batch_start in range(0, len(properties_df), batch_size):
        batch_df = properties_df.iloc[batch_start: batch_start + batch_size]
        features = []
        for _, row in batch_df.iterrows():
            features.append(ee.Feature(_sample_geometry(row, default_radius_m), {
                'property_id': str(row['property_id']),
                'address':     str(row.get('address', '')),
            }))

        try:
            sampled = combined_image.reduceRegions(
                collection=ee.FeatureCollection(features),
                reducer=combined_reducer,
                scale=scale
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
                    result = combined_image.reduceRegion(
                        reducer=combined_reducer,
                        geometry=_sample_geometry(row, default_radius_m),
                        scale=scale
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


def _hand_or_none(value):
    """HAND sample -> float feet, or None when MERIT Hydro has no value."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v < 0 else round(v, 2)


def _row_from_sample(property_id, address, p):
    """Normalize one reduceRegion(s) result dict into the standard row shape."""
    row = {
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
        # HAND: -1 (or missing) means MERIT Hydro has no value here — reported
        # as None so the ensemble abstains instead of reading it as "at the
        # drainage line", which is what a 0 would mean.
        'hand_ft':       _hand_or_none(p.get('hand_ft_mean')),
        # Round-7 bands — all default to "absent/abstain" when not sampled.
        'vh_available':  int(round(float(p.get('vh_valid_mean') or 0))),
        'vh_water_pct':  round(max(0.0, float(p.get('vh_flood_mean') or 0)), 4),
        'rain_mm':       round(max(0.0, float(p.get('rain_mm_mean') or 0)), 1),
        'ndvi_valid':    int(round(float(p.get('ndvi_valid_mean') or 0))),
        'ndvi_delta':    round(float(p.get('ndvi_delta_mean') or 0), 4),
        'near_water_flag': int(float(p.get('near_water_mean') or 0) >= 0.5),
    }
    # Duration slices: mean of -1 means "no scene"; report None to abstain.
    for i in range(DURATION['n_slices']):
        v = p.get(f'flood_s{i}_mean')
        if v is None:
            row[f'flood_s{i}'] = None
        else:
            v = float(v)
            row[f'flood_s{i}'] = None if v < 0 else round(max(0.0, v), 4)
    return row
