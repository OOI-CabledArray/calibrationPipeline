"""bin_and_smooth must equal the notebook's xarray groupby_bins + rolling exactly."""
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from rca_sal import calibrate as cal, profiler
from tests import notebook_reference as M
from tests.conftest import need, path25


@need("profiler_nc")
def test_ascending_mask_and_bins_match_notebook_on_real_nc(prof25):
    asc, binned = M.load_ctdpfa_ascending(path25("profiler_nc"))
    mine = prof25[profiler.ascending_mask(prof25)]
    assert len(mine) == asc.sizes["obs" if "obs" in asc.dims else "time"]
    assert np.array_equal(mine["depth_m"].to_numpy(), asc["depth"].values)
    b = cal.bin_and_smooth(mine["depth_m"], {"salinity_psu": mine["salinity_psu"],
                                             "conductivity_sm": mine["conductivity_sm"]})
    assert np.array_equal(b.index.to_numpy(), binned["depth_bins"].values)
    np.testing.assert_allclose(b["salinity_psu"].to_numpy(), binned["sea_water_practical_salinity"].values,
                               rtol=0, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(b["conductivity_sm"].to_numpy(), binned["sea_water_electrical_conductivity"].values,
                               rtol=0, atol=1e-12, equal_nan=True)


@pytest.mark.parametrize("seed,deepest", [(0, 120.0), (1, 99.5), (2, 50.0)])
def test_matches_xarray_on_synthetic_gappy_data(seed, deepest):
    rng = np.random.default_rng(seed)
    depth = rng.uniform(0.05, deepest, 4000)
    depth[(depth > 20) & (depth < 24)] = np.nan_to_num(depth[(depth > 20) & (depth < 24)]) + 6   # empty bins
    sal = 33 + 0.01 * depth + rng.normal(0, 0.01, depth.size)
    sal[rng.random(sal.size) < 0.02] = np.nan
    ds = xr.Dataset({"depth": ("obs", depth), "s": ("obs", sal)})
    edges = np.arange(0, ds["depth"].max().item() + 1, 1)
    ref = ds.groupby_bins("depth", bins=edges).mean()
    ref["s"] = ref["s"].rolling(depth_bins=5, center=True, min_periods=1).mean()
    b = cal.bin_and_smooth(depth, {"s": sal}, smooth=("s",))
    mids = [iv.mid for iv in ref["depth_bins"].values]
    assert np.allclose(b.index, mids)
    np.testing.assert_allclose(b["s"].to_numpy(), ref["s"].values, atol=1e-12, equal_nan=True)


def test_bin_edges_are_right_closed():
    b = cal.bin_and_smooth([0.5, 1.0, 1.0001, 2.0], {"v": [1, 2, 3, 4]}, smooth=())
    assert list(b.index) == [0.5, 1.5]
    assert b["v"].tolist() == [1.5, 3.5]       # (0,1] holds 0.5 and 1.0; (1,2] holds 1.0001 and 2.0
