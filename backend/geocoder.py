"""
geocoder.py — Census Bureau TIGER geocoder.
Free, no API key required, handles US addresses accurately.
"""
import asyncio
import aiohttp
from typing import Optional


CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


async def geocode_single(session: aiohttp.ClientSession, address: str) -> Optional[dict]:
    """Geocode one address. Returns {lat, lon, matched_address} or None."""
    try:
        async with session.get(CENSUS_URL, params={
            "address":   address,
            "benchmark": "Public_AR_Current",
            "format":    "json"
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
