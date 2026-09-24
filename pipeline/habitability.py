"""
habitability.py — How long will this home be uninhabitable? (planning estimate)

ICEYE's insurer workflow #3: decide early whether a household needs a hotel
for a week or a long-let for six months. The drivers are how deep the water
got above the finished floor and how long it stood there — exactly what the
structure-aware depth and the routed hydrograph provide.

    days_out = days_water_above_floor + drying + repair(band)

Bands (water above finished floor, peak):
  ≤ 0 ft      : 0            — water stayed below the floor
  0 – 0.5 ft  : 14–30 days   — floor coverings, base of walls, drying
  0.5 – 2 ft  : 45–90 days   — flood cut of drywall, flooring, lower cabinets
  2 – 4 ft    : 90–180 days  — most interior finishes, appliances, HVAC
  > 4 ft      : 180–365 days — gut rehabilitation

Drying: 3–7 days of structural drying once water is out (typical industry
practice, IICRC S500-style water damage restoration). The bands are an Altis
PLANNING heuristic for reserving and accommodation decisions — every output
is labelled as such, carries its inputs, and is not a contractor schedule.
"""
from __future__ import annotations

BANDS = [  # (upper bound ft above floor, low days, high days, scope)
    (0.0, 0, 0, 'water below the finished floor'),
    (0.5, 14, 30, 'floor coverings, base of walls, drying'),
    (2.0, 45, 90, 'flood cut of drywall, flooring, lower cabinets'),
    (4.0, 90, 180, 'most interior finishes, appliances, HVAC'),
    (float('inf'), 180, 365, 'gut rehabilitation'),
]
DRYING_DAYS = (3, 7)


def estimate(peak_above_floor_ft: float | None, hours_above_floor: float | None = None) -> dict | None:
    if peak_above_floor_ft is None:
        return None
    d = float(peak_above_floor_ft)
    for upper, lo, hi, scope in BANDS:
        if d <= upper:
            break
    if hi == 0:
        return {'days_low': 0, 'days_high': 0, 'scope': scope, 'displacement': False,
                'inputs': {'peak_above_floor_ft': round(d, 2), 'hours_above_floor': hours_above_floor},
                'basis': 'Altis planning heuristic'}
    standing = (hours_above_floor or 0.0) / 24.0
    low = round(standing + DRYING_DAYS[0] + lo)
    high = round(standing + DRYING_DAYS[1] + hi)
    return {
        'days_low': low, 'days_high': high, 'scope': scope, 'displacement': True,
        'accommodation': 'short-stay (hotel)' if high <= 45 else 'long-let / ALE rental',
        'inputs': {'peak_above_floor_ft': round(d, 2),
                   'hours_above_floor': None if hours_above_floor is None else round(hours_above_floor, 1),
                   'drying_days': list(DRYING_DAYS)},
        'basis': 'Altis planning heuristic (depth band + standing time + drying) — not a contractor schedule',
    }
