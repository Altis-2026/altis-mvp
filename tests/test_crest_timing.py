"""
Tests for the crest-timing disclosure.

The logic here decides whether "no flood detected" may become a Remote-Deny.
Getting it wrong in the permissive direction produces a wrongly denied claim on
a house that was under water, so the abstention behaviour is tested as
carefully as the positive path.

All pure-function tests — no network. `fetch_gauge_peaks` is exercised against
live USGS separately in validation, not here.
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))

import config  # noqa: E402
import crest_timing as ct  # noqa: E402


def peak_at(iso, name='Test Gauge', site='0000', value=10.0):
    return {'site': site, 'name': name, 'parameter': '00065',
            'peak_value': value, 'peak_time_utc': iso, 'n_readings': 100}


def scenes(*isos):
    return [dt.datetime.fromisoformat(s) for s in isos]


class TestVerdicts:
    def test_pass_on_the_crest_is_observed(self):
        a = ct.assess(scenes('2017-09-01T06:00'),
                      [peak_at('2017-09-01T05:15')])
        assert a['crest_observed'] == 'observed'
        assert ct.safe_to_deny(a) is True

    def test_pass_days_away_is_missed(self):
        a = ct.assess(scenes('2017-09-05T12:22'),
                      [peak_at('2017-09-01T05:15')])
        assert a['crest_observed'] == 'missed'
        assert ct.safe_to_deny(a) is False

    def test_some_caught_some_missed_is_partial(self):
        a = ct.assess(
            scenes('2017-08-30T12:22'),
            [peak_at('2017-08-30T14:00', name='near'),
             peak_at('2017-09-01T05:15', name='far')])
        assert a['crest_observed'] == 'partial'
        assert a['gauges_observed'] == 1 and a['gauges_total'] == 2

    def test_partial_is_not_safe_to_deny(self):
        """A partial crest means an absence of signal is uninformative for the
        catchments that were missed. Denying on it is the failure mode."""
        a = ct.assess(
            scenes('2017-08-30T12:22'),
            [peak_at('2017-08-30T14:00'), peak_at('2017-09-05T05:15')])
        assert a['crest_observed'] == 'partial'
        assert ct.safe_to_deny(a) is False


class TestAbstention:
    def test_no_gauges_is_unknown_not_observed(self):
        """
        Most of the world has no gauge coverage. Collapsing "could not check"
        into "checked and fine" would reintroduce the exact failure this module
        exists to expose.
        """
        a = ct.assess(scenes('2017-09-01T06:00'), [])
        assert a['crest_observed'] == 'unknown'
        assert ct.safe_to_deny(a) is False

    def test_no_scenes_is_unknown(self):
        a = ct.assess([], [peak_at('2017-09-01T05:15')])
        assert a['crest_observed'] == 'unknown'
        assert ct.safe_to_deny(a) is False

    def test_unknown_is_never_safe_to_deny(self):
        for a in (ct.assess([], []),
                  ct.assess(scenes('2017-09-01T06:00'), [])):
            assert a['crest_observed'] == 'unknown'
            assert ct.safe_to_deny(a) is False


class TestGapSign:
    def test_pass_before_crest_is_negative(self):
        """
        Sign convention matters for the disclosure text: negative means we
        photographed the RISING limb and the water kept climbing after we
        looked away. That is the Brazos case (-40.9 h at Richmond).
        """
        a = ct.assess(scenes('2017-08-30T12:22'),
                      [peak_at('2017-09-01T05:15')])
        assert a['gauges'][0]['nearest_pass_gap_hours'] < 0

    def test_pass_after_crest_is_positive(self):
        a = ct.assess(scenes('2017-09-03T12:22'),
                      [peak_at('2017-09-01T05:15')])
        assert a['gauges'][0]['nearest_pass_gap_hours'] > 0

    def test_nearest_pass_wins_not_the_first(self):
        a = ct.assess(
            scenes('2017-08-20T00:00', '2017-09-01T07:00', '2017-09-20T00:00'),
            [peak_at('2017-09-01T05:15')])
        assert abs(a['gauges'][0]['nearest_pass_gap_hours']) < 2
        assert a['crest_observed'] == 'observed'

    def test_worst_gauge_is_reported_not_the_best(self):
        """The disclosure has to be driven by the catchment we saw worst."""
        a = ct.assess(
            scenes('2017-09-01T06:00'),
            [peak_at('2017-09-01T05:30', name='good'),
             peak_at('2017-09-05T05:30', name='bad')])
        assert a['worst_gauge'] == 'bad'
        assert abs(a['worst_gap_hours']) > 24


class TestTolerance:
    def test_tolerance_is_configurable(self):
        peaks = [peak_at('2017-09-01T05:15')]
        s = scenes('2017-09-02T05:15')      # exactly 24 h later
        assert ct.assess(s, peaks, cfg={'tolerance_hours': 24})[
            'crest_observed'] == 'observed'
        assert ct.assess(s, peaks, cfg={'tolerance_hours': 6})[
            'crest_observed'] == 'missed'

    def test_shipped_tolerance_and_gate(self):
        assert config.CREST['tolerance_hours'] == 24
        assert config.CREST['gate_remote_deny'] is True, (
            "Turning this off allows a Remote-Deny on an unobserved crest")

    def test_gate_can_be_disabled_explicitly(self):
        a = ct.assess(scenes('2017-09-05T12:22'), [peak_at('2017-09-01T05:15')])
        assert ct.safe_to_deny(a) is False
        assert ct.safe_to_deny(a, cfg={'gate_remote_deny': False}) is True


class TestTimezones:
    def test_offset_timestamps_normalise_to_utc(self):
        """NWIS returns local time with an offset; mixing naive and aware
        datetimes would raise, and comparing them unconverted would be silently
        off by the offset — 5 hours for Texas."""
        naive = ct._parse_iso('2017-09-01T00:15:00.000-05:00')
        assert naive == dt.datetime(2017, 9, 1, 5, 15)
        assert naive.tzinfo is None

    def test_already_utc_is_unchanged(self):
        assert ct._parse_iso('2017-09-01T05:15:00') == \
            dt.datetime(2017, 9, 1, 5, 15)


class TestRealEventRegression:
    def test_brazos_richmond_crest_was_missed_by_41_hours(self):
        """
        Measured from live USGS: Brazos at Richmond crested 55.19 ft at
        2017-09-01T05:15Z, and the nearest Sentinel-1 pass was 2017-08-30T12:22Z.
        Pinned because it is the concrete example the config comment cites and
        a partial explanation for the -2.64 ft depth bias.
        """
        a = ct.assess(
            scenes('2017-08-29T00:26', '2017-08-30T12:22', '2017-09-05T12:22',
                   '2017-09-10T00:26', '2017-09-11T12:22'),
            [peak_at('2017-09-01T05:15', name='Brazos Rv at Richmond, TX',
                     value=55.19)])
        gap = a['gauges'][0]['nearest_pass_gap_hours']
        assert gap == pytest.approx(-40.9, abs=0.2)
        assert a['crest_observed'] == 'missed'
        assert ct.safe_to_deny(a) is False
