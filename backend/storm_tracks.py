"""
storm_tracks.py — NHC best-track data for the demo events (Round 8).

Simplified from the National Hurricane Center's public HURDAT2 best-track
records (public domain). Each point: time (UTC), lat, lon, max sustained wind
(kt), and Saffir-Simpson category at that fix. "Simplified" = 6-24h fixes
covering the segment relevant to the study area, not the storm's full life —
the overlay is labeled accordingly in the UI.

For arbitrary/live events there is no track here; the endpoint 404s and the
frontend simply doesn't draw an overlay (no fabrication).
"""


def _cat(wind_kt: int) -> str:
    if wind_kt >= 137: return '5'
    if wind_kt >= 113: return '4'
    if wind_kt >= 96:  return '3'
    if wind_kt >= 83:  return '2'
    if wind_kt >= 64:  return '1'
    if wind_kt >= 34:  return 'TS'
    return 'TD'


# (time_utc, lat, lon, wind_kt)
_HARVEY = [
    ('2017-08-25 00Z', 25.0, -95.6,  85),
    ('2017-08-25 12Z', 26.4, -96.2,  95),
    ('2017-08-26 03Z', 28.0, -96.8, 115),   # landfall, San Jose Island TX (Cat 4)
    ('2017-08-26 12Z', 28.7, -97.3,  60),
    ('2017-08-27 00Z', 28.9, -97.4,  40),   # stall — record Houston-area rainfall
    ('2017-08-28 00Z', 28.6, -96.8,  35),
    ('2017-08-29 00Z', 28.9, -95.4,  40),
    ('2017-08-30 04Z', 29.8, -93.9,  45),   # second landfall, near Cameron LA
    ('2017-08-31 00Z', 31.5, -93.0,  25),
]

_IAN = [
    ('2022-09-26 12Z', 21.6, -84.0, 110),
    ('2022-09-27 00Z', 22.6, -83.6, 110),
    ('2022-09-27 12Z', 24.2, -83.1, 105),
    ('2022-09-28 00Z', 25.6, -82.8, 120),
    ('2022-09-28 12Z', 26.3, -82.5, 135),
    ('2022-09-28 19Z', 26.7, -82.2, 130),   # landfall, Cayo Costa FL (Cat 4)
    ('2022-09-29 00Z', 27.1, -81.8,  75),   # inland over Punta Gorda / Arcadia
    ('2022-09-29 12Z', 28.4, -81.0,  55),
    ('2022-09-30 00Z', 29.2, -80.3,  65),
]

TRACKS = {
    'harvey': {'name': 'Hurricane Harvey (2017)', 'points': _HARVEY},
    'ian':    {'name': 'Hurricane Ian (2022)',    'points': _IAN},
}


def storm_track_geojson(event_id: str) -> dict | None:
    """GeoJSON FeatureCollection: track line + per-fix points, or None."""
    track = TRACKS.get(event_id)
    if track is None:
        return None

    coords = [[lon, lat] for _, lat, lon, _ in track['points']]
    features = [{
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': coords},
        'properties': {'kind': 'track', 'name': track['name']},
    }]
    for time_utc, lat, lon, wind in track['points']:
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {
                'kind':     'fix',
                'time':     time_utc,
                'wind_kt':  wind,
                'category': _cat(wind),
            },
        })
    return {
        'type': 'FeatureCollection',
        'features': features,
        'source': 'NHC HURDAT2 best track (simplified)',
    }
