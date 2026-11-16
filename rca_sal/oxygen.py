"""DOFSTA oxygen gain from Winkler titrations through the shipboard SBE43.

    g_ctd    = median_i(W_i / O_ctd(z_i))            Winkler value W_i at depth z_i, CTD oxygen interpolated to z_i
    O_cal    = g_ctd * O_ctd                          calibrated CTD profile
    g_dofsta = median_k(O_cal(z_k) / O_dofsta,k)      over depth bins k
    corrected = g_dofsta * raw
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import calibrate as cal
from . import correction, eos80

CTD_O2_MLL = "sbeox0ML/L"


def _col(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _sheet(path, sheet):
    """(cell(row, col) -> value or '', n_rows) for an .xls (xlrd) or .xlsx/.xlsm (openpyxl) workbook; zero-based."""
    if str(path).lower().endswith(".xls"):
        import xlrd
        ws = xlrd.open_workbook(str(path)).sheet_by_name(sheet)
        return (lambda r, c: ws.cell_value(r, c) if c < ws.ncols else ""), ws.nrows
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True, read_only=True)[sheet]
    grid = [list(row) for row in ws.iter_rows(values_only=True)]
    return (lambda r, c: ("" if grid[r][c] is None else grid[r][c]) if c < len(grid[r]) else ""), len(grid)


def read_winkler(path: str | Path, cast: str, sheet: str = "Winkler Calculations") -> pd.DataFrame:
    """Winkler titrations for one CTD cast from the lab's calculation workbook (.xls or .xlsx).

    Columns follow the sheet: B cast, G Niskin, H bottle quality flag (3 or 4 = bad), J depth (m), K pressure
    (dbar), AM [O2] umol/kg. Rows are matched on the cast name in column B, so duplicates (two titrations per
    Niskin) all come through.
    """
    cell, nrows = _sheet(path, sheet)
    hdr = next(r for r in range(nrows) if str(cell(r, _col("B"))).strip() == "Cast #")
    rows = []
    for r in range(hdr + 2, nrows):
        if str(cell(r, _col("B"))).strip() != cast:
            continue
        o2 = cell(r, _col("AM"))
        if o2 in ("", None):
            continue
        rows.append({"sheet_row": r + 1, "cast": cast, "niskin": cell(r, _col("G")),
                     "bottle_flag": cell(r, _col("H")), "depth_m": cell(r, _col("J")),
                     "pressure_dbar": cell(r, _col("K")), "o2_winkler_umolkg": float(o2)})
    if not rows:
        raise ValueError(f"no Winkler rows for cast {cast!r} in {path}")
    return pd.DataFrame(rows)


SUMMARY_MISSING = -9999999
SUMMARY_COLS = {"cast": "Cast", "niskin": "Niskin/Bottle Position", "niskin_flag": "Niskin Flag", "pressure_dbar": "CTD Pressure [db]",
                "depth_m": "CTD Depth [m]", "ctd_sal": "CTD Salinity 1 [psu]", "ctd_t90": "CTD Temperature 1 [deg C]",
                "o2_ml_l": "Discrete Oxygen [mL/L]", "o2_flag": "Discrete Oxygen Flag", "o2_replicate_flag": "Discrete Oxygen Replicate Flag"}


def summary_flag_bits(flag) -> set[int] | None:
    """Bits set in an OOI discrete-summary flag ('*' + 16 binary digits, bit 0 rightmost; summaryBuilder/flags.csv); None if missing."""
    s = str(flag).strip()
    if not s.startswith("*"):
        return None
    return {i for i, ch in enumerate(reversed(s[1:])) if ch == "1"}


def _bottle_flag(o2_bits: set[int] | None, niskin_bits: set[int] | None) -> float:
    """Workbook-style quality code from the summary flags: 4 = bad oxygen measurement, 3 = questionable measurement or a Niskin
    problem (leaking, ran out of water, vent open, misfire, unknown), 2 = acceptable; NaN when the oxygen flag is missing."""
    if o2_bits is None:
        return float("nan")
    if 4 in o2_bits:
        return 4.0
    if 3 in o2_bits or (niskin_bits or set()) & {3, 4, 5, 6, 7}:
        return 3.0
    return 2.0


def read_discrete_oxygen(path: str | Path, cast: str, ctd_file: str | None = None) -> pd.DataFrame:
    """Winkler oxygen for one CTD cast from a cruise's OOI discrete summary CSV (OOI-CabledArray/discreteSummaries).

    The summary gives Discrete Oxygen in mL/L; it is converted to umol/kg with the Sea-Bird formula (mL/L x 44660 / (sigma-theta
    + 1000)) using the CTD salinity 1, temperature 1 and pressure at the bottle closure, all from the same row. Columns match
    read_winkler(); the summary's flags are kept as given and decoded into bottle_flag (see _bottle_flag).
    A cast label can cover two CTD files (a restarted cast, e.g. 'CTD-006' and 'CTD-006a'): with ctd_file, only the rows of that CTD file are kept, unless no row carries that file name.
    """
    d = pd.read_csv(path)
    miss = [c for c in SUMMARY_COLS.values() if c not in d.columns]
    if miss:
        raise KeyError(f"discrete summary {path} lacks columns {miss}")
    d = d[d[SUMMARY_COLS["cast"]].astype(str).str.strip() == cast]
    if ctd_file is not None and "CTD File" in d.columns and (d["CTD File"].astype(str).str.strip() == ctd_file).any():
        d = d[d["CTD File"].astype(str).str.strip() == ctd_file]
    d = d.rename(columns={v: k for k, v in SUMMARY_COLS.items()})
    num = ["niskin", "pressure_dbar", "depth_m", "ctd_sal", "ctd_t90", "o2_ml_l"]
    d[num] = d[num].apply(pd.to_numeric, errors="coerce").mask(lambda x: x <= SUMMARY_MISSING)
    d = d[d["o2_ml_l"].notna()]
    if d.empty:
        raise ValueError(f"no discrete oxygen for cast {cast!r} in {path}")
    o2b, nkb = d["o2_flag"].map(summary_flag_bits), d["niskin_flag"].map(summary_flag_bits)
    out = pd.DataFrame({
        "sheet_row": d.index.to_numpy() + 2, "cast": cast, "niskin": d["niskin"].to_numpy(),
        "bottle_flag": [_bottle_flag(a, b) for a, b in zip(o2b, nkb)],
        "depth_m": d["depth_m"].to_numpy(), "pressure_dbar": d["pressure_dbar"].to_numpy(),
        "o2_winkler_umolkg": eos80.oxygen_mll_to_umolkg(d["o2_ml_l"], d["ctd_sal"], d["ctd_t90"], d["pressure_dbar"]),
        "o2_ml_l": d["o2_ml_l"].to_numpy(), "ctd_sal_at_bottle": d["ctd_sal"].to_numpy(), "ctd_t90_at_bottle": d["ctd_t90"].to_numpy(),
        "o2_flag": d["o2_flag"].astype(str).to_numpy(), "niskin_flag": d["niskin_flag"].astype(str).to_numpy(),
        "o2_replicate_flag": d["o2_replicate_flag"].astype(str).to_numpy()})
    return out[np.isfinite(out["o2_winkler_umolkg"])].reset_index(drop=True)


def ctd_o2_umolkg(ctd: pd.DataFrame) -> pd.Series:
    """CTD SBE43 oxygen in umol/kg. Uses a ready-made column if the .cnv has one, else converts ml/l."""
    if "sbox0Mm/Kg" in ctd.columns:
        return ctd["sbox0Mm/Kg"]
    for need in (CTD_O2_MLL, "sal00", "t090C", "prDM"):
        if need not in ctd.columns:
            raise KeyError(f"CTD file lacks {need!r}; cannot derive oxygen in umol/kg")
    return pd.Series(eos80.oxygen_mll_to_umolkg(ctd[CTD_O2_MLL], ctd["sal00"], ctd["t090C"], ctd["prDM"]), index=ctd.index)


def remove_analog_offset(ctd: pd.DataFrame, volts: np.ndarray, sensors: list[dict], reference_type: str = "NotInUse",
                         max_decode_counts: int = 2) -> tuple[pd.DataFrame, dict]:
    """Remove a voltage offset common to all analog channels from the shipboard SBE43 oxygen (sbeox0ML/L).

    A channel with nothing connected (`reference_type`, normally NotInUse) reads 0 V; whatever it reads instead is an offset added
    to every analog channel. The .cnv's scans are the first scans of the .hex, which is checked by comparing the decoded oxygen
    voltage with the .cnv's sbeox0V (within `max_decode_counts` A/D counts). The SBE 43 equation is linear in the voltage, so the offset is removed scan by scan as
        O2_corrected = O2 - Soc * V_ref * oxsol * (1 + A T + B T^2 + C T^3) * exp(E P / (T + 273.15))
    with the sensor's own coefficients from the .xmlcon; the .cnv's other processing of the oxygen is kept. A ready-made
    sbox0Mm/Kg column is scaled by the same ratio.
    """
    oxy = [s for s in sensors if s["type"] == "OxygenSensor"]
    ref = [s for s in sensors if s["type"] == reference_type]
    if not oxy or "coefficients" not in oxy[0]:
        raise ValueError("the .xmlcon has no SBE 43 oxygen sensor with 2007-equation coefficients")
    if len(ref) != 1:
        raise ValueError(f"the .xmlcon has {len(ref)} {reference_type} channels; exactly one is needed as the zero reference")
    o, r, c = oxy[0], ref[0], oxy[0]["coefficients"]
    n = len(ctd)
    if volts.shape[1] < n:
        raise ValueError(f"the .hex has {volts.shape[1]} scans, fewer than the .cnv's {n}")
    v_ox, v_ref = volts[o["voltage_channel"], :n], volts[r["voltage_channel"], :n]
    diff = float(np.nanmax(np.abs(v_ox - ctd["sbeox0V"].to_numpy(float))))
    if diff > max_decode_counts * 5 / 4095:          # A/D counts of the 12-bit, 0-5 V converter
        raise ValueError(f"the .hex oxygen voltage does not match the .cnv's sbeox0V scan by scan (max difference {diff:.4f} V)")
    t, p = ctd["t090C"].to_numpy(float), ctd["prDM"].to_numpy(float)
    delta = c["Soc"] * v_ref * ctd["oxsolML/L"].to_numpy(float) * (1 + c["A"] * t + c["B"] * t**2 + c["C"] * t**3) * np.exp(c["E"] * p / (t + 273.15))
    out = ctd.copy()
    before = out[CTD_O2_MLL].to_numpy(float)
    out[CTD_O2_MLL] = before - delta
    if "sbox0Mm/Kg" in out.columns:
        out["sbox0Mm/Kg"] = out["sbox0Mm/Kg"] * np.where(before != 0, (before - delta) / before, np.nan)
    info = {"oxygen_sensor_serial": o["serial"], "oxygen_voltage_channel": o["voltage_channel"],
            "reference_channel_type": reference_type, "reference_voltage_channel": r["voltage_channel"], "n_scans": n,
            "reference_v_median": float(np.median(v_ref)), "reference_v_min": float(v_ref.min()), "reference_v_max": float(v_ref.max()),
            "decode_max_abs_diff_v": diff, "o2_removed_mll_median": float(np.nanmedian(delta)),
            "o2_mll_median_before": float(np.nanmedian(before)), "o2_mll_median_after": float(np.nanmedian(before - delta))}
    return out, info


@dataclass
class WinklerGain:
    gain: float
    n: int
    iqr: float
    ratios: np.ndarray           # one per Winkler row; NaN where the CTD profile does not reach the bottle depth (not extrapolating)
    ctd_at_winkler: np.ndarray


def winkler_gain(wink_depth, wink_o2, ctd_depth, ctd_o2, extrapolate: bool = True) -> WinklerGain:
    """Gain-only CTD calibration to Winkler: median(Winkler / CTD at the Winkler depths)."""
    at = cal.reference_at(wink_depth, ctd_depth, ctd_o2, extrapolate)
    ratio = correction.pointwise("multiplicative", at, np.asarray(wink_o2, float))   # reference / sensor
    ok = np.isfinite(ratio)
    if not ok.any():
        raise ValueError("no Winkler bottle lies within the depth range of the shipboard CTD profile")
    q75, q25 = np.percentile(ratio[ok], [75, 25])
    return WinklerGain(float(np.median(ratio[ok])), int(ok.sum()), float(q75 - q25), ratio, at)


def ctd_winkler_fit(ctd, winkler) -> dict:
    """Ship CTD oxygen against Winkler oxygen at the same bottles: median CTD - Winkler, median Winkler / CTD, and the ordinary least-squares line
    Winkler = intercept + slope x CTD with the slope's standard error, R2 and the RMSE about the line. Pairs with a missing value are left out.
    The line's values are NaN below 3 pairs or when the CTD values do not vary, since no line can be tested there."""
    x, y = np.asarray(ctd, float), np.asarray(winkler, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    out = {"n": int(x.size), "median_offset": float(np.median(x - y)) if x.size else float("nan"),
           "median_ratio": float(np.median(y[x != 0] / x[x != 0])) if (x != 0).any() else float("nan"),
           "slope": float("nan"), "slope_se": float("nan"), "intercept": float("nan"), "r2": float("nan"), "rmse": float("nan")}
    sxx = float(((x - x.mean()) ** 2).sum()) if x.size else 0.0
    if x.size < 3 or sxx == 0:
        return out
    slope = float(((x - x.mean()) * (y - y.mean())).sum() / sxx)
    intercept = float(y.mean() - slope * x.mean())
    sse = float(((y - intercept - slope * x) ** 2).sum())
    syy = float(((y - y.mean()) ** 2).sum())
    out.update(slope=slope, intercept=intercept, slope_se=float(np.sqrt(sse / (x.size - 2) / sxx)), r2=1 - sse / syy if syy > 0 else float("nan"),
               rmse=float(np.sqrt(sse / (x.size - 2))))
    return out


def within_profiler_range(winkler: pd.DataFrame, deepest_m: float) -> pd.DataFrame:
    """Winkler rows (column depth_m) no deeper than the profiler's deepest sample; deeper bottles say nothing about the profiler's range."""
    return winkler[winkler["depth_m"] <= deepest_m].reset_index(drop=True)


def read_dofsta_nc(paths) -> pd.DataFrame:
    """OOINet DOFSTA do_fast_sample NetCDF -> tidy table (time, depth, corrected O2 in umol/kg). Takes one file, or several when a request
    spans an instrument exchange (one file per deployment); their units must agree."""
    import xarray as xr
    paths = [paths] if isinstance(paths, (str, Path)) else list(paths)
    want = {"depth_m": "depth", "o2_umolkg": "corrected_dissolved_oxygen"}
    frames, units = [], set()
    for path in paths:
        with xr.open_dataset(path) as ds:
            dim = ds["depth"].dims[0]
            missing = [v for v in want.values() if v not in ds]
            if missing:
                raise KeyError(f"DOFSTA file has no {missing}")
            df = pd.DataFrame({"time": pd.to_datetime(ds["time"].values, utc=True)})
            for k, v in want.items():
                df[k] = ds[v].values.astype(float)
            units.add(ds["corrected_dissolved_oxygen"].attrs.get("units"))
            assert dim in ds["depth"].dims
        frames.append(df)
    if len(units) != 1:
        raise ValueError(f"the DOFSTA files disagree on the oxygen units: {sorted(map(str, units))}")
    df = pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)
    df.attrs["o2_units"] = units.pop()
    return df
