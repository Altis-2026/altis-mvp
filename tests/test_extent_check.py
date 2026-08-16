"""
Tests for the mapped-extent per-property validation.

As with test_hwm_check, these lock the decisions that would corrupt a result
rather than crash it. The headline risk here is the negative class: it is the
first one this project has ever had, and the easiest way to ruin it is to let
unmapped ground quietly count as dry.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "pipeline"))

shapely = pytest.importorskip("shapely", reason="shapely backs the extent check")
from shapely.geometry import box  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location(
        "extent_check", BASE / "validation" / "extent_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ec = _load()

# A 1x1 degree mapped domain whose western half flooded.
BOUNDARY = box(0, 0, 1, 1)
INUNDATION = box(0, 0, 0.5, 1)


def _pts(coords):
    return pd.DataFrame({'longitude': [c[0] for c in coords],
                         'latitude': [c[1] for c in coords]})


class TestLabelling:
    def test_unmapped_ground_is_dropped_not_called_dry(self):
        """
        THE decision this module turns on. A structure outside the USGS mapped
        area boundary has no label — USGS never assessed it. Counting it as dry
        would manufacture negatives out of absence of evidence, inflate
        specificity, and make precision look better than it is.
        """
        df = _pts([(0.25, 0.5),    # inside boundary, inside flood
                   (0.75, 0.5),    # inside boundary, outside flood -> dry
                   (5.00, 5.0)])   # outside the mapped boundary entirely
        out = ec.label_structures(df, BOUNDARY, INUNDATION)

        assert len(out) == 2, "the unmapped structure must be dropped"
        assert sorted(out['truth_flooded']) == [0, 1]

    def test_inside_boundary_outside_extent_is_a_real_negative(self):
        out = ec.label_structures(_pts([(0.75, 0.5)]), BOUNDARY, INUNDATION)
        assert list(out['truth_flooded']) == [0]

    def test_inside_extent_is_positive(self):
        out = ec.label_structures(_pts([(0.25, 0.5)]), BOUNDARY, INUNDATION)
        assert list(out['truth_flooded']) == [1]

    def test_edge_buffer_drops_the_uncertain_band(self):
        """
        The extent is interpolated from surveyed water-surface elevations, so
        it is least trustworthy right at the flood edge. --edge-buffer must
        remove those structures entirely rather than reassign them, so the
        reader can see how much of a result rests on the uncertain band.
        """
        near_edge = _pts([(0.5001, 0.5)])
        assert len(ec.label_structures(near_edge, BOUNDARY, INUNDATION)) == 1
        dropped = ec.label_structures(near_edge, BOUNDARY, INUNDATION,
                                      edge_buffer_m=200)
        assert len(dropped) == 0

    def test_edge_buffer_keeps_structures_far_from_the_edge(self):
        far = _pts([(0.9, 0.5), (0.1, 0.5)])
        kept = ec.label_structures(far, BOUNDARY, INUNDATION, edge_buffer_m=200)
        assert len(kept) == 2

    def test_label_column_survives(self):
        """A DataFrame without truth_flooded silently breaks every metric
        downstream; pandas groupby has eaten this column once already."""
        out = ec.label_structures(_pts([(0.25, 0.5)]), BOUNDARY, INUNDATION)
        assert 'truth_flooded' in out.columns


class TestMetrics:
    def test_confusion_matrix(self):
        m = ec.metrics([1, 1, 1, 0, 0, 0], [1, 1, 0, 1, 0, 0])
        assert (m['tp'], m['fn'], m['fp'], m['tn']) == (2, 1, 1, 2)
        assert m['precision'] == pytest.approx(2 / 3)
        assert m['recall'] == pytest.approx(2 / 3)
        assert m['specificity'] == pytest.approx(2 / 3)

    def test_precision_is_nan_not_zero_when_nothing_is_predicted(self):
        """A detector that never fires has UNDEFINED precision, not perfect and
        not zero. Reporting 0.0 would read as 'measured, and bad'."""
        m = ec.metrics([1, 0, 1], [0, 0, 0])
        assert m['precision'] != m['precision']   # NaN
        assert m['recall'] == 0.0

    def test_base_rate_is_carried_so_precision_can_be_judged(self):
        """80% precision is excellent at a 5% base rate and worthless at 80%."""
        m = ec.metrics([1, 1, 1, 1, 0], [1, 1, 1, 1, 1])
        assert m['base_rate'] == pytest.approx(0.8)

    def test_specificity_needs_the_negative_class(self):
        m = ec.metrics([1, 1], [1, 0])
        assert m['specificity'] != m['specificity']   # no negatives -> NaN


class TestAuc:
    def test_perfect_separation(self):
        assert ec.auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)

    def test_inverted_separation(self):
        assert ec.auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == pytest.approx(0.0)

    def test_no_information_is_half(self):
        assert ec.auc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == pytest.approx(0.5)

    def test_single_class_abstains(self):
        assert ec.auc([1, 1, 1], [0.1, 0.5, 0.9]) is None


class TestGroundTruthAsset:
    def test_committed_extent_geojson_is_present_and_usable(self):
        """The GeoJSON is committed precisely so this runs with no network."""
        boundary, inundation = ec.load_extent()
        assert boundary.is_valid and inundation.is_valid
        assert not boundary.is_empty and not inundation.is_empty

    def test_mapped_dry_area_actually_exists(self):
        """If the boundary hugged the flood extent there would be no negative
        class at all — which is exactly what the LOWER Brazos reach turned out
        to be. Guard the property the whole method depends on."""
        boundary, inundation = ec.load_extent()
        dry = boundary.difference(inundation)
        assert dry.area > 0.2 * boundary.area, (
            "mapped-dry margin vanished; the negative class is gone")

    def test_extent_overlaps_the_brazos_study_bbox(self):
        import config
        boundary, _ = ec.load_extent()
        assert boundary.intersects(box(*config.BRAZOS['bbox']))

    @pytest.mark.parametrize('event', ['brazos', 'harvey'])
    def test_every_registered_event_has_a_usable_extent(self, event):
        """Both events must resolve, or `--event harvey` is a trap that only
        fails after the NSI fetch and the whole image build have been paid for."""
        boundary, inundation = ec.load_extent(event)
        assert boundary.is_valid and not boundary.is_empty
        assert inundation.is_valid and not inundation.is_empty
        assert boundary.difference(inundation).area > 0, (
            f"{event} has no mapped-dry margin — no negative class")

    @pytest.mark.parametrize('event', ['brazos', 'harvey'])
    def test_extent_overlaps_its_own_event_bbox(self, event):
        import config
        boundary, _ = ec.load_extent(event)
        cfg = getattr(config, ec.EXTENTS[event]['cfg'])
        assert boundary.intersects(box(*cfg['bbox']))

    def test_harvey_coverage_limit_is_recorded_not_hidden(self):
        """
        The San Jacinto reach covers only the northern part of the HARVEY bbox,
        so Harvey numbers describe riverine floodplain in north Harris County
        rather than the Addicks/Barker reservoir release. That limitation has
        to travel with the code, because the number will outlive this session.
        """
        assert 'northern' in ec.EXTENTS['harvey']['reach'].lower()
        src = (BASE / "validation" / "extent_check.py").read_text()
        assert 'Addicks/Barker' in src

    def test_events_share_one_ground_truth_source(self):
        """Different vendors for the two events would make any Brazos-vs-Harvey
        difference unattributable to the detector."""
        import json
        srcs = []
        for e in ('brazos', 'harvey'):
            gj = json.loads((ec.OUT / ec.EXTENTS[e]['geojson']).read_text())
            srcs.append(gj.get('properties', {}).get('source', ''))
        assert all('2018-5070' in s for s in srcs), srcs
