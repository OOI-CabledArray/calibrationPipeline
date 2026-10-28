"""DOFSTA gain: EOS-80 conversion, Winkler reading, and exact reproduction of the notebook T1 (Aug 2025)."""
import glob

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from rca_sal import calibrate as cal, eos80, oxygen as ox, profiler, seabird
from tests import notebook_reference_o2 as M
from tests.conftest import CFG, ROOT, Y25, resolve_spec

O = Y25["oxygen"]


def p(role):
    return resolve_spec(O["inputs"][role] if role in O["inputs"] else Y25["inputs"][role])


def need_o2(*roles):
    gone = [str(p(r)) for r in roles if not p(r).is_file()]
    return pytest.mark.skipif(bool(gone), reason=f"input missing: {gone}")


ALL = ("winkler_xls", "dofsta_nc", "reference_cnv")


# ------------------------------------------------------------------ EOS-80 (UNESCO TR44 check values)
@pytest.mark.parametrize("fn,args,expect,tol", [
    (eos80.dens0, (35.0, 5.0), 1027.67547, 1e-4),
    (eos80.dens, (35.0, 25.0, 10000.0), 1062.53817, 1e-4),
    (eos80.dens, (35.0, 5.0, 10000.0), 1069.48914, 1e-4),
    (eos80.adiabatic_gradient, (40.0, 40.0, 10000.0), 3.255976e-4, 1e-9),
    (eos80.ptmp, (40.0, 40.0, 10000.0, 0.0), 36.89073, 1e-4),
])
def test_eos80_unesco_check_values(fn, args, expect, tol):
    assert float(fn(*args)) == pytest.approx(expect, abs=tol)


def test_oxygen_conversion_is_sensible():
    # 5 ml/l at S=34, T=5, 0 dbar: sigma-theta ~ 27 -> ~217 umol/kg
    v = float(eos80.oxygen_mll_to_umolkg(5.0, 34.0, 5.0, 0.0))
    assert 215 < v < 220


# ------------------------------------------------------------------ Winkler reader vs the notebook pandas call
@need_o2("winkler_xls")
def test_winkler_rows_equal_notebook_read_excel():
    theirs = pd.read_excel(p("winkler_xls"), sheet_name="Winkler Calculations", usecols="J,AM", skiprows=15, nrows=16,
                           header=None, names=["Depth_wink", "O2_wink"])
    mine = ox.read_winkler(p("winkler_xls"), O["cast"])
    np.testing.assert_array_equal(mine["depth_m"].to_numpy(), theirs["Depth_wink"].to_numpy())
    np.testing.assert_allclose(mine["o2_winkler_umolkg"], theirs["O2_wink"], rtol=0, atol=0)


def test_missing_cast_in_winkler_is_an_explicit_error():
    if not p("winkler_xls").is_file():
        pytest.skip("winkler workbook not reachable")
    with pytest.raises(ValueError, match="no Winkler rows"):
        ox.read_winkler(p("winkler_xls"), "CTD-999")


# ------------------------------------------------------------------ T1 reproduction
@need_o2(*ALL)
def test_t1_gains_equal_notebook_functions_on_the_same_inputs(tmp_path):
    ctd, _ = seabird.read_cnv(p("reference_cnv"))
    ctd["o2_ctd_umolkg"] = ox.ctd_o2_umolkg(ctd)
    wink = ox.read_winkler(p("winkler_xls"), O["cast"])
    d = ox.read_dofsta_nc(p("dofsta_nc"))
    w0, w1 = (pd.Timestamp(O["notebook_window_utc"][k]) for k in ("start", "end"))

    # --- the notebook code, unmodified, fed our CTD file (with our umol/kg column under its column name) and the window crop
    df_ctd = M.read_ctd_cnv(str(p("reference_cnv")))
    df_ctd["sbox0Mm/Kg"] = ctd["o2_ctd_umolkg"].to_numpy()
    df_wink = pd.DataFrame({"Depth_wink": wink["depth_m"], "O2_wink": wink["o2_winkler_umolkg"]})
    gain_ctd, df_cal = M.calibrate_ctd_to_winkler(df_wink, df_ctd)
    with xr.open_dataset(p("dofsta_nc")) as ds:
        t = pd.to_datetime(ds["time"].values).tz_localize("UTC")
        sub = ds.isel({ds["depth"].dims[0]: np.flatnonzero((t >= w0) & (t <= w1))}).load()
    crop = tmp_path / "dofsta_window.nc"
    sub.to_netcdf(crop)
    asc, binned = M.load_dofsta_ascending(str(crop))
    gain_dofsta = M.dofsta_gain(binned, df_cal)

    # --- ours
    win = d[(d.time >= w0) & (d.time <= w1)].reset_index(drop=True)
    sel = win[profiler.ascending_mask(win)]
    g = ox.winkler_gain(wink["depth_m"], wink["o2_winkler_umolkg"], ctd["prDM"], ctd["o2_ctd_umolkg"])
    b = cal.bin_and_smooth(sel["depth_m"], {"o2_umolkg": sel["o2_umolkg"]}, smooth=())
    ref = ctd.assign(c=ctd["o2_ctd_umolkg"] * g.gain)
    r = cal.estimate_correction(b, ref["prDM"], ref["c"], col="o2_umolkg", kind="multiplicative")

    assert g.gain == pytest.approx(gain_ctd, abs=1e-12)
    assert r.value == pytest.approx(gain_dofsta, abs=1e-12)
    t1_mine = profiler.mean_time(sel["time"])
    assert abs((t1_mine - pd.Timestamp(asc["time"].mean().values, tz="UTC")).total_seconds()) < 1e-3

    # --- the values printed in the notebook (its .cnv had a ready-made umol/kg column; ours is converted from ml/l)
    assert gain_ctd == pytest.approx(1.0324950673418702, abs=2e-5)
    assert gain_dofsta == pytest.approx(1.3087659712051245, abs=2e-5)


@need_o2(*ALL)
def test_multiplicative_kind_used_and_gain_applies_as_raw_times_gain():
    assert CFG["parameters"]["oxygen"]["correction_kind"] == "multiplicative"
    from rca_sal import correction
    assert correction.apply("multiplicative", 100.0, 1.3) == pytest.approx(130.0)


@pytest.mark.parametrize("flag,bits", [("*0000000000000100", {2}), ("*0000000010000001", {0, 7}), ("-9999999", None), (-9999999, None)])
def test_summary_flag_bits(flag, bits):
    assert ox.summary_flag_bits(flag) == bits


@pytest.mark.parametrize("o2,niskin,code", [({2}, {2}, 2.0), ({4}, {2}, 4.0), ({3}, {2}, 3.0), ({2}, {3}, 3.0), ({2, 7}, {2}, 2.0)])
def test_bottle_flag_from_summary_flags(o2, niskin, code):
    assert ox._bottle_flag(o2, niskin) == code
    assert np.isnan(ox._bottle_flag(None, {2}))


def test_discrete_summary_oxygen_is_converted_with_the_bottle_ctd_values(tmp_path):
    cols = list(ox.SUMMARY_COLS.values())
    row = dict(zip(cols, ["CTD-001", 3, "*0000000000000100", 100.0, 99.0, 33.0, 8.0, 5.0, "*0000000000000100", "*0000000000001000"]))
    miss = {**row, ox.SUMMARY_COLS["o2_ml_l"]: -9999999.0}
    other = {**row, ox.SUMMARY_COLS["cast"]: "CTD-002"}
    p = tmp_path / "s.csv"
    pd.DataFrame([row, miss, other]).to_csv(p, index=False)
    w = ox.read_discrete_oxygen(p, "CTD-001")
    assert len(w) == 1 and w.loc[0, "bottle_flag"] == 2.0 and w.loc[0, "depth_m"] == 99.0
    assert w.loc[0, "o2_winkler_umolkg"] == pytest.approx(float(eos80.oxygen_mll_to_umolkg(5.0, 33.0, 8.0, 100.0)), rel=1e-12)
    with pytest.raises(ValueError, match="no discrete oxygen"):
        ox.read_discrete_oxygen(p, "CTD-009")
