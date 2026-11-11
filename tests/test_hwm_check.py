"""
Tests for the USGS high water mark validation path.

The decisions locked here are the ones that would silently corrupt the result
rather than crash it — a validation script that quietly measures the wrong
thing is worse than one that fails, because its number gets quoted.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))


def _load_hwm_check():
    """Import hwm_check by path — validation/ is a script dir, not a package."""
    spec = importlib.util.spec_from_file_location(
        "hwm_check", BASE / "validation" / "hwm_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hwm = _load_hwm_check()

BBOX = [-96.0, 29.0, -95.0, 30.0]


def _mark(hwm_id, lon, lat, height, site_id=1):
    return {'hwm_id': hwm_id, 'site_id': site_id,
            'longitude_dd': lon, 'latitude_dd': lat,
            'height_above_gnd': height, 'hwm_quality_id': 1,
            'hwm_type_id': 2, 'hwm_environment': 'Riverine',
            'waterbody': 'test creek', 'flag_date': None, 'survey_date': None}


class TestMarkSelection:
    def test_zero_height_is_dropped_not_treated_as_zero_depth(self):
        """
        THE decision this module turns on. 974 of the 2,364 Harvey marks carry
        height_above_gnd == 0.0 while also carrying a surveyed elev_ft, and 670
        of those are debris lines — an unfilled optional field, not a measured
        zero. Admitting them would manufacture ~900 fake "no flooding here"
        ground-truth points out of a dataset that contains no dry points at
        all, and would make recall look far worse and precision look
        measurable when it is not.
        """
        marks = [_mark(1, -95.5, 29.5, 2.0), _mark(2, -95.5, 29.5, 0.0)]
        df, report = hwm.hwms_in_bbox(marks, BBOX)

        assert len(df) == 1
        assert df.iloc[0]['hwm_id'] == 1
        assert report['dropped_zero_height'] == 1
        # The drop has to be reported, not just performed.
        assert report['in_bbox'] == 2 and report['usable'] == 1

    def test_null_height_is_dropped_and_counted_separately(self):
        marks = [_mark(1, -95.5, 29.5, 2.0), _mark(2, -95.5, 29.5, None)]
        df, report = hwm.hwms_in_bbox(marks, BBOX)

        assert len(df) == 1
        assert report['dropped_null_height'] == 1
        # Distinct from the zero case: "not recorded" and "recorded as 0" are
        # different data problems and are counted apart.
        assert report['dropped_zero_height'] == 0

    def test_marks_outside_bbox_excluded(self):
        marks = [_mark(1, -95.5, 29.5, 2.0), _mark(2, -90.0, 29.5, 2.0)]
        df, report = hwm.hwms_in_bbox(marks, BBOX)

        assert list(df['hwm_id']) == [1]
        assert report['in_bbox'] == 1

    def test_missing_coordinates_do_not_crash_selection(self):
        marks = [_mark(1, -95.5, 29.5, 2.0), _mark(2, None, None, 2.0)]
        df, _ = hwm.hwms_in_bbox(marks, BBOX)
        assert list(df['hwm_id']) == [1]

    def test_property_id_is_a_string_for_the_sampler(self):
        """sample_properties keys on a string property_id; an int silently
        breaks the merge back onto the ground truth and yields all-NaN depths."""
        df, _ = hwm.hwms_in_bbox([_mark(7, -95.5, 29.5, 2.0)], BBOX)
        assert df.iloc[0]['property_id'] == '7'
        assert isinstance(df.iloc[0]['property_id'], str)


class TestSiteRecallBound:
    def _frame(self, rows):
        return pd.DataFrame(
            [{'site_id': s, 'max_depth_ft': d} for s, d in rows])

    def test_zero_detections_reports_a_bound_not_just_zero(self):
        """A bare 0/18 is compatible with a detector that has real recall and
        got unlucky. The upper bound is the quotable number."""
        k, n, lo, hi = hwm._recall_ci_by_site(
            self._frame([(i, 0.0) for i in range(18)]))
        assert (k, n) == (0, 18)
        assert lo == 0.0
        assert 0.15 < hi < 0.20      # Clopper-Pearson upper bound ≈ 18.5%

    def test_sites_are_the_unit_not_marks(self):
        """Six marks at two sites is two observations of the detector, not
        six. Counting marks would overstate the sample and shrink the CI."""
        k, n, _, _ = hwm._recall_ci_by_site(
            self._frame([(1, 0.0), (1, 0.0), (1, 0.0),
                         (2, 0.0), (2, 0.0), (2, 0.0)]))
        assert n == 2

    def test_a_site_counts_as_detected_if_any_mark_detects(self):
        """The most generous reading available, so the upper bound is not
        flattered by a strict one."""
        k, n, _, _ = hwm._recall_ci_by_site(
            self._frame([(1, 0.0), (1, 5.0), (2, 0.0)]))
        assert (k, n) == (1, 2)

    def test_detection_floor_matches_the_pipeline(self):
        """Recall here must mean what "flooded" means everywhere else in the
        repo, or the number is not comparable to any other in the docs."""
        assert hwm.DETECT_FLOOR_FT == 0.1
        k, _, _, _ = hwm._recall_ci_by_site(self._frame([(1, 0.05)]))
        assert k == 0, "a depth below the floor must not count as a detection"

    def test_empty_frame_abstains(self):
        assert hwm._recall_ci_by_site(pd.DataFrame(
            columns=['site_id', 'max_depth_ft'])) == (0, 0, None, None)


class TestSummary:
    def test_bias_is_signed_and_negative_when_we_underestimate(self):
        df = pd.DataFrame([
            {'site_id': 1, 'surveyed_ft': 4.0, 'max_depth_ft': 0.0},
            {'site_id': 2, 'surveyed_ft': 2.0, 'max_depth_ft': 0.0},
        ])
        s = hwm.summarize(df, 'test')
        assert s['bias_ft'] == -3.0
        assert s['mae_ft'] == 3.0
        # Missing every known-flooded point is 0% recall, not an error state.
        assert s['recall_at_0.1ft'] == 0.0

    def test_reports_site_count_alongside_mark_count(self):
        df = pd.DataFrame([
            {'site_id': 1, 'surveyed_ft': 1.0, 'max_depth_ft': 0.0},
            {'site_id': 1, 'surveyed_ft': 2.0, 'max_depth_ft': 0.0},
        ])
        s = hwm.summarize(df, 'test')
        assert s['n_marks'] == 2 and s['n_sites'] == 1


class TestSharedDetectorAssembly:
    def test_pipeline_and_validator_use_the_same_builder(self):
        """
        The refactor's whole point. If 03_flood_pipeline ever stops importing
        build_event_image, the validator silently starts measuring a different
        detector than the one that ships — the failure mode that already cost
        this repo a 4,000-property run when Phase 4b was dropped from an
        output list.
        """
        src = (BASE / "pipeline" / "03_flood_pipeline.py").read_text()
        assert "from event_image import build_event_image" in src
        assert "build_event_image(event_config)" in src

        val = (BASE / "validation" / "hwm_check.py").read_text()
        assert "from event_image import build_event_image" in val

    def test_build_event_image_reports_abstention_distinctly(self):
        """meta must let a caller tell "measured, nothing there" from "not
        measured" — the abstention contract the rest of the repo keeps."""
        import event_image
        import inspect
        src = inspect.getsource(event_image.build_event_image)
        for key in ('baseline_active', 'dualpol_active',
                    'vh_baseline_scene_count', 'hand_source'):
            assert key in src, f"meta lost {key}"


@pytest.mark.parametrize('event', ['brazos', 'harvey'])
def test_event_is_registered_for_validation(event):
    """Both study areas are Harvey, so both validate against STN event 180."""
    assert event in hwm.EVENTS
    assert hwm.STN_EVENT_ID == 180
