"""
gee_service.py — Google Earth Engine integration.

Generates:
- Flood overlay tile URLs (raster source for Mapbox)
- SAR thumbnail URLs per property (before/after)

GEE is optional — if not authenticated, returns None gracefully.
The demo works without GEE; real tiles and thumbnails require earthengine authenticate.
"""
import os
import hashlib
import base64
import io
import numpy as np
from pathlib import Path
from typing import Optional

BASE_DIR  = Path(__file__).parent.parent
CACHE_DIR = BASE_DIR / 'cache' / 'sar'


# ── Flood tile URL generation ─────────────────────────────────────────────────

def get_flood_tile_url(event_id: str) -> Optional[str]:
    """
    Generate a Mapbox-compatible GEE tile URL for the flood extent raster.
    Returns None if GEE is not authenticated or initialization fails.
    """
    try:
        import ee
        from pipeline.config import GEE_PROJECT, HARVEY, IAN

        ee.Initialize(project=GEE_PROJECT)

        event_map = {'harvey': HARVEY, 'ian': IAN}
        cfg = event_map.get(event_id)
        if not cfg:
            return None

        bbox = ee.Geometry.Rectangle(cfg['bbox'])

        # Load pre + post SAR composites
        def load_sar(start, end):
            return (ee.ImageCollection("COPERNICUS/S1_GRD")
                      .filterBounds(bbox)
                      .filterDate(start, end)
                      .filter(ee.Filter.eq('instrumentMode', 'IW'))
                      .select('VV')
                      .median())

        pre  = load_sar(cfg['pre_start'],  cfg['pre_end'])
        post = load_sar(cfg['post_start'], cfg['post_end'])

        # Simple threshold flood mask for visualization
        threshold    = -15.0
        permanent    = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select('seasonality').gte(8)
        flood_visual = (post.lt(threshold)
                           .And(pre.lt(threshold).Not())
                           .And(permanent.Not())
                           .updateMask(post.lt(threshold).And(pre.lt(threshold).Not())))

        # Get tile URL
        map_id   = flood_visual.getMapId({'palette': ['00B4D8', '0077B6', '023E8A']})
        tile_url = map_id['tile_fetcher'].url_format
        return tile_url

    except Exception as e:
        print(f"GEE tile URL unavailable: {e}")
        return None


# ── SAR thumbnail generation ──────────────────────────────────────────────────

def _value_noise(rng: np.random.RandomState, H: int, W: int, octaves=(8, 16, 32, 64)) -> np.ndarray:
    """Cheap multi-octave value noise (no external deps): generate coarse random
    grids and upsample with bicubic interpolation at increasing frequency, summed
    with decreasing amplitude. Produces smooth, natural-looking terrain texture
    instead of flat per-pixel static."""
    from PIL import Image as _Image
    out = np.zeros((H, W), dtype=np.float32)
    amp_total = 0.0
    for i, n in enumerate(octaves):
        amp = 1.0 / (2 ** i)
        grid = rng.rand(max(2, n // 4), max(2, n // 4)).astype(np.float32)
        layer = np.asarray(
            _Image.fromarray((grid * 255).astype(np.uint8)).resize((W, H), _Image.BICUBIC),
            dtype=np.float32,
        ) / 255.0
        out += layer * amp
        amp_total += amp
    return out / amp_total


def generate_synthetic_thumbnail(property_id: str, depth_ft: float,
                                  is_post: bool, view: str = 'sar') -> str:
    """
    Generate a synthetic thumbnail using PIL. Returns a base64 PNG data URL.
    Fallback when real GEE imagery isn't cached.

    Two distinct, physically-motivated renderings, built from smooth multi-octave
    terrain noise (not flat per-pixel static) plus a parcel layout — a street
    grid, building footprints with drop shadows, and a tree-line/yard texture —
    so the scene reads as an aerial tile rather than abstract noise:
    - view='sar'     : Sentinel-1 radar. Speckled grayscale; standing water is
                       specular and reads NEAR-BLACK. Flooding => dark pools.
    - view='optical' : Sentinel-2 true-color/MNDWI-style. Vegetated land reads
                       green, roads/roofs read warm gray; water has high MNDWI
                       and reads BRIGHT CYAN. Flooding => bright pools.
                       (Deliberately the inverse of SAR so the two sensors are
                       visibly different, as they are in reality.)
    """
    try:
        from PIL import Image, ImageFilter, ImageDraw
    except ImportError:
        return ""

    optical = (view == 'optical')
    seed = int(hashlib.md5(
        f"{property_id}{'post' if is_post else 'pre'}{view}".encode()
    ).hexdigest()[:8], 16) % 100_000
    rng  = np.random.RandomState(seed)
    H, W = 200, 300

    # Smooth terrain base (ground/vegetation albedo variation), then speckle
    # is layered on top only for SAR — optical stays smooth like a real
    # pan-sharpened composite.
    terrain = _value_noise(rng, H, W)

    # Parcel layout drawn once, shared geometry between pre/post and sensors
    # (seeded without 'post'/'view' so the neighborhood layout doesn't change
    # between frames — only the flood state does).
    layout_seed = int(hashlib.md5(f"{property_id}-layout".encode()).hexdigest()[:8], 16) % 100_000
    lrng = np.random.RandomState(layout_seed)

    buildings = []  # (x, y, w, h)
    n_buildings = lrng.randint(6, 11)
    for _ in range(n_buildings):
        bw, bh = lrng.randint(16, 34), lrng.randint(14, 26)
        bx, by = lrng.randint(8, W - bw - 8), lrng.randint(8, H - bh - 8)
        buildings.append((bx, by, bw, bh))

    road_y = lrng.randint(int(H * 0.40), int(H * 0.62))
    road_x = lrng.randint(int(W * 0.55), int(W * 0.80))

    if optical:
        base = (terrain * 0.5 + rng.normal(0.5, 0.04, size=(H, W))).astype(np.float32)
    else:
        # SAR speckle multiplies the terrain signal (true radar speckle is
        # multiplicative gamma noise, not additive).
        speckle = rng.gamma(shape=4.0, scale=0.25, size=(H, W)).astype(np.float32)
        base = (terrain * 0.6 + 0.4) * speckle

    rgb_extra = np.zeros((H, W, 3), dtype=np.float32)  # building/road tint, optical-only

    # Roads — light gray asphalt strip, slight width variance
    base[road_y - 3:road_y + 3, :] *= 0.55
    base[:, road_x - 3:road_x + 3] *= 0.60
    if optical:
        rgb_extra[road_y - 3:road_y + 3, :, :] += 8
        rgb_extra[:, road_x - 3:road_x + 3, :] += 8

    # Buildings — bright radar return / warm rooftop color, with a soft
    # drop-shadow on the SW side for a pseudo-3D aerial look.
    for (bx, by, bw, bh) in buildings:
        sx0, sy0 = min(bx + 3, W - 1), min(by + 3, H - 1)
        sx1, sy1 = min(bx + bw + 5, W), min(by + bh + 5, H)
        base[sy0:sy1, sx0:sx1] *= 0.65
        base[by:by+bh, bx:bx+bw] = base[by:by+bh, bx:bx+bw] * 0.3 + rng.uniform(0.7, 1.3)
        if optical:
            rgb_extra[by:by+bh, bx:bx+bw, 0] += 40
            rgb_extra[by:by+bh, bx:bx+bw, 1] += 28
            rgb_extra[by:by+bh, bx:bx+bw, 2] += 22

    # Flood mask on post-event image — irregular, noise-carved water edge
    # rather than a clean ellipse, clipped near the parcel's low ground.
    flood_mask = np.zeros((H, W), dtype=bool)
    if is_post and depth_ft > 0.2:
        inten   = min(depth_ft / 7.0, 1.0)
        Y, X    = np.ogrid[:H, :W]
        cx      = lrng.randint(W // 4, 3 * W // 4)
        cy      = lrng.randint(H // 2, H - 15)
        rx      = int(40 + W * 0.18 * inten)
        ry2     = int(30 + H * 0.18 * inten)
        ellipse = ((X - cx)**2 / rx**2 + (Y - cy)**2 / ry2**2) <= 1.0
        edge_noise = _value_noise(np.random.RandomState(seed + 7), H, W, octaves=(16, 32, 64))
        flood_mask |= ellipse & (edge_noise > 0.32)
        if depth_ft > 1.5:
            cx2, cy2 = lrng.randint(15, W - 25), lrng.randint(H // 3, H - 15)
            r2 = int(20 + 24 * inten)
            pool2 = (X - cx2)**2 + (Y - cy2)**2 <= r2**2
            flood_mask |= pool2 & (edge_noise > 0.30)
        # Streets/yards flood first, then erode raised building footprints —
        # a flooded scene still shows roofs poking above shallow water.
        for (bx, by, bw, bh) in buildings:
            if depth_ft < 3.0:
                flood_mask[by:by+bh, bx:bx+bw] = False

    if not optical:
        base[flood_mask] = rng.uniform(0.02, 0.08, flood_mask.sum())

    arr_img = Image.fromarray((np.clip(base / (np.percentile(base, 98) + 1e-8), 0, 1) * 255).astype(np.uint8))
    if optical:
        arr_img = arr_img.filter(ImageFilter.GaussianBlur(radius=0.6))
    arr = np.asarray(arr_img, dtype=np.float32) / 255.0

    rgb = np.zeros((H, W, 3), dtype=np.float32)
    if optical:
        # Natural aerial palette: green vegetation, warm-gray hardscape.
        rgb[:, :, 0] = arr * 95  + rgb_extra[:, :, 0]
        rgb[:, :, 1] = arr * 138 + rgb_extra[:, :, 1]
        rgb[:, :, 2] = arr * 80  + rgb_extra[:, :, 2]
        if flood_mask.any():
            shade = arr[flood_mask]
            rgb[flood_mask, 0] = 35 + shade * 20
            rgb[flood_mask, 1] = 130 + shade * 70
            rgb[flood_mask, 2] = 195 + shade * 55
    else:
        # Altis blue-gray SAR palette (dark = low backscatter = water/shadow)
        rgb[:, :, 0] = arr * 150
        rgb[:, :, 1] = arr * 172
        rgb[:, :, 2] = arr * 198

    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    img = Image.fromarray(rgb)
    if not optical:
        img = img.filter(ImageFilter.GaussianBlur(radius=0.4))  # softens speckle just enough to avoid pure pixel noise

    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def get_sar_thumbnails(property_id: str, depth_ft: float, view: str = 'sar') -> dict:
    """
    Return pre/post thumbnail data URLs for a property and sensor view
    ('sar' or 'optical'). Checks GEE cache first, falls back to synthetic.
    """
    from backend.database import get_cached_thumbnail

    # Only real SAR tiles are cached; optical always uses the synthetic path.
    pre_url = post_url = None
    if view == 'sar':
        pre_url  = get_cached_thumbnail(property_id, is_post=False)
        post_url = get_cached_thumbnail(property_id, is_post=True)

    if not pre_url:
        pre_url  = generate_synthetic_thumbnail(property_id, depth_ft, is_post=False, view=view)
    if not post_url:
        post_url = generate_synthetic_thumbnail(property_id, depth_ft, is_post=True, view=view)

    return {
        'property_id': property_id,
        'view':        view,
        'pre_url':     pre_url,
        'post_url':    post_url,
        'is_real_sar': False,   # Will be True when GEE thumbnails are cached
    }


def precache_gee_thumbnails(event_id: str, sample_size: int = 100):
    """
    Pre-generate real GEE SAR thumbnails for a sample of properties.
    Run this script once with GEE authenticated to populate the cache.
    """
    try:
        import ee
        from pipeline.config import GEE_PROJECT, HARVEY, IAN
        from backend.database import load_event_data, save_thumbnail_cache

        ee.Initialize(project=GEE_PROJECT)

        event_map = {'harvey': HARVEY, 'ian': IAN}
        cfg = event_map.get(event_id)
        df  = load_event_data(event_id)
        if df is None or cfg is None:
            print(f"No data for event {event_id}")
            return

        # Sample properties with the most notable depth spread
        sample = df.nlargest(sample_size // 2, 'max_depth_ft')
        sample = pd.concat([sample, df.sample(min(sample_size // 2, len(df)))])
        sample = sample.drop_duplicates('property_id').head(sample_size)

        def load_sar(start, end):
            return (ee.ImageCollection("COPERNICUS/S1_GRD")
                      .filterDate(start, end)
                      .filter(ee.Filter.eq('instrumentMode', 'IW'))
                      .select('VV')
                      .median())

        pre_img  = load_sar(cfg['pre_start'],  cfg['pre_end'])
        post_img = load_sar(cfg['post_start'], cfg['post_end'])

        VIZ = {'min': -25, 'max': 0,
               'palette': ['000000', '2A3A4A', '4A6A8A', 'A8D4E6', 'FFFFFF']}

        for _, row in sample.iterrows():
            try:
                pid  = row['property_id']
                pt   = ee.Geometry.Point([row['longitude'], row['latitude']])
                bbox = pt.buffer(250).bounds()

                for is_post, img in [(False, pre_img), (True, post_img)]:
                    url = img.getThumbURL({
                        'region':     bbox,
                        'dimensions': '300x200',
                        'format':     'png',
                        **VIZ
                    })
                    import requests, base64
                    resp = requests.get(url, timeout=30)
                    if resp.status_code == 200:
                        b64 = base64.b64encode(resp.content).decode()
                        data_url = f"data:image/png;base64,{b64}"
                        save_thumbnail_cache(pid, is_post, data_url)

                print(f"  Cached thumbnails for {pid}")
            except Exception as e:
                print(f"  Failed {row['property_id']}: {e}")

        print(f"✓ Cached {len(sample)} property thumbnail pairs for {event_id}")

    except Exception as e:
        print(f"GEE thumbnail caching failed: {e}")
