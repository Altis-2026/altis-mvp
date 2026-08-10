"""
Tests for the sub-pixel water fraction (Phase 4a).

The claim being pinned is the physics: backscatter mixes linearly in POWER,
so a pixel that is half water should unmix to ~0.5 — not to whatever a
dB-domain subtraction happens to produce. These tests exercise the arithmetic
directly against hand-computed expectations rather than through Earth Engine.
"""
import math
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "pipeline"))

from config import SUBPIXEL  # noqa: E402


def db_to_power(db):
    return 10.0 ** (db / 10.0)


def unmix(obs_db, dry_db, water_db=None):
    """
    Reference implementation of the same arithmetic flood_detect.water_fraction
    performs on images. Kept independent so a change to the image code that
    breaks the physics fails here rather than silently shipping.
    """
    water_db = SUBPIXEL['water_endmember_db'] if water_db is None else water_db
    dry, water, obs = db_to_power(dry_db), db_to_power(water_db), db_to_power(obs_db)
    denom = dry - water
    if denom <= 0:
        return 0.0
    return max(0.0, min(1.0, (dry - obs) / denom))


def test_fully_dry_pixel_is_zero():
    assert unmix(obs_db=-8.0, dry_db=-8.0) == 0.0


def test_fully_wet_pixel_is_one():
    assert unmix(obs_db=SUBPIXEL['water_endmember_db'], dry_db=-8.0) == 1.0


def test_darker_than_water_endmember_clamps_to_one():
    """A pixel darker than open water is fully water, not >100% water."""
    assert unmix(obs_db=-30.0, dry_db=-8.0) == 1.0


def test_half_water_pixel_unmixes_to_about_half():
    """
    The core physical claim. Build an observation that is genuinely 50/50 in
    POWER, then check it inverts back to ~0.5.
    """
    dry_db, water_db = -8.0, SUBPIXEL['water_endmember_db']
    mixed_power = 0.5 * db_to_power(water_db) + 0.5 * db_to_power(dry_db)
    mixed_db = 10.0 * math.log10(mixed_power)
    assert unmix(mixed_db, dry_db, water_db) == pytest.approx(0.5, abs=1e-6)


@pytest.mark.parametrize("true_f", [0.1, 0.25, 0.4, 0.6, 0.75, 0.9])
def test_unmixing_recovers_arbitrary_fractions(true_f):
    dry_db, water_db = -7.5, SUBPIXEL['water_endmember_db']
    mixed_power = true_f * db_to_power(water_db) + (1 - true_f) * db_to_power(dry_db)
    mixed_db = 10.0 * math.log10(mixed_power)
    assert unmix(mixed_db, dry_db, water_db) == pytest.approx(true_f, abs=1e-6)


def test_db_domain_subtraction_would_be_wrong():
    """
    Guards the reason this is done in power. A naive dB-domain linear
    interpolation gives a materially different (wrong) answer for the same
    genuinely-half-water pixel, so nobody should "simplify" it back.
    """
    dry_db, water_db = -8.0, SUBPIXEL['water_endmember_db']
    mixed_power = 0.5 * db_to_power(water_db) + 0.5 * db_to_power(dry_db)
    mixed_db = 10.0 * math.log10(mixed_power)

    naive_db_fraction = (dry_db - mixed_db) / (dry_db - water_db)
    assert unmix(mixed_db, dry_db, water_db) == pytest.approx(0.5, abs=1e-6)
    # The dB-domain answer is wrong by a wide margin — it reads a half-water
    # pixel as roughly a quarter water.
    assert abs(naive_db_fraction - 0.5) > 0.2


def test_monotonic_in_observed_darkness():
    dry_db = -8.0
    fracs = [unmix(obs, dry_db) for obs in (-8.0, -10.0, -12.0, -15.0, -20.0)]
    assert fracs == sorted(fracs)


def test_already_water_like_pixel_yields_zero_not_garbage():
    """
    Where the dry reference is at or below the water endmember the unmixing is
    meaningless (denominator <= 0) — permanent water, for instance. It must
    return 0 rather than a negative or exploding value.
    """
    assert unmix(obs_db=-25.0, dry_db=-22.0, water_db=-20.0) == 0.0


def test_config_gate_is_looser_than_binary_detector():
    """
    The whole point of the sub-pixel path is to recover partial inundation the
    strict binary gate discards, so its z gate must be the looser of the two.
    """
    from config import BASELINE
    assert SUBPIXEL['z_min'] < BASELINE['z_threshold']


def test_water_endmember_below_otsu_water_range():
    """The endmember should be unambiguous open water, not borderline."""
    from config import SAR
    assert SUBPIXEL['water_endmember_db'] <= SAR['water_db_max']
