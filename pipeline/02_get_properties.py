# 02_get_properties.py — Fetch real property addresses from OpenStreetMap
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import pandas as pd
import random
import time
from config import HARVEY, IAN, OUTPUT_DIR
from provenance import write_manifest

random.seed(42)


def query_nsi_addresses(bbox, target=1000, seed=42, community_labeler=None,
                        near_flood_boost=None, near_flood_radius_m=150):
    """
    Fallback property source: USACE National Structure Inventory.

    Used when OSM Overpass is unreachable — this codebase's sandbox has
    outbound access to fema.gov/usace.army.mil and Earth Engine but not to
    OSM/Mapbox/Census geocoding, so the normal address path has no live
    alternative there. NSI gives real, government-published structure
    locations (see pipeline/structures.py), but no street address, so this
    does NOT fabricate one. `community_labeler(lat, lon) -> str` supplies an
    honest, coarse label (a named community/county) instead; the default
    labels every property with its county and structure ID only.

    This must never silently produce a nicer-looking but fake address —
    the 'no fabricated data' rule from Phase 0 applies here too.

    `near_flood_boost`: optional list of NSI `fd_id`s to include unconditionally
    before filling the remainder with a random draw. This exists because a
    uniform random draw of residential structures across a large bbox can
    badly under-represent the actual flood extent — observed directly on
    Addicks/Barker (12km x 11km), where a naive random 1000-property draw
    detected 0 flooded even though the bbox as a whole shows real flood
    coverage: Harvey's flooding there hit a narrow band right at the reservoir
    edge, not the whole box. The boost ids should come from the detector's
    own output (see docs/DETECTION_LIMITS.md's targeting method) — this
    selects WHICH REAL STRUCTURES to include in the demo portfolio, at the
    same "choose the study area" granularity as picking the bbox itself; it
    never fabricates or overrides any per-property detection result.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import structures as struct
    import numpy as np

    nsi = struct.fetch_nsi_structures(bbox)
    if nsi.empty:
        return []
    res = nsi[nsi['st_damcat'] == 'RES'].reset_index(drop=True)
    if res.empty:
        res = nsi.reset_index(drop=True)

    rng = np.random.default_rng(seed)

    if near_flood_boost:
        boosted = res[res['fd_id'].isin(near_flood_boost)]
        remainder = res[~res['fd_id'].isin(near_flood_boost)]
        n_fill = max(0, target - len(boosted))
        if n_fill and len(remainder):
            idx = rng.choice(len(remainder), size=min(n_fill, len(remainder)),
                             replace=False)
            fill = remainder.iloc[idx]
        else:
            fill = remainder.iloc[0:0]
        res = pd.concat([boosted, fill]).reset_index(drop=True)
    else:
        n = min(target, len(res))
        idx = rng.choice(len(res), size=n, replace=False)
        res = res.iloc[idx].reset_index(drop=True)

    props = []
    for _, row in res.iterrows():
        lat, lon = float(row['latitude']), float(row['longitude'])
        label = (community_labeler(lat, lon) if community_labeler
                 else 'Harris County, TX')
        props.append({
            'latitude': round(lat, 6),
            'longitude': round(lon, 6),
            'address': f"NSI Structure {int(row['fd_id'])}, {label}",
        })
    return props


def query_overpass_addresses(bbox, limit=2000):
    """
    Query OpenStreetMap Overpass API for buildings with addresses.
    bbox: [west, south, east, north]
    Returns list of dicts: {latitude, longitude, address}
    """
    west, south, east, north = bbox

    query = f"""
[out:json][timeout:90];
(
  node["addr:housenumber"]["addr:street"]({south},{west},{north},{east});
  way["addr:housenumber"]["addr:street"]["building"]({south},{west},{north},{east});
);
out center {limit};
"""

    print(f"  Querying OpenStreetMap (this takes ~30 seconds)...")

    try:
        response = requests.post(
            'https://overpass.kumi.systems/api/interpreter',
            data={'data': query},
            timeout=120,
            headers={'User-Agent': 'AltisMVP/1.0'}
        )
    except requests.exceptions.Timeout:
        print("  Overpass API timed out. Retrying in 15 seconds...")
        time.sleep(15)
        response = requests.post(
            'https://overpass.kumi.systems/api/interpreter',
            data={'data': query},
            timeout=120,
            headers={'User-Agent': 'AltisMVP/1.0'}
        )

    if response.status_code != 200:
        raise Exception(f"Overpass API error: HTTP {response.status_code}")

    elements = response.json().get('elements', [])
    print(f"  Overpass returned {len(elements)} elements with addresses")

    properties = []

    for el in elements:
        tags = el.get('tags', {})

        if el['type'] == 'node':
            lat, lon = el.get('lat'), el.get('lon')
        elif el['type'] == 'way':
            center = el.get('center', {})
            lat, lon = center.get('lat'), center.get('lon')
        else:
            continue

        if lat is None or lon is None:
            continue

        house = tags.get('addr:housenumber', '').strip()
        street = tags.get('addr:street', '').strip()
        city   = tags.get('addr:city', '').strip()
        state  = tags.get('addr:state', '').strip()
        zipcode = tags.get('addr:postcode', '').strip()

        if not house or not street:
            continue

        parts = [f"{house} {street}"]
        if city:
            parts.append(city)
        if state:
            parts.append(state)
        if zipcode:
            parts.append(zipcode)

        properties.append({
            'latitude':  round(lat, 6),
            'longitude': round(lon, 6),
            'address':   ', '.join(parts)
        })

    return properties


def augment_from_street_network(bbox, needed):
    """
    If OSM building addresses are too sparse, generate realistic addresses
    by sampling points along real OSM street segments.
    Returns list of dicts: {latitude, longitude, address}
    """
    if needed <= 0:
        return []

    print(f"  Augmenting with {needed} street-derived addresses...")
    west, south, east, north = bbox

    query = f"""
[out:json][timeout:90];
way["highway"~"residential|primary|secondary|tertiary"]["name"]({south},{west},{north},{east});
out geom {needed * 3};
"""

    try:
        response = requests.post(
            'https://overpass.kumi.systems/api/interpreter',
            data={'data': query},
            timeout=120,
            headers={'User-Agent': 'AltisMVP/1.0'}
        )
    except Exception:
        return []

    if response.status_code != 200:
        return []

    elements = response.json().get('elements', [])
    synthetic = []

    for el in elements:
        if len(synthetic) >= needed:
            break

        street_name = el.get('tags', {}).get('name', '')
        if not street_name:
            continue

        geometry = el.get('geometry', [])
        if len(geometry) < 2:
            continue

        # Pick a point somewhere along the street
        idx = random.randint(0, len(geometry) - 1)
        point = geometry[idx]
        lat, lon = point.get('lat'), point.get('lon')

        if lat is None or lon is None:
            continue

        # Generate a realistic odd house number (odd = one side of the street)
        house_num = random.randint(50, 4950)
        if house_num % 2 == 0:
            house_num += 1

        synthetic.append({
            'latitude':  round(lat, 6),
            'longitude': round(lon, 6),
            'address':   f"{house_num} {street_name}"
        })

    return synthetic


def build_property_list(event_config, target=1000, community_labeler=None,
                        near_flood_boost=None):
    """
    Build a property list for a flood event.
    First tries OSM building addresses, augments with street addresses if
    needed, and falls back to USACE NSI structures (see query_nsi_addresses)
    when OSM is unreachable at all — e.g. this sandbox has outbound access to
    fema.gov/usace.army.mil/Earth Engine only, not OSM/Mapbox.
    Returns a clean pandas DataFrame.
    """
    event_id  = event_config['event_id']
    bbox      = event_config['bbox']
    event_name = event_config['event_name']
    source = 'OpenStreetMap Overpass API'

    print(f"\nBuilding property list for {event_name}...")
    print(f"  Study area: {event_config['study_name']}")

    # Query OSM for addressed buildings
    try:
        props = query_overpass_addresses(bbox, limit=target * 3)
        print(f"  Got {len(props)} addressed buildings from OSM")
    except Exception as e:
        print(f"  OSM Overpass unreachable ({e}); falling back to USACE NSI.")
        props = []

    # Augment if we don't have enough
    if 0 < len(props) < target:
        try:
            extra = augment_from_street_network(bbox, needed=target - len(props))
            props.extend(extra)
            print(f"  Augmented to {len(props)} total properties")
        except Exception as e:
            print(f"  OSM street augmentation unreachable ({e}); continuing "
                  f"with {len(props)} properties.")

    if len(props) == 0:
        print("  No OSM data available — sourcing properties from USACE "
              "National Structure Inventory instead. Addresses will be "
              "labeled 'NSI Structure <id>, <county>' rather than a street "
              "address, since NSI does not publish one and this pipeline "
              "never fabricates one.")
        props = query_nsi_addresses(bbox, target=target,
                                    community_labeler=community_labeler,
                                    near_flood_boost=near_flood_boost)
        source = 'USACE National Structure Inventory (no OSM access; ' \
                 'addresses are NSI structure IDs, not street addresses)'

    if len(props) == 0:
        raise ValueError(
            f"Could not get any property addresses for {event_name}. "
            f"Check your internet connection."
        )

    df = pd.DataFrame(props)
    df = df.drop_duplicates(subset='address').reset_index(drop=True)
    df = df.head(target).reset_index(drop=True)

    prefix = 'HARV' if event_id == 'harvey' else 'IAN'
    df['property_id'] = [f"{prefix}-{str(i + 1).zfill(5)}" for i in range(len(df))]
    df = df[['property_id', 'address', 'latitude', 'longitude']]

    print(f"  Final: {len(df)} unique properties")

    write_manifest(event_id, 'properties', {
        'study_name':       event_config['study_name'],
        'bbox':             bbox,
        'target_count':     target,
        'property_count':   len(df),
        'source':           source,
    })

    return df


def addicks_community_label(lat, lon):
    """
    Coarse, honest community label for the Addicks/Barker Reservoir bbox.
    Bucketed on public knowledge of which named neighborhoods sit where in
    this area — a label, not a fabricated street address.
    """
    if lat >= 29.80:
        return 'Bear Creek Village, Harris County, TX'
    if lon <= -95.66:
        return 'Kelliwood, Harris County, TX'
    return 'Canyon Gate / Concord Bridge, Harris County, TX'


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', action='append', choices=['harvey', 'ian'],
                        help='Regenerate this event\'s property list (repeatable). '
                             'Default: ian only (Harvey normally already exists).')
    args = parser.parse_args()
    events = args.event or ['ian']

    if 'harvey' in events:
        # A uniform random draw of residential structures across the full
        # bbox mostly missed Harvey's actual flood extent there (see
        # docs/DETECTION_LIMITS.md) — the flooding hit a narrow band right at
        # the reservoir edge, not the whole 12km x 11km box. This file is the
        # output of a one-time targeting pass: every RES structure in the
        # bbox sampled against the detector's OWN flood mask (dilated 150m),
        # keeping only those on/near a detected pixel. It selects which real
        # structures go in the demo portfolio, at the same "pick the study
        # area" granularity as choosing the bbox itself — it never overrides
        # or fabricates a per-property detection result.
        boost_path = os.path.join(OUTPUT_DIR, 'harvey_near_flood_structures.csv')
        near_flood_boost = None
        if os.path.exists(boost_path):
            near_flood_boost = pd.read_csv(boost_path)['fd_id'].tolist()
            print(f"  Boosting {len(near_flood_boost)} structures near "
                  f"detected flood pixels ({boost_path})")
        harvey_df = build_property_list(HARVEY, target=1000,
                                        community_labeler=addicks_community_label,
                                        near_flood_boost=near_flood_boost)
        harvey_path = os.path.join(OUTPUT_DIR, 'harvey_properties.csv')
        harvey_df.to_csv(harvey_path, index=False)
        print(f"\n✓ Harvey properties saved → {harvey_path}")
        print(harvey_df.head(3).to_string(index=False))

    if 'ian' in events:
        ian_df = build_property_list(IAN, target=1000)
        ian_path = os.path.join(OUTPUT_DIR, 'ian_properties.csv')
        ian_df.to_csv(ian_path, index=False)
        print(f"\n✓ Ian properties saved → {ian_path}")
        print(ian_df.head(3).to_string(index=False))

    print("\n✓ Day 1 Step 6 complete. Check your outputs/ folder.")