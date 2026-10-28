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


def read_discrete_oxygen(path: str | Path, cast: str) -> pd.DataFrame:
    """Winkler oxygen for one CTD cast from a cruise's OOI discrete summary CSV (OOI-CabledArray/discreteSummaries).

    The summary gives Discrete Oxygen in mL/L; it is converted to umol/kg with the Sea-Bird formula (mL/L x 44660 / (sigma-theta
    + 1000)) using the CTD salinity 1, temperature 1 and pressure at the bottle closure, all from the same row. Columns match
    read_winkler(); the summary's flags are kept as given and decoded into bottle_flag (see _bottle_flag).
    """
    d = pd.read_csv(path)
    miss = [c for c in SUMMARY_COLS.values() if c not in d.columns]
    if miss:
        raise KeyError(f"discrete summary {path} lacks columns {miss}")
    d = d[d[SUMMARY_COLS["cast"]].astype(str).str.strip() == cast].rename(columns={v: k for k, v in SUMMARY_COLS.items()})
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


def read_dofsta_nc(path: str | Path) -> pd.DataFrame:
    """OOINet DOFSTA do_fast_sample NetCDF -> tidy table (time, depth, corrected O2 in umol/kg)."""
    import xarray as xr
    want = {"depth_m": "depth", "o2_umolkg": "corrected_dissolved_oxygen"}
    with xr.open_dataset(path) as ds:
        dim = ds["depth"].dims[0]
        missing = [v for v in want.values() if v not in ds]
        if missing:
            raise KeyError(f"DOFSTA file has no {missing}")
        df = pd.DataFrame({"time": pd.to_datetime(ds["time"].values, utc=True)})
        for k, v in want.items():
            df[k] = ds[v].values.astype(float)
        units = ds["corrected_dissolved_oxygen"].attrs.get("units")
        assert dim in ds["depth"].dims
    df = df.sort_values("time").reset_index(drop=True)
    df.attrs["o2_units"] = units
    return df
