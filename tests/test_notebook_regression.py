"""Exact reproduction of the notebook T1 (Aug 2025) using the notebook functions as the oracle."""
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from rca_sal import calibrate as cal, profiler, seabird
from tests import notebook_reference as M
from tests.conftest import ROOT, Y25, need, path25


@need("profiler_nc", "reference_cnv")
def test_t1_offset_iqr_rmse_equal_notebook_function(tmp_path, prof25):
    w0, w1 = (pd.Timestamp(Y25["notebook_window_utc"][k]) for k in ("start", "end"))
    # the notebook profiler file is the same request cropped to the notebook window: write that crop as .nc, feed the notebook code
    with xr.open_dataset(path25("profiler_nc")) as ds:
        t = pd.to_datetime(ds["time"].values).tz_localize("UTC")
        sub = ds.isel({ds["depth"].dims[0]: np.flatnonzero((t >= w0) & (t <= w1))}).load()
    crop = tmp_path / "window.nc"
    sub.to_netcdf(crop)
    asc, binned = M.load_ctdpfa_ascending(str(crop))
    d_ctd = M.read_ctd_cnv(str(path25("reference_cnv")))
    off, iqr, rmse = M.salinity_offset(binned, d_ctd)

    win = prof25[(prof25.time >= w0) & (prof25.time <= w1)].reset_index(drop=True)
    sel = win[profiler.ascending_mask(win)]
    b = cal.bin_and_smooth(sel.depth_m, {"salinity_psu": sel.salinity_psu})
    ctd, _ = seabird.read_cnv(path25("reference_cnv"))
    r = cal.estimate_correction(b, ctd["prDM"], ctd["sal00"])
    assert r.value == pytest.approx(off, abs=1e-12)
    assert r.iqr == pytest.approx(iqr, abs=1e-12)
    assert r.rmse == pytest.approx(rmse, abs=1e-12)
    t1_mine = profiler.mean_time(sel.time)
    t1_hers = pd.Timestamp(asc["time"].mean().values, tz="UTC")
    assert abs((t1_mine - t1_hers).total_seconds()) < 1e-3
    # and the numbers quoted for the notebook T1 (offset -0.022, IQR 0.0421)
    assert round(off, 3) == -0.022 and round(iqr, 4) == 0.0421


@need("profiler_nc", "reference_cnv")
def test_pipeline_regression_variant_equals_notebook(tmp_path):
    from rca_sal.pipeline import run_year
    rec = run_year(2025, ROOT / "config" / "cruises.toml", tmp_path)
    v = {x["variant_id"]: x for x in rec["rca:variants"]}["notebook_window_ascending_prDM"]
    assert round(v["offset_psu"], 3) == -0.022 and round(v["iqr_psu"], 4) == 0.0421
