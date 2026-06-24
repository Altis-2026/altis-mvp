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

def generate_synthetic_thumbnail(property_id: str, depth_ft: float,
                                  is_post: bool, view: str = 'sar') -> str:
    """
    Generate a synthetic thumbnail using PIL. Returns a base64 PNG data URL.
    Fallback when real GEE imagery isn't cached.

    Two distinct, physically-motivated renderings:
    - view='sar'     : Sentinel-1 radar. Speckled grayscale; standing water is
                       specular and reads NEAR-BLACK. Flooding => dark pools.
    - view='optical' : Sentinel-2 MNDWI-style. Vegetated land reads green;
                       water has high MNDWI and reads BRIGHT CYAN. Flooding =>
                       bright pools. (Deliberately the inverse of SAR so the
                       two sensors are visibly different, as they are in reality.)
    """
    try:
        from PIL import Image
    except ImportError:
        return ""

    optical = (view == 'optical')
    seed = int(hashlib.md5(
        f"{property_id}{'post' if is_post else 'pre'}{view}".encode()
    ).hexdigest()[:8], 16) % 100_000
    rng  = np.random.RandomState(seed)
    H, W = 200, 300

    # Base texture: SAR is heavy speckle; optical is smoother.
    if optical:
        base = rng.normal(0.55, 0.10, size=(H, W)).astype(np.float32)
    else:
        base = rng.exponential(scale=0.32, size=(H, W)).astype(np.float32)

    # Building-like bright returns (both sensors see structures as brighter)
    for _ in range(rng.randint(5, 12)):
        bx = rng.randint(5, W - 20)
        by = rng.randint(5, H - 18)
        bw = rng.randint(8, 22)
        bh = rng.randint(6, 14)
        base[by:by+bh, bx:bx+bw] += rng.uniform(0.35, 1.0)

    # Road-like linear return
    ry = rng.randint(H // 3, 2 * H // 3)
    base[ry:ry+2, :] *= rng.uniform(0.25, 0.40)

    # Flood mask on post-event image — same geometry for both sensors, so a
    # demo viewer can see the *same* flood read oppositely by the two sensors.
    flood_mask = np.zeros((H, W), dtype=bool)
    if is_post and depth_ft > 0.2:
        inten = min(depth_ft / 7.0, 1.0)
        Y, X  = np.ogrid[:H, :W]
        cx    = rng.randint(W // 4, 3 * W // 4)
        cy    = rng.randint(H // 2, H - 15)
        rx    = int(38 + W * 0.17 * inten)
        ry2   = int(28 + H * 0.17 * inten)
        flood_mask |= ((X - cx)**2 / rx**2 + (Y - cy)**2 / ry2**2) <= 1.0
        if depth_ft > 1.5:
            cx2, cy2 = rng.randint(15, W - 25), rng.randint(H // 3, H - 15)
            r2 = int(18 + 22 * inten)
            flood_mask |= (X - cx2)**2 + (Y - cy2)**2 <= r2**2

    if not optical:
        # SAR: water is dark
        base[flood_mask] = rng.uniform(0.01, 0.06, flood_mask.sum())

    # Normalize
    if optical:
        arr = np.clip(base / (np.percentile(base, 99) + 1e-8), 0, 1)
    else:
        arr = np.clip(base / (np.percentile(base, 97) + 1e-8), 0, 1)

    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    if optical:
        # Natural-ish: land leans green, then paint water bright cyan on top.
        rgb[:, :, 0] = (arr * 120).astype(np.uint8)
        rgb[:, :, 1] = (arr * 150).astype(np.uint8)
        rgb[:, :, 2] = (arr * 110).astype(np.uint8)
        if flood_mask.any():
            rgb[flood_mask, 0] = 40
            rgb[flood_mask, 1] = (140 + arr[flood_mask] * 60).astype(np.uint8)
            rgb[flood_mask, 2] = (200 + arr[flood_mask] * 55).astype(np.uint8)
    else:
        # Altis blue-gray SAR palette
        rgb[:, :, 0] = (arr * 155).astype(np.uint8)
        rgb[:, :, 1] = (arr * 178).astype(np.uint8)
        rgb[:, :, 2] = (arr * 205).astype(np.uint8)

    img    = Image.fromarray(rgb)
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
