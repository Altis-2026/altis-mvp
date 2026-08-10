"""
Tests for dual-polarisation water discrimination (Phase 4b).

These exercise the REAL functions in flood_detect, not a reimplementation of
them. Phase 4a's tests used a parallel reference implementation, which pinned
the physics but left the shipped image code untested; a scalar stand-in for
ee.Image closes that gap, because the arithmetic is elementwise anyway — what
`dualpol_water_score` does to one pixel is exactly what it does to an image.

The central claim under test is the one the design rests on: taking the MINIMUM
of the two channels' evidence means a non-corroborating channel CAPS the score.
That is what rejects saturated soil, and it is the single line that would
silently turn this back into Phase 4a if someone "simplified" it to a mean.
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "pipeline"))


class Px:
    """
    A one-pixel stand-in for ee.Image, implementing just the operations
    flood_detect's dual-pol path uses. Booleans are carried as 1.0/0.0 the way
    Earth Engine carries them.
    """

    def __init__(self, v):
        self.v = float(v)

    @staticmethod
    def _v(o):
        return o.v if isinstance(o, Px) else float(o)

    def subtract(self, o):  return Px(self.v - self._v(o))
    def divide(self, o):    return Px(self.v / self._v(o))
    def multiply(self, o):  return Px(self.v * self._v(o))
    def max(self, o):       return Px(max(self.v, self._v(o)))
    def min(self, o):       return Px(min(self.v, self._v(o)))
    def gte(self, o):       return Px(1.0 if self.v >= self._v(o) else 0.0)
    def lt(self, o):        return Px(1.0 if self.v < self._v(o) else 0.0)
    def Not(self):          return Px(0.0 if self.v else 1.0)
    def clamp(self, lo, hi): return Px(max(lo, min(hi, self.v)))
    def rename(self, _):    return self
    def float(self):        return self

    def where(self, cond, val):
        return Px(self._v(val)) if self._v(cond) else Px(self.v)


@pytest.fixture(autouse=True)
def _stub_ee(monkeypatch):
    """
    flood_detect imports `ee` at module scope. The dual-pol functions never
    touch it — everything they do goes through the image objects handed in —
    so a bare module object is enough to let the import succeed offline.
    """
    import types
    if 'ee' not in sys.modules:
        sys.modules['ee'] = types.ModuleType('ee')


def _score(vv_post, vh_post, vv_mean, vh_mean, std=1.0, **kw):
    """
    Convenience wrapper: run the real scorer on scalar dB values with a unit
    baseline sigma, so a "z-score of -3" is literally "3 dB below the mean".
    """
    import flood_detect as fd
    return fd.dualpol_water_score(
        Px(vv_post), Px(vh_post), Px(vv_mean), Px(std), Px(vh_mean), Px(std),
        **kw).v


# ── The discrimination that Phase 4a could not make ──────────────────────────

def test_standing_water_scores_high():
    """Both channels collapse: co-pol 4 sigma down, cross-pol 5 sigma down."""
    s = _score(vv_post=-14.0, vh_post=-25.0, vv_mean=-10.0, vh_mean=-20.0)
    assert s == pytest.approx(1.0)


def test_saturated_soil_is_rejected_despite_a_large_vv_drop():
    """
    The Phase 4a failure mode, encoded. VV falls 4 sigma — enough to score 1.0
    on its own, which is exactly what the sub-pixel unmixer did — while VH
    barely moves, because rough wet ground still depolarises.
    """
    s = _score(vv_post=-14.0, vh_post=-20.3, vv_mean=-10.0, vh_mean=-20.0)
    assert s == 0.0


def test_min_not_mean_is_what_rejects_it():
    """
    Pins the mechanism, not just the outcome, in the case where min() is the
    ONLY thing doing the rejecting.

    VH here drops 5 dB against VV's 4 dB, so the ratio gate is satisfied — but
    VH is a noisier channel (sigma 3 dB vs 1 dB), so that larger raw drop is
    LESS significant: 1.67 sigma against VV's 4. Evidence is 1.0 and ~0.22. A
    mean would score ~0.61 and sail through. The minimum scores 0.22.

    Swap min() for an average and this fails, which is the point: the average
    is precisely what made Phase 4a fail.
    """
    import flood_detect as fd
    from config import DUALPOL
    ev_vv = fd._channel_evidence(Px(-14.0), Px(-10.0), Px(1.0), DUALPOL).v
    ev_vh = fd._channel_evidence(Px(-25.0), Px(-20.0), Px(3.0), DUALPOL).v
    assert ev_vv == pytest.approx(1.0)
    assert 0.0 < ev_vh < 0.30
    combined = fd.dualpol_water_score(
        Px(-14.0), Px(-25.0), Px(-10.0), Px(1.0), Px(-20.0), Px(3.0)).v
    assert combined == pytest.approx(min(ev_vv, ev_vh))
    assert combined < (ev_vv + ev_vh) / 2.0


def test_ratio_gate_demands_vh_outdrop_vv():
    """
    The gate's actual content, stated as a test because it is easy to misread
    as "both channels fell".

    (VV-VH) rising is algebraically identical to VH falling MORE than VV, in
    dB. So a pixel where VV falls further than VH is rejected outright — which
    is the saturated-soil signature, and means the gate and min() overlap
    whenever the two channels share a sigma. They stop overlapping when the
    sigmas differ, which is the normal case and why both are kept.
    """
    import flood_detect as fd
    # VV falls 4 dB, VH falls only 1.5 dB -> ratio FALLS -> rejected.
    assert fd.dualpol_water_score(
        Px(-14.0), Px(-21.5), Px(-10.0), Px(1.0), Px(-20.0), Px(1.0)).v == 0.0
    # Same VV drop, VH now falls 5 dB -> ratio rises -> accepted.
    assert fd.dualpol_water_score(
        Px(-14.0), Px(-25.0), Px(-10.0), Px(1.0), Px(-20.0), Px(1.0)).v > 0.0


# ── Channel evidence ramp ────────────────────────────────────────────────────

def test_no_change_scores_zero():
    import flood_detect as fd
    from config import DUALPOL
    assert fd._channel_evidence(Px(-10.0), Px(-10.0), Px(1.0), DUALPOL).v == 0.0


def test_brightening_scores_zero_not_negative():
    """A pixel that got BRIGHTER is not weak evidence of water; it is none."""
    import flood_detect as fd
    from config import DUALPOL
    assert fd._channel_evidence(Px(-6.0), Px(-10.0), Px(1.0), DUALPOL).v == 0.0


def test_evidence_ramps_linearly_between_the_gates():
    import flood_detect as fd
    from config import DUALPOL
    lo, hi = DUALPOL['z_min'], DUALPOL['z_full']
    mid = (lo + hi) / 2.0
    got = fd._channel_evidence(Px(-10.0 - mid), Px(-10.0), Px(1.0), DUALPOL).v
    assert got == pytest.approx(0.5)


def test_tiny_baseline_sigma_cannot_manufacture_certainty():
    """
    A pixel that was unnaturally quiet across the baseline window would divide
    by ~0 and report infinite significance. The floor is what stops a 0.05 dB
    wobble reading as a 20 sigma flood.
    """
    import flood_detect as fd
    from config import DUALPOL, BASELINE
    got = fd._channel_evidence(Px(-10.05), Px(-10.0), Px(0.0001), DUALPOL).v
    assert got == pytest.approx(
        max(0.0, min(1.0, (0.05 / BASELINE['min_std_db'] - DUALPOL['z_min'])
                     / (DUALPOL['z_full'] - DUALPOL['z_min']))))


# ── The co/cross ratio gate ──────────────────────────────────────────────────

def test_uniform_darkening_is_rejected_by_the_ratio_gate():
    """
    Both channels down by an identical 4 dB. That is a scene-level shift, not
    water: over real water VH collapses further than VV, so the ratio rises.
    Here the ratio is unchanged, so the gate zeroes it even though both
    channels individually clear z_full.
    """
    s = _score(vv_post=-14.0, vh_post=-24.0, vv_mean=-10.0, vh_mean=-20.0)
    assert s == 0.0


def test_ratio_gate_can_be_disabled():
    """Same inputs, gate off — the min() evidence survives on its own."""
    import flood_detect as fd
    from config import DUALPOL
    cfg = dict(DUALPOL, ratio_gate=False)
    s = fd.dualpol_water_score(
        Px(-14.0), Px(-24.0), Px(-10.0), Px(1.0), Px(-20.0), Px(1.0), cfg=cfg).v
    assert s == pytest.approx(1.0)


# ── Masks ────────────────────────────────────────────────────────────────────

def test_slope_mask_zeroes_the_score():
    """Water does not pool on a steep slope, however dark both channels go."""
    s = _score(vv_post=-14.0, vh_post=-25.0, vv_mean=-10.0, vh_mean=-20.0,
               slope_mask=Px(0))
    assert s == 0.0


def test_permanent_water_zeroes_the_score():
    """A river is not a flood claim."""
    s = _score(vv_post=-14.0, vh_post=-25.0, vv_mean=-10.0, vh_mean=-20.0,
               permanent_water=Px(1))
    assert s == 0.0


def test_speckle_tail_is_dropped():
    """A hair past z_min is speckle, not partial inundation."""
    import flood_detect as fd
    from config import DUALPOL
    z = DUALPOL['z_min'] + 0.01 * (DUALPOL['z_full'] - DUALPOL['z_min'])
    s = fd.dualpol_water_score(
        Px(-10.0 - z), Px(-25.0), Px(-10.0), Px(1.0), Px(-20.0), Px(1.0)).v
    assert s == 0.0


# ── Config invariants ────────────────────────────────────────────────────────

def test_gates_are_ordered():
    from config import DUALPOL
    assert DUALPOL['z_full'] > DUALPOL['z_min'] > 0
    assert 0.0 <= DUALPOL['min_score'] < 1.0
    assert DUALPOL['min_vh_baseline_scenes'] >= 1
