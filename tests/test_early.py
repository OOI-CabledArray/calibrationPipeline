"""Request windows of the earlier cruises: overlapping windows of one instrument are merged."""
import pandas as pd

from rca_sal import instruments


def _w(rd, begin, end, bottles):
    return {"rd": rd, "begin": pd.Timestamp(begin, tz="UTC"), "end": pd.Timestamp(end, tz="UTC"), "bottles": bottles}


def test_overlapping_windows_of_one_instrument_are_merged_others_kept():
    w = pd.DataFrame([_w("A", "2015-08-02", "2015-08-05", 8), _w("A", "2015-08-01", "2015-08-04", 8), _w("A", "2016-07-01", "2016-07-04", 2),
                      _w("B", "2015-08-01", "2015-08-04", 5), _w("A", "2015-08-05", "2015-08-06", 1)])
    m = instruments.merge_windows(w)
    a = m[m.rd == "A"].reset_index(drop=True)
    assert len(m) == 3 and len(a) == 2
    assert (a.loc[0, "begin"], a.loc[0, "end"], a.loc[0, "bottles"]) == (pd.Timestamp("2015-08-01", tz="UTC"), pd.Timestamp("2015-08-06", tz="UTC"), 17)
    assert a.loc[1, "bottles"] == 2 and m[m.rd == "B"]["bottles"].iloc[0] == 5


def test_empty_input_gives_no_windows():
    assert instruments.merge_windows(pd.DataFrame(columns=["rd", "begin", "end", "bottles"])).empty


def _dofsta(tmp_path, name, t0, units="umol kg-1"):
    import xarray as xr
    t = pd.date_range(t0, periods=3, freq="s")
    ds = xr.Dataset({"depth": ("obs", [10.0, 11.0, 12.0]), "corrected_dissolved_oxygen": ("obs", [250.0, 251.0, 252.0], {"units": units})},
                    coords={"time": ("obs", t.values)})
    p = tmp_path / name
    ds.to_netcdf(p)
    return p


def test_dofsta_files_of_two_deployments_are_read_as_one_sorted_table(tmp_path):
    from rca_sal import oxygen
    later, earlier = _dofsta(tmp_path, "b.nc", "2016-07-02"), _dofsta(tmp_path, "a.nc", "2016-07-01")
    d = oxygen.read_dofsta_nc([later, earlier])
    assert len(d) == 6 and d["time"].is_monotonic_increasing and d.attrs["o2_units"] == "umol kg-1"
    assert len(oxygen.read_dofsta_nc(earlier)) == 3


def test_dofsta_files_that_disagree_on_units_are_refused(tmp_path):
    import pytest
    from rca_sal import oxygen
    with pytest.raises(ValueError):
        oxygen.read_dofsta_nc([_dofsta(tmp_path, "a.nc", "2016-07-01"), _dofsta(tmp_path, "b.nc", "2016-07-02", units="ml l-1")])


def test_winkler_deeper_than_the_profiler_is_left_out():
    from rca_sal import oxygen
    w = pd.DataFrame({"depth_m": [10.0, 150.0, 200.0, 900.0, 2400.0], "o2_winkler_umolkg": [290.0, 250.0, 200.0, 0.5, 69.0]})
    kept = oxygen.within_profiler_range(w, 200.0)
    assert kept["depth_m"].tolist() == [10.0, 150.0, 200.0] and oxygen.within_profiler_range(w, 5.0).empty
