# 02_get_properties.py — Fetch real property addresses from OpenStreetMap
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import pandas as pd
import random
import time
from config import HARVEY, IAN, OUTPUT_DIR

random.seed(42)


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
    First tries OSM building addresses, augments with street addresses if needed.
    Returns a clean pandas DataFrame.
    """
    event_id  = event_config['event_id']
    bbox      = event_config['bbox']
    event_name = event_config['event_name']

    print(f"\nBuilding property list for {event_name}...")
    print(f"  Study area: {event_config['study_name']}")

    # Query OSM for addressed buildings
    props = query_overpass_addresses(bbox, limit=target * 3)
    print(f"  Got {len(props)} addressed buildings from OSM")

    # Augment if we don't have enough
    if len(props) < target:
        extra = augment_from_street_network(bbox, needed=target - len(props))
        props.extend(extra)
        print(f"  Augmented to {len(props)} total properties")

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
    return df


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Ian only (Harvey already done)
    ian_df = build_property_list(IAN, target=1000)
    ian_path = os.path.join(OUTPUT_DIR, 'ian_properties.csv')
    ian_df.to_csv(ian_path, index=False)
    print(f"\n✓ Ian properties saved → {ian_path}")
    print(ian_df.head(3).to_string(index=False))

    print("\n✓ Day 1 Step 6 complete. Check your outputs/ folder.")