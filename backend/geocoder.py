"""
geocoder.py — Global address geocoding.

Primary:  Mapbox v6 forward geocoder — worldwide coverage, so uploaded
          portfolios resolve anywhere (Colombia, Spain, Japan, …). Uses the
          same token the frontend map already uses.
Fallback: US Census TIGER geocoder — free, no key, US-only. Used when no
          Mapbox token is configured, or for a US address Mapbox missed.

The public surface (`geocode_batch`, `geocode_addresses_sync`) is unchanged, so
every caller (the portfolio-confirm endpoint) keeps working as-is — only the
coverage area went from "US only" to "the whole planet".
"""
import asyncio
import aiohttp
from typing import Optional

try:
    from pipeline.config import MAPBOX_TOKEN
except Exception:  # pragma: no cover - config import guard
    import os
    MAPBOX_TOKEN = os.getenv('MAPBOX_TOKEN') or os.getenv('VITE_MAPBOX_TOKEN')

MAPBOX_URL = "https://api.mapbox.com/search/geocode/v6/forward"
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


async def _geocode_mapbox(session: aiohttp.ClientSession, address: str) -> Optional[dict]:
    """Geocode one address via Mapbox (global). Returns {lat, lon, matched_address} or None."""
    if not MAPBOX_TOKEN:
        return None
    try:
        async with session.get(MAPBOX_URL, params={
            "q":            address,
            "limit":        "1",
            "access_token": MAPBOX_TOKEN,
        }, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            features = data.get('features') or []
            if not features:
                return None
            feat = features[0]
            lon, lat = feat['geometry']['coordinates']  # GeoJSON is [lon, lat]
            props = feat.get('properties', {})
            matched = props.get('full_address') or props.get('name') or address
            return {'lat': float(lat), 'lon': float(lon), 'matched_address': matched}
    except Exception:
        return None


async def _geocode_census(session: aiohttp.ClientSession, address: str) -> Optional[dict]:
    """Geocode one US address via Census TIGER. Returns {lat, lon, matched_address} or None."""
    try:
        async with session.get(CENSUS_URL, params={
            "address":   address,
            "benchmark": "Public_AR_Current",
            "format":    "json",
        }, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            matches = data.get('result', {}).get('addressMatches', [])
            if not matches:
                return None
            coords = matches[0]['coordinates']
            return {
                'lat':             float(coords['y']),
                'lon':             float(coords['x']),
                'matched_address': matches[0].get('matchedAddress', address),
            }
    except Exception:
        return None


async def geocode_single(session: aiohttp.ClientSession, address: str) -> Optional[dict]:
    """
    Geocode one address. Mapbox first (global); if it's configured but returns
    nothing, try Census as a US fallback. If no Mapbox token at all, Census only.
    """
    if MAPBOX_TOKEN:
        result = await _geocode_mapbox(session, address)
        if result:
            return result
    return await _geocode_census(session, address)


async def geocode_batch(addresses: list[str],
                        concurrency: int = 10) -> list[Optional[dict]]:
    """
    Geocode a list of addresses concurrently.
    Returns results in same order as input.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_geocode(session, addr):
        async with semaphore:
            return await geocode_single(session, addr)

    async with aiohttp.ClientSession(headers={
        'User-Agent': 'Altis-Flood-Intelligence/1.0'
    }) as session:
        tasks   = [bounded_geocode(session, addr) for addr in addresses]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    return [r if isinstance(r, (dict, type(None))) else None for r in results]


def geocode_addresses_sync(addresses: list[str]) -> list[Optional[dict]]:
    """Synchronous wrapper for use outside async context."""
    return asyncio.run(geocode_batch(addresses))
