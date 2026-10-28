"""Shallow-profiler CTD (CTDPFA) loading and upcast labelling.

read_ooinet_nc reads the NetCDF4 file of an OOINet request, read_ooinet_csv the CSV export; both return the same table.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# OOINet parameter names -> pipeline names. First match wins.
COLUMN_MAP = {
    "time": ["time"],
    "depth_m": ["depth"],
    "pressure_dbar": ["seawater_pressure", "sea_water_pressure", "ctdpf_sbe43_sample-seawater_pressure"],
    "temperature_c": ["seawater_temperature", "sea_water_temperature"],
    "conductivity_sm": ["seawater_conductivity", "sea_water_electrical_conductivity"],
    "salinity_psu": ["practical_salinity", "sea_water_practical_salinity"],
}


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    used = {}
    for name, candidates in COLUMN_MAP.items():
        col = next((c for c in candidates if c in df.columns), None)
        if col is None:
            raise KeyError(f"profiler file has no column for {name}; looked for {candidates}")
        out[name] = df[col]
        used[name] = col
    if pd.api.types.is_numeric_dtype(out["time"]):
        # OOINet CSV exports time as seconds since 1900-01-01 (NTP epoch)
        out["time"] = pd.Timestamp("1900-01-01", tz="UTC") + pd.to_timedelta(out["time"], unit="s")
    else:
        out["time"] = pd.to_datetime(out["time"], utc=True)
    out = out.sort_values("time").reset_index(drop=True)
    out.attrs["source_columns"] = used
    return out


def read_ooinet_nc(path: str | Path) -> pd.DataFrame:
    import xarray as xr  # imported here so the rest of the package works without it
    with xr.open_dataset(path) as ds:
        dim = ds["depth"].dims[0]          # 'obs' in some files, 'time' in others
        keep = [v for v in ds.variables if ds[v].dims == (dim,)]
        df = ds[keep].to_dataframe().reset_index()
    return _tidy(df)


def read_ooinet_csv(paths) -> pd.DataFrame:
    if isinstance(paths, (str, Path)):
        paths = [paths]
    return _tidy(pd.concat([pd.read_csv(p, low_memory=False) for p in paths], ignore_index=True))


def read_profiler(paths) -> pd.DataFrame:
    paths = [paths] if isinstance(paths, (str, Path)) else list(paths)
    if all(str(p).lower().endswith(".nc") for p in paths):
        return pd.concat([read_ooinet_nc(p) for p in paths], ignore_index=True).sort_values("time").reset_index(drop=True)
    return read_ooinet_csv(paths)


# ---------------------------------------------------------------- upcasts

@dataclass
class UpcastParams:
    smooth_s: float = 15.0        # rolling-median window for depth before differentiating
    w_ascend: float = -0.03       # m/s; depth decreasing faster than this = ascending
    max_gap_s: float = 30.0       # bridge short pauses inside one ascent
    min_span_m: float = 100.0     # an upcast must cover at least this much water column


def label_upcasts(df: pd.DataFrame, p: UpcastParams = UpcastParams()) -> pd.DataFrame:
    """Add 'upcast_id' (int, -1 = not part of a full upcast) and 'w_ms' (vertical speed, +down).

    Ascending samples (smoothed dz/dt below p.w_ascend) are grouped into runs; consecutive ascending
    samples further apart than p.max_gap_s start a new run; a run is kept as an upcast only if it spans
    at least p.min_span_m of depth. Upcasts are numbered 0.. in time order.
    """
    df = df.copy()
    n = len(df)
    df["upcast_id"] = -1
    df["w_ms"] = np.nan
    if n < 3:
        return df
    t = (df["time"] - df["time"].iloc[0]).dt.total_seconds().to_numpy()
    if np.any(np.diff(t) <= 0):
        raise ValueError("profiler times must be strictly increasing (sort and de-duplicate first)")
    dt = float(np.median(np.diff(t)))
    win = max(3, int(round(p.smooth_s / dt)) | 1)
    z = pd.Series(df["depth_m"].to_numpy(dtype=float)).rolling(win, center=True, min_periods=1).median().to_numpy()
    w = np.gradient(z, t)
    df["w_ms"] = w
    idx = np.flatnonzero(np.isfinite(w) & (w < p.w_ascend))
    ids = np.full(n, -1)
    if idx.size:
        breaks = np.flatnonzero(np.diff(t[idx]) > p.max_gap_s) + 1
        depth = df["depth_m"].to_numpy(dtype=float)
        k = 0
        for seg in np.split(idx, breaks):
            a, b = seg[0], seg[-1]
            if np.nanmax(depth[a:b + 1]) - np.nanmin(depth[a:b + 1]) >= p.min_span_m:
                ids[a:b + 1] = k
                k += 1
    df["upcast_id"] = ids
    return df


def ascending_mask(df: pd.DataFrame) -> np.ndarray:
    """A sample is ascending if depth decreased since the previous sample."""
    d = df["depth_m"].to_numpy()
    m = np.zeros(len(d), dtype=bool)
    m[1:] = np.diff(d) < 0
    return m


def mean_time(times) -> pd.Timestamp:
    """Mean of a time series."""
    t = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    return pd.Timestamp(int(t.asi8.mean()), unit=t.unit if hasattr(t, "unit") else "ns", tz="UTC")


def upcast_summary(df: pd.DataFrame) -> pd.DataFrame:
    g = df[df["upcast_id"] >= 0].groupby("upcast_id")
    return pd.DataFrame({
        "start": g["time"].min(), "end": g["time"].max(),
        "mid_time": g["time"].apply(lambda s: s.min() + (s.max() - s.min()) / 2),
        "mean_time": g["time"].apply(mean_time),
        "depth_min": g["depth_m"].min(), "depth_max": g["depth_m"].max(), "n": g.size(),
        "mean_speed_ms": g["w_ms"].mean(),
    })
