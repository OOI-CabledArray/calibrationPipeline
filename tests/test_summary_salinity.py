"""Bottle salinity from a discrete summary (cruises without the lab's workbooks): replicates, flags and the run's source choice."""
import numpy as np
import pandas as pd
import pytest

from rca_sal import discrete
from rca_sal.pipeline import SALINITY_BOTTLE_SOURCES, salinity_bottle_roles

OK, BAD, MISSING = "*0000000000000100", "*0000000000001000", -9999999


def _row(niskin, sal, flag=OK, rep=OK, nisk=OK, cast="CTD-001"):
    return {"Cast": cast, "Niskin/Bottle Position": niskin, "Niskin Flag": nisk, "CTD Depth [m]": 10.0 * niskin,
            "Discrete Salinity [psu]": sal, "Discrete Salinity Flag": flag, "Discrete Salinity Replicate Flag": rep}


def _read(tmp_path, rows, cast="CTD-001"):
    f = tmp_path / "s.csv"
    pd.DataFrame(rows).to_csv(f, index=False)
    return discrete.read_summary_salinity(f, cast, "No problems noted", "Acceptable")


def test_replicates_are_averaged_and_bad_ones_left_out(tmp_path):
    samples, autosal = _read(tmp_path, [_row(1, 33.0), _row(1, 33.2), _row(1, 35.0, rep=BAD), _row(2, 34.0), _row(3, MISSING), _row(4, 31.0, cast="CTD-002")])
    s, a = samples.set_index("niskin"), autosal.set_index("bottle_id")
    assert s.index.tolist() == [1, 2]
    assert a.loc["1", "salinity"] == pytest.approx(33.1) and a.loc["2", "salinity"] == pytest.approx(34.0)
    assert (s["salinity_flag"] == "Acceptable").all() and (s["niskin_flag"] == "No problems noted").all()
    assert s.loc[1, "sample_id"] == "CTD-001 Niskin 1" and s.loc[1, "target_depth_m"] == pytest.approx(10.0)
    assert np.isnan(a.loc["1", "ratio_2rt"]) and not a["is_standard_check"].any()


def test_flagged_bottles_keep_their_value_with_a_flag_the_check_excludes(tmp_path):
    samples, autosal = _read(tmp_path, [_row(1, 33.0, flag=BAD), _row(2, 34.0, nisk=BAD), _row(3, 35.0, flag=np.nan)])
    s = samples.set_index("niskin")
    assert s.loc[1, "salinity_flag"] == "not acceptable" and s.loc[2, "niskin_flag"] == "not acceptable"
    assert pd.isna(s.loc[3, "salinity_flag"])                      # an absent flag does not exclude the bottle
    assert autosal.set_index("bottle_id").loc["1", "salinity"] == pytest.approx(33.0)


def test_unknown_cast_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        _read(tmp_path, [_row(1, 33.0)], cast="CTD-009")


def test_bottle_source_is_the_workbooks_unless_only_the_summary_is_configured():
    workbooks, summary = SALINITY_BOTTLE_SOURCES
    assert salinity_bottle_roles({"autosal_xlsx": "a", "cast_log_xlsm": "b", "discrete_summary_csv": "c"}) == workbooks
    assert salinity_bottle_roles({"discrete_summary_csv": "c"}) == summary
    assert salinity_bottle_roles({"autosal_xlsx": "a", "discrete_summary_csv": "c"}) == workbooks     # half a workbook set is reported missing
    assert salinity_bottle_roles({}) == workbooks
