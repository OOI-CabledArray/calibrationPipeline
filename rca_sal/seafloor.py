"""Salinity offset and oxygen gain of fixed seafloor CTDs from the bottle samples taken at them.

For each bottle, the instrument value is the median of the instrument's samples within +/- half_window of the bottle closure time.
  salinity offset   d_k = S_bottle,k - S_inst,k            (bottle: Autosal, mean of replicates)
  oxygen gain       g_k = O_bottle,k / O_inst,k            (bottle: Winkler converted to umol/kg, mean of replicates)
The value of a deployment is the median over its bottles, with the interquartile range. The oxygen of the integrated optode is
corrected_dissolved_oxygen (umol/kg). Bottles without instrument samples in the window have n_samples = 0 and no value.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import eos80, ooi_qc

USABLE = ("accepted", "unflagged")                 # discrete-sample statuses used (see instruments._status)
INSTRUMENT_VARIABLES = {"salinity": "sea_water_practical_salinity", "oxygen": "corrected_dissolved_oxygen",
                        "pressure": "sea_water_pressure", "temperature": "sea_water_temperature"}


def read_instrument(path) -> pd.DataFrame:
    """time, salinity, oxygen, pressure, temperature of one OOINet CTDPFB request file."""
    import xarray as xr
    with xr.open_dataset(path) as ds:
        df = ds[["time", *INSTRUMENT_VARIABLES.values()]].to_dataframe().reset_index(drop=True)
    df = df.rename(columns={v: k for k, v in INSTRUMENT_VARIABLES.items()})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df[["time", *INSTRUMENT_VARIABLES]].dropna(subset=["time"]).sort_values("time").reset_index(drop=True)


def bottle_table(matches: pd.DataFrame) -> pd.DataFrame:
    """One row per (instrument, bottle) with the reference values: Autosal salinity and Winkler oxygen in umol/kg.

    Only replicates with a usable status enter the mean. The summary's CTD salinity, temperature and pressure at the closure give the
    mL/L to umol/kg conversion (eos80.oxygen_mll_to_umolkg). Bottles with a Niskin problem are dropped.
    """
    m = matches[matches["niskin_ok"]].copy()
    for c in ("CTD Salinity 1 [psu]", "CTD Temperature 1 [deg C]", "CTD Pressure [db]"):
        m[c] = pd.to_numeric(m[c], errors="coerce").where(lambda s: s > -9e6)
    m["o2_umolkg"] = eos80.oxygen_mll_to_umolkg(m["Discrete Oxygen [mL/L]"], m["CTD Salinity 1 [psu]"], m["CTD Temperature 1 [deg C]"], m["CTD Pressure [db]"])
    m["sal_ref"] = m["Discrete Salinity [psu]"].where(m["salinity_status"].isin(USABLE))
    m["o2_ref"] = m["o2_umolkg"].where(m["oxygen_status"].isin(USABLE))
    g = m.groupby(["rd", "bottle"], sort=True)
    return g.agg(cruise=("Cruise", "first"), cast=("Cast", "first"), time=("time", "first"), deployment=("deployment", "first"),
                 timing=("timing", "first"), rov=("rov", "first"), distance_km=("distance_km", "first"),
                 depth_difference_m=("depth_difference_m", "first"), gap_hours=("gap_hours", "first"),
                 label_names_instrument=("label_names_instrument", "first"),
                 salinity_bottle=("sal_ref", "mean"), n_salinity_replicates=("sal_ref", "count"),
                 oxygen_bottle_umolkg=("o2_ref", "mean"), n_oxygen_replicates=("o2_ref", "count"),
                 ctd_salinity_at_bottle=("CTD Salinity 1 [psu]", "mean")).reset_index()


def window_values(inst: pd.DataFrame, times, half_window: pd.Timedelta, annotations=(), refdes=None) -> pd.DataFrame:
    """Median, interquartile range and count of the instrument variables within +/- half_window of each time.

    Samples inside an applicable annotation with exclusion_flag are dropped first.
    """
    inst, n_excl = ooi_qc.drop_excluded(ooi_qc.mark_exclusions(inst, list(annotations), refdes))
    t = inst["time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    rows = []
    for tb in pd.to_datetime(pd.Series(times), utc=True):
        tb = tb.tz_localize(None)
        lo = np.searchsorted(t, (tb - half_window).to_datetime64())
        hi = np.searchsorted(t, (tb + half_window).to_datetime64(), side="right")
        w = inst.iloc[lo:hi]
        row = {"n_samples": len(w), "n_annotation_excluded": n_excl}
        for k in INSTRUMENT_VARIABLES:
            row[f"inst_{k}"] = float(w[k].median()) if len(w) else np.nan
            row[f"inst_{k}_iqr"] = float(w[k].quantile(0.75) - w[k].quantile(0.25)) if len(w) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def compare(bottles: pd.DataFrame, values: pd.DataFrame) -> pd.DataFrame:
    """Bottle table with the instrument values and d = S_bottle - S_inst, g = O_bottle / O_inst per bottle."""
    b = pd.concat([bottles.reset_index(drop=True), values.reset_index(drop=True)], axis=1)
    b["salinity_offset"] = b["salinity_bottle"] - b["inst_salinity"]
    b["ctd_minus_instrument"] = b["ctd_salinity_at_bottle"] - b["inst_salinity"]
    b["oxygen_gain"] = b["oxygen_bottle_umolkg"] / b["inst_oxygen"]
    return b


def deployment_summary(b: pd.DataFrame) -> pd.DataFrame:
    """Per instrument and deployment: bottles used, median and interquartile range of the offset and the gain."""
    def stats(x: pd.Series, name: str) -> dict:
        has = len(x) > 0
        return {f"n_{name}": len(x), f"{name}_median": float(x.median()) if has else np.nan,
                f"{name}_iqr": float(x.quantile(0.75) - x.quantile(0.25)) if has else np.nan,
                f"{name}_min": float(x.min()) if has else np.nan, f"{name}_max": float(x.max()) if has else np.nan}
    rows = []
    for (rd, dep), g in b.groupby(["rd", "deployment"], sort=True):
        rows.append({"reference_designator": rd, "deployment": int(dep), "first_bottle": g["time"].min(), "last_bottle": g["time"].max(),
                     "cruises": ",".join(sorted(g["cruise"].unique())),
                     **stats(g["salinity_offset"].dropna(), "salinity_offset"), **stats(g["oxygen_gain"].dropna(), "oxygen_gain")})
    return pd.DataFrame(rows)
