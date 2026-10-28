"""OOI HITL annotations, the reference that is not extrapolated and the cruise bottle flags."""
import numpy as np
import pandas as pd
import pytest

from rca_sal import calibrate as cal
from rca_sal import ooi_qc
from rca_sal import oxygen as ox
from rca_sal.pipeline import flagged_bottles
from tests.conftest import CFG, ROOT

REFDES = "RS03AXPS-SF03A-2A-CTDPFA302"


def _samples(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame({"time": pd.date_range("2025-01-01", periods=n, freq="s", tz="UTC"), "v": np.arange(n)})


# ------------------------------------------------------------------ HITL annotations
ANN = {"id": 1, "subsite": "RS03AXPS", "node": "SF03A", "sensor": "2A-DOFSTA302", "qc_flag": "suspect", "exclusion_flag": False,
       "start": "2025-01-01 00:00:03", "stop": None, "annotation": "x"}


def test_an_annotation_applies_to_its_own_sensor_or_the_whole_node():
    assert not ooi_qc.applies(ANN, REFDES)
    assert ooi_qc.applies(ANN, "RS03AXPS-SF03A-2A-DOFSTA302")
    assert ooi_qc.applies({**ANN, "sensor": None}, REFDES)
    assert not ooi_qc.applies({**ANN, "sensor": None, "subsite": "CE04OSPS"}, REFDES)


def test_open_ended_annotations_cover_everything_after_their_start():
    on = ooi_qc.annotations_on_samples([{**ANN, "sensor": None}], REFDES, _samples()["time"])
    assert [(a["id"], a["n_samples"]) for a in on] == [(1, 7)]
    assert ooi_qc.annotations_on_samples([ANN], REFDES, _samples()["time"]) == []          # another sensor's annotation
    assert ooi_qc.annotations_on_samples([{**ANN, "sensor": None, "start": "2026-01-01", "stop": None}], REFDES, _samples()["time"]) == []


def test_only_annotations_with_the_exclusion_flag_remove_samples():
    d = _samples()
    flagged = {**ANN, "sensor": None, "exclusion_flag": True, "start": "2025-01-01 00:00:02", "stop": "2025-01-01 00:00:04"}
    marked = ooi_qc.mark_exclusions(d, [flagged, {**ANN, "sensor": None, "start": "2025-01-01 00:00:07", "stop": None}], REFDES)
    assert list(marked["v"][marked[ooi_qc.EXCLUDED]]) == [2, 3, 4]
    kept, n = ooi_qc.drop_excluded(marked)
    assert list(kept["v"]) == [0, 1, 5, 6, 7, 8, 9] and n == 3
    assert ooi_qc.drop_excluded(d)[1] == 0                       # nothing marked, nothing dropped


# ------------------------------------------------------------------ reference that is not extrapolated
def test_reference_is_nan_outside_its_depth_range_only_when_not_extrapolating():
    z = [0.5, 5.0, 12.0]
    np.testing.assert_allclose(cal.reference_at(z, [2.0, 10.0], [1.0, 2.0], extrapolate=True), [1.0, 1.375, 2.0])
    out = cal.reference_at(z, [2.0, 10.0], [1.0, 2.0], extrapolate=False)
    assert np.isnan(out[0]) and np.isnan(out[2]) and out[1] == pytest.approx(1.375)


def test_bins_outside_the_reference_do_not_enter_the_median():
    z = np.arange(0.5, 40.0)
    ref_d = np.arange(10.0, 30.0)
    sens = np.where((z >= 10) & (z <= 30), 35.0 + 0.02, 35.0 + 0.5)              # +0.02 where the reference exists, +0.5 outside
    b = cal.bin_and_smooth(z, {"salinity_psu": sens}, smooth_window=1)
    flat = cal.estimate_correction(b, ref_d, np.full(ref_d.size, 35.0), extrapolate=True)
    restricted = cal.estimate_correction(b, ref_d, np.full(ref_d.size, 35.0), extrapolate=False)
    assert restricted.value == pytest.approx(0.02) and restricted.depth_min_m >= 10 and restricted.depth_max_m <= 30
    assert flat.n_bins > restricted.n_bins and flat.value > restricted.value
    b = cal.bin_and_smooth(z, {"salinity_psu": sens, "conductivity_sm": np.full(z.size, 4.0), "temperature_c": np.full(z.size, 10.0),
                               "pressure_dbar": z}, smooth_window=1)
    with_all = cal.an31_slope(b, ref_d, np.full(ref_d.size, 35.0), smooth_window=1, extrapolate=True)
    inside = cal.an31_slope(b, ref_d, np.full(ref_d.size, 35.0), smooth_window=1, extrapolate=False)
    assert inside["n_bins"] < with_all["n_bins"] and inside["n_bins"] == restricted.n_bins


def test_winkler_bottles_beyond_the_ctd_profile_are_left_out_not_extrapolated():
    ctd_z, ctd_o2 = np.arange(10.0, 30.0), np.full(20, 100.0)
    w_z, w_o2 = np.array([5.0, 15.0, 20.0]), np.array([150.0, 110.0, 110.0])
    kept = ox.winkler_gain(w_z, w_o2, ctd_z, ctd_o2, extrapolate=False)
    assert kept.n == 2 and kept.gain == pytest.approx(1.1) and np.isnan(kept.ratios[0]) and len(kept.ratios) == 3
    assert ox.winkler_gain(w_z, w_o2, ctd_z, ctd_o2, extrapolate=True).n == 3
    with pytest.raises(ValueError, match="no Winkler bottle"):
        ox.winkler_gain([1.0], [150.0], ctd_z, ctd_o2, extrapolate=False)


# ------------------------------------------------------------------ cruise bottle flags
def test_bottles_flagged_by_the_cruise_are_recognised():
    m = pd.DataFrame({"niskin_flag": ["No problems noted", "Leaking", np.nan, "No problems noted", "No problems noted"],
                      "salinity_flag": ["Acceptable; sample processed according to SOP", "Acceptable; sample processed according to SOP",
                                        np.nan, "Acceptable; sample processed according to SOP,Notes/Other", "Questionable"]})
    bad = flagged_bottles(m, CFG["parameters"]["bottle_ok_niskin_flags"], CFG["parameters"]["bottle_ok_salinity_flag_prefix"])
    assert list(bad) == [False, True, False, False, True]


def test_the_records_name_the_code_version():
    v = ooi_qc.code_version(ROOT)
    assert set(v) == {"git_commit", "uncommitted_changes_in_code_or_config"}
    assert v["git_commit"] is None or len(v["git_commit"]) == 40
