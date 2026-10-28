"""Official profile index: labelling samples by it, cross-checking against our own upcast detection (no network)."""
import numpy as np
import pandas as pd

from rca_sal import profile_index as PI


def _index(tmp_path):
    p = tmp_path / "ix.csv"
    p.write_text("profile,start,peak,end\n7,2025-01-01 00:10:00,2025-01-01 00:40:00,2025-01-01 01:10:00\n"
                 "8,2025-01-01 03:00:00,2025-01-01 03:30:00,2025-01-01 04:00:00\n", encoding="utf8")
    return PI.read_index(p)


def test_samples_between_start_and_peak_get_the_profile_number(tmp_path):
    ix = _index(tmp_path)
    t = pd.to_datetime(["2025-01-01 00:00", "2025-01-01 00:10", "2025-01-01 00:25", "2025-01-01 00:40", "2025-01-01 00:41",
                        "2025-01-01 02:00", "2025-01-01 03:15", "2025-01-01 05:00"], utc=True).as_unit("ns")
    lab = PI.label_by_index(pd.DataFrame({"time": t, "depth_m": np.arange(8.0)}), ix)
    assert lab["upcast_id"].tolist() == [-1, 7, 7, 7, -1, -1, 8, -1]      # peak is part of the upcast, the downcast is not


def test_crosscheck_matches_upcasts_within_tolerance(tmp_path):
    ix = _index(tmp_path)
    summ = pd.DataFrame({"start": pd.to_datetime(["2025-01-01 00:10:30", "2025-01-01 03:40:00"], utc=True),
                         "end": pd.to_datetime(["2025-01-01 00:39:30", "2025-01-01 04:10:00"], utc=True)})
    cc = PI.crosscheck(summ, ix)
    assert [u["match"] for u in cc["upcasts"]] == [True, False] and cc["n_matched"] == 1
    assert cc["upcasts"][0]["index_profile"] == 7


def test_first_profiles_after_a_time(tmp_path):
    assert PI.profiles_after(_index(tmp_path), pd.Timestamp("2025-01-01 01:00", tz="UTC"), 5)["profile"].tolist() == [8]


def test_missing_columns_are_reported(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("profile,start\n1,2025-01-01\n", encoding="utf8")
    try:
        PI.read_index(p)
    except ValueError as e:
        assert "peak" in str(e) and "end" in str(e)
    else:
        raise AssertionError("expected ValueError")
