#!/usr/bin/env python3
"""
structures.py — Per-structure attributes from the USACE National Structure
Inventory (NSI), and the depth-above-first-floor calculation they enable.

THE PROBLEM THIS SOLVES
-----------------------
The detector measures depth above GROUND. Every depth-damage curve ever
published takes depth above FIRST FLOOR. The two differ by the foundation
height, so using one where the other belongs is not noise — it is a systematic,
signed bias:

  - Homes on piers or piles (coastal Florida, much of the Gulf) are scored as
    damaged when the water never reached the living space. False positives,
    and they cluster exactly where surge events put the most properties in
    play at once.
  - Slab-on-grade homes have almost no offset, so they are scored roughly
    right — which is why the error hides so well in aggregate.

On a sample of Meyerland/Houston structures the modelled foundation heights
are 0.25-0.75 ft for slab, 2 ft for crawlspace, and 5.25 ft for pier. A 5 ft
error on a residential flood depth is the difference between "total loss" and
"no interior claim".

THE OBJECTION THIS ANSWERS
--------------------------
"You need to know home elevation, and that data set doesn't exist."

That conflates two things. Elevation CERTIFICATES are genuinely sparse — they
exist where a community has collected them. First-floor HEIGHT, modelled
nationally, does exist: NSI publishes foundation height, foundation type,
number of stories, occupancy class, and structure/contents value for every
structure in CONUS, free, from a public API.

HONEST LIMITS — these travel with every number this module produces
-------------------------------------------------------------------
  1. NSI foundation heights are MODELLED and survey-informed, not surveyed.
     For a single named property an elevation certificate is better data. For
     scoring 10,000 policies inside 24 hours of landfall, a national modelled
     layer is the only thing that exists, and it is far better than assuming
     every structure sits at grade — which is what we did before.
  2. NSI is CONUS-only. Outside it, `found_ht` is unavailable and the
     depth-above-first-floor value is None rather than a guess.
  3. Matching is nearest-neighbour from the property's geocoded point to an
     NSI structure point. On dense multi-structure parcels the nearest
     structure may not be the insured one, so the match distance is recorded
     and callers can reject weak matches.
"""
import math
from typing import Optional

import pandas as pd
import requests

NSI_API = "https://nsi.sec.usace.army.mil/nsiapi/structures"

# NSI foundation type codes.
FOUNDATION_LABELS = {
    'S': 'Slab',
    'C': 'Crawlspace',
    'B': 'Basement',
    'P': 'Pier',
    'I': 'Pile',
    'F': 'Fill',
    'W': 'Solid wall',
}

# Fields we keep from an NSI feature. `found_ht` is the one that matters most;
# the rest are exposure and (for Phase 3) damage-curve selection inputs.
NSI_FIELDS = [
    'fd_id', 'occtype', 'st_damcat', 'found_ht', 'found_type', 'num_story',
    'sqft', 'ftprntsqft', 'val_struct', 'val_cont', 'ground_elv',
    'med_yr_blt', 'firmzone', 'bldheight', 'usastrucid',
    # Census block FIPS. Its first 5 digits are the state+county code, which
    # is the only exact way to label a structure's county — a bounding box can
    # straddle a county line, so inferring it from coordinates would be a
    # guess.
    'cbfips',
]

# A match further than this from the geocoded point is not trustworthy as
# "this property's structure".
DEFAULT_MAX_MATCH_M = 60.0


def fetch_nsi_structures(bbox_coords, timeout: int = 240,
                         verbose: bool = True) -> pd.DataFrame:
    """
    Fetch every NSI structure inside a bounding box.

    `bbox_coords` is [west, south, east, north], matching the convention used
    throughout the pipeline. The NSI API's GET `bbox` parameter does not
    accept a two-corner box, so we POST the box as a GeoJSON polygon, which is
    the form that actually works.

    Returns an empty DataFrame outside CONUS — the caller treats that as
    "first-floor height unknown", never as zero.
    """
    west, south, east, north = bbox_coords
    body = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[west, south], [east, south], [east, north],
                                 [west, north], [west, south]]],
            },
        }],
    }
    try:
        resp = requests.post(NSI_API, json=body, timeout=timeout)
        resp.raise_for_status()
        feats = resp.json().get('features', [])
    except Exception as e:  # noqa: BLE001 - reported, never silently zeroed
        if verbose:
            print(f"  NSI unavailable ({e}); first-floor heights will be unknown.")
        return pd.DataFrame()

    if not feats:
        if verbose:
            print("  NSI returned no structures for this bbox (outside CONUS?).")
        return pd.DataFrame()

    rows = []
    for f in feats:
        p = f.get('properties', {})
        coords = (f.get('geometry') or {}).get('coordinates') or [None, None]
        row = {k: p.get(k) for k in NSI_FIELDS}
        row['longitude'] = p.get('x', coords[0])
        row['latitude'] = p.get('y', coords[1])
        rows.append(row)

    df = pd.DataFrame(rows).dropna(subset=['latitude', 'longitude'])
    if verbose:
        print(f"  NSI: {len(df):,} structures in bbox")
    return df.reset_index(drop=True)


def match_properties_to_structures(properties: pd.DataFrame, nsi: pd.DataFrame,
                                   max_match_m: float = DEFAULT_MAX_MATCH_M
                                   ) -> pd.DataFrame:
    """
    Attach the nearest NSI structure to each property.

    Returns one row per input property with the NSI attributes plus
    `nsi_match_m` (distance to the matched structure) and `nsi_matched`
    (whether it cleared `max_match_m`). Unmatched properties keep their row
    with null attributes, so downstream joins never silently drop properties.
    """
    import numpy as np

    out_cols = NSI_FIELDS + ['nsi_match_m', 'nsi_matched',
                             'nsi_lat', 'nsi_lon']
    if properties.empty:
        return pd.DataFrame(columns=['property_id'] + out_cols)

    base = pd.DataFrame({'property_id': properties['property_id'].astype(str)})
    for c in out_cols:
        base[c] = None
    base['nsi_matched'] = False

    if nsi.empty:
        return base

    prop_lat = pd.to_numeric(properties['latitude'], errors='coerce').to_numpy()
    prop_lon = pd.to_numeric(properties['longitude'], errors='coerce').to_numpy()
    nsi_lat = pd.to_numeric(nsi['latitude'], errors='coerce').to_numpy()
    nsi_lon = pd.to_numeric(nsi['longitude'], errors='coerce').to_numpy()

    # Equirectangular projection onto a local tangent plane. Over the tens of
    # metres that decide a match it is accurate to well under a metre.
    lat0 = float(np.nanmean(prop_lat))
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))

    prop_xy = np.column_stack([prop_lon * m_per_deg_lon,
                               prop_lat * m_per_deg_lat])
    nsi_xy = np.column_stack([nsi_lon * m_per_deg_lon,
                              nsi_lat * m_per_deg_lat])

    # Nearest neighbour via a KD-tree, NOT a full pairwise distance matrix.
    # The naive (n_properties x n_structures) matrix is fine at demo scale and
    # explodes past it: a 4,000-property portfolio against the 594,767 NSI
    # structures in the widened Harvey bbox needs 17.7 GiB and dies. The tree
    # is O(n log m) in time and linear in memory, and returns identical
    # nearest-neighbour results.
    try:
        from scipy.spatial import cKDTree
        dist_m, nearest = cKDTree(nsi_xy).query(prop_xy, k=1)
    except ImportError:  # pragma: no cover - scipy is in requirements.txt
        # Chunked brute force: same answer, bounded memory, no new dependency.
        nearest = np.empty(len(prop_xy), dtype=int)
        dist_m = np.empty(len(prop_xy), dtype=float)
        chunk = max(1, int(2e7 // max(len(nsi_xy), 1)))
        for start in range(0, len(prop_xy), chunk):
            block = prop_xy[start:start + chunk]
            d2 = ((block[:, None, 0] - nsi_xy[None, :, 0]) ** 2 +
                  (block[:, None, 1] - nsi_xy[None, :, 1]) ** 2)
            idx = np.nanargmin(d2, axis=1)
            nearest[start:start + len(block)] = idx
            dist_m[start:start + len(block)] = np.sqrt(
                d2[np.arange(len(block)), idx])

    matched = nsi.iloc[nearest].reset_index(drop=True)
    for c in NSI_FIELDS:
        base[c] = matched[c].values
    base['nsi_lat'] = matched['latitude'].values
    base['nsi_lon'] = matched['longitude'].values
    base['nsi_match_m'] = dist_m.round(1)
    base['nsi_matched'] = dist_m <= max_match_m

    # A match that failed the distance test carries no usable attributes.
    for c in NSI_FIELDS:
        base.loc[~base['nsi_matched'], c] = None

    return base


def first_floor_height_ft(found_ht) -> Optional[float]:
    """NSI foundation height in feet, or None when unknown."""
    try:
        v = float(found_ht)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or v < 0) else v


def depth_above_first_floor(depth_above_ground_ft, found_ht) -> Optional[float]:
    """
    Convert depth above ground to depth above the first floor.

    Deliberately computed as a RELATIVE subtraction — our own DEM-derived depth
    above ground minus NSI's foundation height above grade. Both are heights
    relative to local grade, so no vertical datum has to be reconciled between
    the DEM and NSI's `ground_elv`. Mixing those absolute elevations instead
    would import a datum mismatch larger than the effect being measured.

    Returns None when the foundation height is unknown, so callers report
    "first-floor depth unavailable" rather than silently falling back to
    depth above ground.
    """
    ffh = first_floor_height_ft(found_ht)
    if ffh is None:
        return None
    try:
        d = float(depth_above_ground_ft)
    except (TypeError, ValueError):
        return None
    if math.isnan(d):
        return None
    return round(max(0.0, d - ffh), 2)


def footprint_radius_m(ftprnt_sqft, floor_m: float = 5.0,
                       ceiling_m: float = 30.0) -> float:
    """
    Radius of the equal-area circle for a structure's footprint, in metres.

    WHY A PROXY: the real move is to sample the actual USA Structures footprint
    polygon. Those polygons are served from an Esri-hosted endpoint that is not
    reachable from this environment, and no US footprint collection is
    available in our Earth Engine catalog either. NSI does publish each
    structure's footprint AREA (`ftprntsqft`, 100% populated in the areas
    tested), so an equal-area circle centred on the structure point is the
    honest approximation available today.

    It is still a large improvement on what it replaces. The pipeline's fixed
    50 m buffer covers ~7,850 m²; the median structure footprint in the Harvey
    study area is ~2,570 sqft, an 8.7 m equal-area radius covering ~239 m². The
    old buffer therefore averaged the target structure together with roughly 33
    times its own area of street, yard, and neighbouring parcels.

    Bounded below because sampling a sub-pixel region is pointless, and above
    so a bad area value can't reintroduce the problem being fixed.
    """
    try:
        sqft = float(ftprnt_sqft)
    except (TypeError, ValueError):
        return floor_m
    if math.isnan(sqft) or sqft <= 0:
        return floor_m
    radius = math.sqrt((sqft * 0.09290304) / math.pi)
    return float(min(max(radius, floor_m), ceiling_m))


def foundation_label(found_type) -> Optional[str]:
    """Human-readable foundation type for the adjuster-facing drawer."""
    if not found_type:
        return None
    return FOUNDATION_LABELS.get(str(found_type).strip().upper(),
                                 str(found_type))
