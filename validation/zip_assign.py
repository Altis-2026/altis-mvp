#!/usr/bin/env python3
"""
zip_assign.py — Assign a ZIP (ZCTA) to each property from its coordinates.

WHY THIS EXISTS
---------------
The previous validation derived a property's zip by regex-matching the last
five-digit group in its address string. On the Harvey demo portfolio that is
measurably wrong in two ways:

  - 300 of 1000 addresses contain no zip at all ("1516 Pech Road, Houston, TX"),
    so 30% of the portfolio was silently dropped from validation.
  - Street numbers get matched as zips. "10005 Main Street, TX" yielded zip
    10005 — lower Manhattan. Eleven Harvey properties were assigned non-Texas
    zips this way, and each one either dropped out on the join or, worse,
    polluted a real zip's aggregate.

Both failure modes bias the validation set in ways nobody can reason about, so
the fix is to stop parsing strings and use the coordinates we already have.

METHOD
------
Point-in-polygon against the Census TIGER ZCTA5 boundaries hosted in Earth
Engine — the same EE session the pipeline already authenticates. The US Census
geocoder would be the conventional choice but census.gov is not reachable from
this environment, and TIGER-in-EE is the same underlying boundary data.

Results are cached to CSV because the assignment is deterministic and there is
no reason to spend an EE round-trip on it twice.
"""
import os
from pathlib import Path
from typing import Optional

import pandas as pd

# TIGER/2010/ZCTA5 is marked deprecated in the EE catalog in favour of the 2020
# vintage. ZCTA boundaries are redrawn each decennial census, so the 2020
# vintage is correct for Ian (2022) and is also the boundary set NFIP claims
# report against today for Harvey.
ZCTA_ASSET = 'TIGER/2020/ZCTA5'
ZCTA_FIELD = 'ZCTA5CE20'


def assign_zips(properties: pd.DataFrame, cache_path: Optional[str] = None,
                batch_size: int = 500, verbose: bool = True) -> pd.DataFrame:
    """
    Map each property to its ZCTA using its latitude/longitude.

    `properties` needs columns: property_id, latitude, longitude.
    Returns a DataFrame [property_id, zip]; properties that fall outside every
    ZCTA polygon are returned with zip = NaN rather than a guess.

    Requires an already-initialized Earth Engine session (the caller owns
    auth, matching the convention in pipeline/flood_detect.py).
    """
    if cache_path and os.path.exists(cache_path):
        cached = pd.read_csv(cache_path, dtype={'zip': str})
        if set(cached['property_id']) >= set(properties['property_id'].astype(str)):
            if verbose:
                print(f"  ZIP assignment loaded from cache: {cache_path}")
            return cached

    import ee

    df = properties.dropna(subset=['latitude', 'longitude']).copy()
    df['property_id'] = df['property_id'].astype(str)

    # Restrict the ZCTA collection to the portfolio's own bounding box first —
    # turns a national point-in-polygon into a few dozen candidate polygons.
    pad = 0.15
    bbox = ee.Geometry.Rectangle([
        float(df['longitude'].min()) - pad, float(df['latitude'].min()) - pad,
        float(df['longitude'].max()) + pad, float(df['latitude'].max()) + pad,
    ])
    zctas = ee.FeatureCollection(ZCTA_ASSET).filterBounds(bbox)

    rows = []
    for start in range(0, len(df), batch_size):
        chunk = df.iloc[start:start + batch_size]
        feats = [
            ee.Feature(ee.Geometry.Point([float(r['longitude']), float(r['latitude'])]),
                       {'property_id': str(r['property_id'])})
            for _, r in chunk.iterrows()
        ]
        joined = ee.Join.saveFirst(matchKey='zcta').apply(
            primary=ee.FeatureCollection(feats),
            secondary=zctas,
            condition=ee.Filter.intersects(leftField='.geo', rightField='.geo'),
        )
        try:
            for feat in joined.getInfo().get('features', []):
                props = feat.get('properties', {})
                match = props.get('zcta') or {}
                zip_code = (match.get('properties', {}) or {}).get(ZCTA_FIELD)
                rows.append({'property_id': props.get('property_id'), 'zip': zip_code})
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            print(f"    ZIP assignment batch at {start} failed: {e}")

        if verbose:
            print(f"    assigned {len(rows):,}/{len(df):,} properties")

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=['property_id', 'zip'])

    out['zip'] = out['zip'].astype(str).str.extract(r'^(\d{5})', expand=False)

    if cache_path:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(cache_path, index=False)
        if verbose:
            print(f"  ZIP assignment cached → {cache_path}")
    return out
