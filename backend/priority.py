"""
priority.py — Severity × coverage ranking for the dispatch queue.

The dispatch queue is what an adjuster actually works: of all the properties a
storm flagged for a site visit, which should the CAT team hit first? "First" is
not "deepest water" — a 2ft flood in a $4M commercial building outranks a 4ft
flood in a $90k rental. So priority blends physical severity (how bad the flood
looks) with financial exposure (how much is on the line).

Pure functions only — no pandas/DB/network — so the same formula powers the API
endpoint, the PDF report, and the unit tests, and the JS frontend mirror
(utils/priority.js) can be checked against it.
"""
import math
from typing import Optional

# Depth (ft) at which physical severity saturates. Residential structures are
# effectively a total loss well before this, so 6ft = max severity.
SEVERITY_DEPTH_FT_CAP = 6.0
# Weighting of depth vs. flooded-area within the severity term.
DEPTH_WEIGHT = 0.6
AREA_WEIGHT = 0.4


def severity(depth_ft: float, pct_flooded: float) -> float:
    """
    Physical flood severity in [0, 1] from depth (ft) and flooded area
    (pct_flooded is a 0–100 percentage, matching the pipeline output).
    """
    depth_norm = min(max(depth_ft or 0.0, 0.0) / SEVERITY_DEPTH_FT_CAP, 1.0)
    area_norm = min(max(pct_flooded or 0.0, 0.0) / 100.0, 1.0)
    return DEPTH_WEIGHT * depth_norm + AREA_WEIGHT * area_norm


def exposure_multiplier(coverage_amount: Optional[float]) -> float:
    """
    Financial-exposure multiplier in roughly [1.0, ~2.2]. Log-damped so a single
    very large policy lifts priority without dwarfing every other property —
    ranking should still respond to severity, not collapse to "sort by TIV".
    A missing/zero coverage amount (e.g. raw event data with no policy attached)
    yields a neutral 1.0, so the queue degrades gracefully to severity-only.
    """
    if not coverage_amount or coverage_amount <= 0:
        return 1.0
    # log10($1M) ≈ 6 → +1.0; log10($100k) ≈ 5 → +0.83; log10($10k) ≈ 4 → +0.67
    return 1.0 + math.log10(1.0 + coverage_amount) / 6.0


def priority_score(depth_ft: float, pct_flooded: float,
                   coverage_amount: Optional[float] = None) -> float:
    """
    Combined dispatch priority on a 0–100+ scale (severity × exposure × 100).
    Higher = work first. Deterministic and monotonic in each input.
    """
    return round(severity(depth_ft, pct_flooded) * exposure_multiplier(coverage_amount) * 100, 1)


def rank_dispatch(properties: list, classes=('Dispatch', 'Review')) -> list:
    """
    Given property dicts (each with impact_class, max_depth_ft, pct_flooded, and
    optionally coverage_amount), return the subset in `classes` sorted by
    priority_score descending, each annotated with `priority_score` and a 1-based
    `priority_rank`. Input is not mutated.
    """
    queue = []
    for p in properties:
        if p.get('impact_class') not in classes:
            continue
        score = priority_score(
            _num(p.get('max_depth_ft')),
            _num(p.get('pct_flooded')),
            _num(p.get('coverage_amount')),
        )
        queue.append({**p, 'priority_score': score})

    queue.sort(key=lambda p: p['priority_score'], reverse=True)
    for i, p in enumerate(queue, start=1):
        p['priority_rank'] = i
    return queue


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
