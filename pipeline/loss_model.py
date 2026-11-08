"""
loss_model.py — claims-calibrated depth -> insured loss.

THE MODEL
  For each (coverage, depth basis, structure segment, depth bin) it stores the
  empirical DISTRIBUTION (40 quantile points) of damage as a fraction of the
  building's actual cash value, learned from real settled NFIP claims
  (validation/fit_loss_model.py, training storms only). To estimate a loss at
  a depth it interpolates that distribution, scales each point by the
  property's value, pushes every point through policy_terms.net_payment, and
  averages. Doing it per point matters: a deductible and a limit are
  nonlinear, so the expected payment is NOT the payment at the expected damage.

  Output: expected net payment (mid), the same at depth -/+ the depth error
  (low/high: measurement uncertainty), and the 10th/90th percentile of a
  single claim at that depth (claim-to-claim spread, a different thing).

WHAT THE INPUT DEPTH MEANS
  depth_basis 'above_first_floor' uses the INTERIOR water depth records
  (inches inside the building); 'above_ground' uses EXTERIOR depth records
  (inches against the building relative to lowest adjacent grade). FEMA's
  generic `waterDepth` field mixes feet and inches and is not used.

WHAT THIS IS NOT
  It is conditional on a claim being filed. It does not model take-up (which
  policies exist), claim propensity, fraud, ICC, or supplemental payments.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

try:
    from policy_terms import PolicyTerms, net_payment_array, REPLACEMENT_UPLIFT_THETA
except ImportError:  # pragma: no cover
    from pipeline.policy_terms import PolicyTerms, net_payment_array, REPLACEMENT_UPLIFT_THETA

MODEL_PATH = Path(__file__).resolve().parent.parent / "outputs" / "loss_model.json"
BIN_EDGES_IN = [0, 1, 3, 6, 12, 18, 24, 36, 48, 72, 120, 10 ** 6]      # inches
NQ = 40
MIN_CELL = 150
SEGMENTS = ("res1_nb", "res1_b", "other")


def segment_for(occupancy_type=None, basement=None) -> str:
    """Structure segment from NFIP claim fields OR USACE NSI attributes.

    NFIP occupancyType 1/11 = single family; NSI occtype 'RES1-...'.
    `basement` truthy = finished/unfinished basement.
    """
    occ = str(occupancy_type or "").strip().upper()
    if occ.endswith(".0"):
        occ = occ[:-2]
    sfh = occ in ("1", "11") or occ.startswith("RES1")
    if not sfh:
        return "other"
    return "res1_b" if basement else "res1_nb"


def depth_bin(inches: float) -> int:
    return int(np.clip(np.searchsorted(BIN_EDGES_IN, inches, side="right") - 1, 0, len(BIN_EDGES_IN) - 2))


def bin_centres_in(table_bins: list) -> list:
    return [b["centre_in"] for b in table_bins]


# ── Fitting (used by validation/fit_loss_model.py, training data only) ───────

def fit_distributions(ratio: np.ndarray, depth_in: np.ndarray, seg: np.ndarray) -> dict:
    """Per-segment depth-bin quantile tables with back-off to all segments."""
    qs = (np.arange(NQ) + 0.5) / NQ
    out = {}
    nb = len(BIN_EDGES_IN) - 1
    bins = np.array([depth_bin(x) for x in depth_in])

    def cell(mask):
        r = ratio[mask]
        return {"n": int(mask.sum()), "q": [round(float(v), 4) for v in np.quantile(r, qs)],
                "mean": round(float(r.mean()), 4)} if mask.sum() else None

    for s in SEGMENTS + ("all",):
        sm = np.ones(len(ratio), bool) if s == "all" else (seg == s)
        rows = []
        for b in range(nb):
            m = sm & (bins == b)
            c = cell(m)
            lo, hi = BIN_EDGES_IN[b], min(BIN_EDGES_IN[b + 1], 240)
            centre = float(np.median(depth_in[m])) if m.sum() else float(np.sqrt(max(lo, 0.5) * hi))
            rows.append({"bin_in": [lo, BIN_EDGES_IN[b + 1]], "centre_in": round(centre, 2), "cell": c})
        out[s] = rows
    return out


# ── Runtime ──────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_model(path: str = str(MODEL_PATH)) -> Optional[dict]:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else None


def _quantile_vector(tables: dict, segment: str, inches: float) -> tuple[np.ndarray, int, str]:
    """Interpolated 40-point damage-ratio distribution at a depth (inches).

    Cells with fewer than MIN_CELL claims back off to the all-segment cell.
    Interpolates linearly between adjacent bin centres; clamps at the ends.
    Returns (quantiles, support_n, source_segment).
    """
    def usable(seg_key):
        rows = tables[seg_key]
        return [(r["centre_in"], np.array(r["cell"]["q"]), r["cell"]["n"])
                for r in rows if r["cell"] and r["cell"]["n"] >= MIN_CELL]

    pts, src = usable(segment), segment
    if len(pts) < 3:
        pts, src = usable("all"), "all"
    xs = np.array([p[0] for p in pts]); qs = np.stack([p[1] for p in pts]); ns = np.array([p[2] for p in pts])
    if inches <= xs[0]:
        return qs[0], int(ns[0]), src
    if inches >= xs[-1]:
        return qs[-1], int(ns[-1]), src
    i = int(np.searchsorted(xs, inches) - 1)
    w = (inches - xs[i]) / (xs[i + 1] - xs[i])
    return (1 - w) * qs[i] + w * qs[i + 1], int(min(ns[i], ns[i + 1])), src


def _net_distribution(tables, segment, depth_ft, acv_value, terms: PolicyTerms, theta):
    q, n, src = _quantile_vector(tables, segment, max(0.0, depth_ft) * 12.0)
    net = net_payment_array(q * acv_value, terms, theta)
    return net, q, n, src


def expected_loss(depth_ft: float, depth_ci_ft: float, terms: PolicyTerms, *,
                  coverage: str = "building", depth_basis: str = "above_first_floor",
                  segment: str = "other", model: Optional[dict] = None) -> Optional[dict]:
    """Expected net payment for one coverage, with depth-error range and claim spread.

    Returns None when no calibrated model is available or the terms carry no
    limit (nothing to scale against).
    """
    m = model if model is not None else load_model()
    if not m or not terms.limit or terms.limit <= 0:
        return None
    key = f"{coverage}_{'interior' if depth_basis == 'above_first_floor' else 'exterior'}"
    tables = m["tables"].get(key)
    if not tables:
        return None
    t = terms.resolved(assumed_deductible=(1250))
    acv = t.acv_value
    theta = m.get("theta", REPLACEMENT_UPLIFT_THETA)
    ci = max(0.0, float(depth_ci_ft or 0.0))
    mid, q, n, src = _net_distribution(tables, segment, depth_ft, acv, t, theta)
    lo, *_ = _net_distribution(tables, segment, max(0.0, depth_ft - ci), acv, t, theta)
    hi, *_ = _net_distribution(tables, segment, depth_ft + ci, acv, t, theta)
    return {
        "mid": round(float(mid.mean())), "low": round(float(lo.mean())), "high": round(float(hi.mean())),
        "claim_p10": round(float(np.quantile(mid, 0.10))), "claim_p50": round(float(np.quantile(mid, 0.50))),
        "claim_p90": round(float(np.quantile(mid, 0.90))),
        "expected_damage_pct": round(float(q.mean()) * 100, 1),
        "support_claims": n, "segment_used": src, "depth_basis": depth_basis,
        "terms_provenance": t.provenance,
        "model": {"trained_on": m.get("trained_on"), "n_train_claims": m.get("n_train", {}).get(key)},
    }
