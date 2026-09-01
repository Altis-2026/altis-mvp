"""
Tests for the crest gate on Remote-Deny.

Remote-Deny is the only triage class that acts on the ABSENCE of a signal, so
it is the only one whose correctness depends on the satellite having looked at
the right moment. These tests cover both directions of the gate, because both
failure modes are real:

  - too permissive: denies a claim on a pass that flew days off the crest
  - too strict: a manifest-path bug pins every event to 'unknown' and blocks
    Remote-Deny forever, which makes the gate useless rather than selective
    (this actually happened while writing it)
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))

from triage_core import classify_triage  # noqa: E402


def _load_triage_notes():
    """04_triage_notes.py is not an importable module name — load by path."""
    spec = importlib.util.spec_from_file_location(
        "triage_notes", BASE / "pipeline" / "04_triage_notes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A property with no detected water and high confidence — the exact shape that
# would otherwise be denied remotely.
DRY_ROW = {'max_depth_ft': 0.0, 'pct_flooded': 0.0, 'confidence_score': 95}


class TestGateBlocksDenial:
    def test_observed_crest_allows_remote_deny(self):
        cls, _ = classify_triage(DRY_ROW, crest_observed='observed')
        assert cls == 'Remote-Deny'

    @pytest.mark.parametrize('verdict', ['missed', 'partial', 'unknown'])
    def test_anything_else_downgrades_to_review(self, verdict):
        cls, action = classify_triage(DRY_ROW, crest_observed=verdict)
        assert cls == 'Review'
        assert verdict in action, "the reason must name the verdict"
        assert 'crest' in action.lower()

    def test_none_preserves_historical_behaviour(self):
        """Callers not yet updated must not silently change behaviour."""
        cls, _ = classify_triage(DRY_ROW, crest_observed=None)
        assert cls == 'Remote-Deny'
        cls_default, _ = classify_triage(DRY_ROW)
        assert cls_default == 'Remote-Deny'

    def test_gate_does_not_touch_dispatch(self):
        """Only the absence-of-signal class is gated. A detected flood is
        evidence regardless of whether the crest was the moment we saw."""
        wet = {'max_depth_ft': 6.0, 'pct_flooded': 80.0, 'confidence_score': 90}
        for verdict in ('observed', 'missed', 'unknown', None):
            cls, _ = classify_triage(wet, crest_observed=verdict)
            assert cls == 'Dispatch'


class TestManifestLookup:
    """
    The manifest path bug that made the gate useless, locked so it stays fixed.
    """

    def _write(self, tmp_path, payload):
        mod = _load_triage_notes()
        mod.OUTPUT_DIR = str(tmp_path)
        (tmp_path / 'evt_manifest.json').write_text(json.dumps(payload))
        return mod

    def test_reads_the_nested_stage_path(self, tmp_path):
        mod = self._write(tmp_path, {
            'event_id': 'evt',
            'stages': {'flood_detection': {'crest_observed': 'observed'}}})
        assert mod._crest_verdict('evt') == 'observed'

    def test_missing_manifest_is_unknown_not_observed(self, tmp_path):
        mod = _load_triage_notes()
        mod.OUTPUT_DIR = str(tmp_path)
        assert mod._crest_verdict('does-not-exist') == 'unknown'

    def test_manifest_predating_the_crest_check_is_unknown(self, tmp_path):
        """An old manifest has no crest_observed key. It must not be read as a
        pass — that would re-enable denials on unobserved crests silently."""
        mod = self._write(tmp_path, {
            'event_id': 'evt',
            'stages': {'flood_detection': {'sar_orbit_pass': 'DESCENDING'}}})
        assert mod._crest_verdict('evt') == 'unknown'

    def test_corrupt_manifest_is_unknown(self, tmp_path):
        mod = _load_triage_notes()
        mod.OUTPUT_DIR = str(tmp_path)
        (tmp_path / 'evt_manifest.json').write_text('{not json')
        assert mod._crest_verdict('evt') == 'unknown'

    def test_top_level_key_is_not_where_it_lives(self, tmp_path):
        """Guards the specific bug: a value at the top level is NOT the
        manifest's shape, and reading it there would have looked correct in a
        hand-written fixture while failing on every real run."""
        mod = self._write(tmp_path, {'crest_observed': 'observed'})
        assert mod._crest_verdict('evt') == 'unknown'

    def test_real_committed_manifest_parses(self, tmp_path):
        """The shape assumption is checked against a manifest the pipeline
        actually wrote, not only against fixtures."""
        real = BASE / 'outputs' / 'brazos_manifest.json'
        if not real.exists():
            pytest.skip('no committed manifest')
        payload = json.loads(real.read_text())
        assert 'stages' in payload and 'flood_detection' in payload['stages'], (
            "manifest shape changed — _crest_verdict's path is now wrong")
