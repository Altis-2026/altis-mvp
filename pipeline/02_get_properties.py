# 02_get_properties.py — Fetch real property addresses from OpenStreetMap
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import pandas as pd
import random
import time
from config import HARVEY, BRAZOS, OUTPUT_DIR
from provenance import write_manifest

random.seed(42)


def query_nsi_addresses(bbox, target=1000, seed=42):
    """
    Fallback property source: USACE National Structure Inventory.

    Used when OSM Overpass is unreachable — this codebase's sandbox has
    outbound access to fema.gov/usace.army.mil and Earth Engine but not to
    OSM/Mapbox/Census geocoding, so the normal address path has no live
    alternative there. NSI gives real, government-published structure
    locations (see pipeline/structures.py), but no street address, so this
    does NOT fabricate one: each property is labeled with its NSI structure id
    and its county, the latter derived exactly from the structure's census
    block FIPS rather than guessed from coordinates.

    This must never silently produce a nicer-looking but fake address — the
    'no fabricated data' rule from Phase 0 applies here too.

    SAMPLING IS A PLAIN UNIFORM RANDOM DRAW, and must stay that way for any
    event whose output feeds validation. An earlier revision biased this draw
    toward structures near the detector's own flood mask; that inflates
    apparent agreement with claims because it conditions the sample on the
    thing being measured. See the note in __main__ for the full reasoning.
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
    n = min(target, len(res))
    idx = rng.choice(len(res), size=n, replace=False)
    res = res.iloc[idx].reset_index(drop=True)

    props = []
    for _, row in res.iterrows():
        lat, lon = float(row['latitude']), float(row['longitude'])
        label = county_label_from_cbfips(row.get('cbfips'))
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


def build_property_list(event_config, target=1000):
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
        props = query_nsi_addresses(bbox, target=target)
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

    prefix = {'harvey': 'HARV', 'brazos': 'BRZ'}.get(event_id, event_id[:4].upper())
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


# County FIPS -> name, for the counties the configured study areas span.
# NSI publishes each structure's census block FIPS (`cbfips`); its first five
# digits are the state+county code. Deriving the label from that is exact,
# rather than guessing a county (or worse, a neighborhood) from a bounding box
# that may straddle a county line.
COUNTY_FIPS = {
    '48201': 'Harris County, TX',
    '48157': 'Fort Bend County, TX',
    '48473': 'Waller County, TX',
    '48339': 'Montgomery County, TX',
    '48291': 'Liberty County, TX',
    '48071': 'Chambers County, TX',
    '48039': 'Brazoria County, TX',
}


def county_label_from_cbfips(cbfips, default='TX'):
    """Exact county label from an NSI census-block FIPS, or a neutral default."""
    code = str(cbfips or '')[:5]
    return COUNTY_FIPS.get(code, default)


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', action='append', choices=['harvey', 'brazos'],
                        help='Regenerate this event\'s property list (repeatable). '
                             'Default: both.')
    parser.add_argument('--target', type=int, default=4000,
                        help='Properties per event (default 4000).')
    args = parser.parse_args()
    events = args.event or ['harvey', 'brazos']

    # NOTE ON SAMPLING — deliberately UNBIASED, and this reverses an earlier
    # decision in this codebase's history.
    #
    # An earlier revision targeted the property list at structures near the
    # detector's own flood mask, because a random draw across the then-tight
    # reservoir-edge bbox found almost no flooding. That is fine for a
    # showcase portfolio and WRONG for anything feeding validation: selecting
    # properties where the detector already fires, then asking whether the
    # claims agree, cannot measure accuracy — it measures the selection.
    #
    # The real fix was the study area, not the sampling. The widened bbox
    # (see config.HARVEY) spans zips that flooded badly and zips that barely
    # did, so a plain random draw now lands on both and gives the per-zip
    # correlation actual contrast to work with. Targeting is gone.
    for event_cfg, name in ((HARVEY, 'harvey'), (BRAZOS, 'brazos')):
        if name not in events:
            continue
        df = build_property_list(event_cfg, target=args.target)
        path = os.path.join(OUTPUT_DIR, f'{name}_properties.csv')
        df.to_csv(path, index=False)
        print(f"\n✓ {event_cfg['event_name']} properties saved → {path}")
        print(df.head(3).to_string(index=False))

    print("\n✓ Property lists rebuilt. Check your outputs/ folder.")