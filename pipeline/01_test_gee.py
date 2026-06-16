# 01_test_gee.py — Verify GEE connection before running the full pipeline
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ee
from config import GEE_PROJECT

print("=" * 50)
print("Testing Google Earth Engine connection...")
print("=" * 50)

try:
    ee.Initialize(project=GEE_PROJECT)
    print(f"✓ GEE initialized with project: {GEE_PROJECT}")
except Exception as e:
    print(f"✗ Failed to initialize GEE: {e}")
    print("\nFix: Make sure you ran 'earthengine authenticate' in the terminal")
    print("     and that your GEE_PROJECT in config.py is correct.")
    sys.exit(1)

# Test 1: Can we access Sentinel-1 over Houston?
print("\nTest 1: Accessing Sentinel-1 data over Houston...")
houston_box = ee.Geometry.Rectangle([-95.60, 29.62, -95.38, 29.80])

try:
    collection = (ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(houston_box)
        .filterDate('2017-08-01', '2017-09-10')
        .filter(ee.Filter.eq('instrumentMode', 'IW'))
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')))

    count = collection.size().getInfo()
    print(f"✓ Found {count} Sentinel-1 images over Houston (Harvey period)")

    if count == 0:
        print("✗ No images found. This is unexpected — check your GEE account approval.")
        sys.exit(1)

    # Print dates of available images
    dates = collection.aggregate_array('system:time_start').getInfo()
    import datetime
    print("  Available image dates:")
    for d in sorted(dates):
        dt = datetime.datetime.utcfromtimestamp(d / 1000)
        print(f"    {dt.strftime('%Y-%m-%d')}")

except Exception as e:
    print(f"✗ Sentinel-1 query failed: {e}")
    sys.exit(1)

# Test 2: Can we access SRTM DEM?
print("\nTest 2: Accessing SRTM Digital Elevation Model...")
try:
    dem = ee.Image("USGS/SRTMGL1_003").select('elevation')
    sample = dem.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=houston_box,
        scale=1000
    ).getInfo()
    print(f"✓ SRTM DEM accessible. Mean elevation in test area: {sample.get('elevation', 'N/A'):.1f}m")
except Exception as e:
    print(f"✗ SRTM DEM access failed: {e}")
    sys.exit(1)

# Test 3: Can we access JRC Global Surface Water?
print("\nTest 3: Accessing JRC Global Surface Water...")
try:
    jrc = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select('seasonality')
    print("✓ JRC Global Surface Water accessible")
except Exception as e:
    print(f"✗ JRC access failed: {e}")
    sys.exit(1)

print("\n" + "=" * 50)
print("✓ All tests passed. GEE is fully operational.")
print("=" * 50)