"""Shallow-profiler SUNA nitrate against the discrete nitrate of a CTD cast (OOI BGC Best Practices guide v1.1.1, chapter 3).

Sensor value: OOI L2 salinity_corrected_nitrate (TCSS algorithm, Sakamoto et al. 2009) in umol/L, with the profiler CTD temperature,
salinity and depth that OOINet merges into the stream.

Cleaning (guide 3.5.2):
  - samples inside an OOI annotation with exclusion_flag are dropped (rca_sal.ooi_qc);
  - spike test: x_i is removed when |x_i - median(x_{i-k..i+k})| > threshold, the running median taken within one profile;
  - negative concentrations are counted and reported, not removed.

Matchup (guide 3.6): the SUNA profile closest in time to the cast among those spanning at least min_profile_span_m ('full'). Each profile
is binned in depth (median per bin) and the sensor value at a bottle is interpolated linearly at the bottle's CTD temperature (recommended)
or depth, only inside the profile's range. A record with a smaller span was taken with the profiler parked ('parked'): its median is one
point, compared with the bottle profile interpolated at the record's temperature or depth (one matchup).

Discrete-sample quality control (guide 3.5.3): a matchup is an outlier when |(N_bottle - N_sensor) - median(N_bottle - N_sensor)|
exceeds bottle_outlier_um; outliers are reported and left out of the statistics.

Correction (guide 3.5.3, offset as a step function):
  offset  d = median_k(N_bottle,k - N_sensor,k) over the matchups that are not outliers,   N_corrected = N_sensor + d.
Also reported: RMSE before and after, and the least-squares fit N_sensor = a + b * N_bottle with R^2 (guide figure 3.4).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .instruments import ACCEPTED_BIT, BAD_NISKIN_BITS, BAD_SAMPLE_BITS
from .oxygen import SUMMARY_MISSING, summary_flag_bits

NC_VARIABLES = {"nitrate": "salinity_corrected_nitrate", "nitrate_uncorrected": "nitrate_concentration", "temperature": "sea_water_temperature",
                "salinity": "sea_water_practical_salinity", "pressure": "int_ctd_pressure", "depth": "depth"}


def read_nutnr_nc(paths) -> pd.DataFrame:
    """time, deployment (from the OOINet file name deploymentNNNN_...) and the NC_VARIABLES columns of one or more nutnr_a_sample files;
    attrs['nitrate_units'] from the files (they must agree). A request that spans an instrument exchange returns one file per deployment."""
    import xarray as xr
    paths = [paths] if isinstance(paths, (str, Path)) else list(paths)
    frames, units = [], set()
    for path in paths:
        with xr.open_dataset(path) as ds:
            units.add(ds[NC_VARIABLES["nitrate"]].attrs.get("units"))
            df = ds[["time", *NC_VARIABLES.values()]].to_dataframe().reset_index(drop=True)
        m = re.search(r"deployment(\d{4})_", Path(path).name)
        frames.append(df.assign(deployment=int(m.group(1)) if m else -1))
    if len(units) != 1:
        raise ValueError(f"nitrate files disagree on units: {sorted(map(str, units))}")
    df = pd.concat(frames, ignore_index=True).rename(columns={v: k for k, v in NC_VARIABLES.items()})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.dropna(subset=["time", "nitrate"]).sort_values("time").reset_index(drop=True)
    df.attrs["nitrate_units"] = units.pop()
    return df


def label_profiles(d: pd.DataFrame, gap_min: float, min_samples: int) -> pd.DataFrame:
    """Add profile_id (1, 2, ...): samples separated by more than gap_min minutes, or from another deployment, start a new profile;
    profiles with fewer than min_samples get 0."""
    new = d["time"].diff() > pd.Timedelta(minutes=gap_min)
    if "deployment" in d:
        new |= d["deployment"].diff().fillna(0).ne(0)
    pid = new.cumsum().to_numpy() + 1
    size = pd.Series(pid).map(pd.Series(pid).value_counts())
    pid = np.where(size.to_numpy() >= min_samples, pid, 0)
    keep = sorted(set(pid) - {0})
    renum = {old: i + 1 for i, old in enumerate(keep)}
    return d.assign(profile_id=[renum.get(p, 0) for p in pid])


def spike_mask(x: pd.Series, profile: pd.Series, window: int, threshold: float) -> np.ndarray:
    """True for samples that differ from the centred running median (window samples, within the same profile) by more than threshold."""
    med = x.groupby(profile.to_numpy()).transform(lambda s: s.rolling(window, center=True, min_periods=1).median())
    return (x - med).abs().to_numpy() > threshold


def profile_summary(d: pd.DataFrame, cast_time: pd.Timestamp) -> pd.DataFrame:
    g = d[d["profile_id"] > 0].groupby("profile_id")
    s = g.agg(start_utc=("time", "min"), end_utc=("time", "max"), n_samples=("nitrate", "size"),
              deployment=("deployment", "first") if "deployment" in d else ("nitrate", lambda x: -1),
              depth_min_m=("depth", "min"), depth_max_m=("depth", "max"), temperature_min=("temperature", "min"), temperature_max=("temperature", "max"))
    s["mean_time_utc"] = g["time"].apply(lambda t: t.min() + (t - t.min()).mean())
    s["hours_from_cast"] = (s["mean_time_utc"] - cast_time).dt.total_seconds() / 3600
    return s


def bin_profile(p: pd.DataFrame, bin_size: float) -> pd.DataFrame:
    """Median nitrate, temperature and depth per depth bin of one profile (bins (k*b, (k+1)*b], labelled by mid-point)."""
    k = np.ceil(p["depth"].to_numpy(float) / bin_size)
    b = p.assign(depth_bin_m=(k - 0.5) * bin_size).groupby("depth_bin_m")[["nitrate", "temperature", "depth"]].median()
    return b.reset_index()


def sensor_at(binned: pd.DataFrame, x_col: str, x) -> np.ndarray:
    """Sensor nitrate interpolated at x on the binned profile's x_col (temperature or depth); NaN outside its range."""
    b = binned.dropna(subset=[x_col, "nitrate"]).sort_values(x_col)
    xs, ys = b[x_col].to_numpy(float), b["nitrate"].to_numpy(float)
    x = np.asarray(x, float)
    out = np.interp(x, xs, ys) if len(xs) else np.full(x.shape, np.nan)
    return np.where((x >= xs.min()) & (x <= xs.max()), out, np.nan) if len(xs) else out


def bottle_at(bottles: pd.DataFrame, x_col: str, x: float) -> float:
    """Bottle nitrate interpolated linearly at x on the bottles' x_col (temperature or depth_m); NaN outside the bottles' range."""
    b = bottles.dropna(subset=[x_col, "nitrate_bottle"]).sort_values(x_col)
    xs, ys = b[x_col].to_numpy(float), b["nitrate_bottle"].to_numpy(float)
    if len(xs) == 0 or not (xs.min() <= x <= xs.max()):
        return float("nan")
    return float(np.interp(x, xs, ys))


def read_discrete_nitrate(path, cast: str) -> pd.DataFrame:
    """Discrete nitrate of one cast from an OOI discrete summary CSV, one row per Niskin (mean of usable replicates).

    A replicate is usable with a value and a nutrient flag that is either absent (kept as 'unflagged') or acceptable (bit 2) without
    questionable/bad bits; the Niskin flag must be acceptable. Nitrite is carried along for reference.
    """
    d = pd.read_csv(path, low_memory=False)
    d = d[d["Cast"].astype(str) == str(cast)]
    if d.empty:
        raise ValueError(f"cast {cast!r} is not in {path}")
    num = lambda c: pd.to_numeric(d[c], errors="coerce").where(lambda s: s != SUMMARY_MISSING)  # noqa: E731
    bits = d["Discrete Nutrients Flag"].map(summary_flag_bits)
    nisk = d["Niskin Flag"].map(summary_flag_bits)
    status = np.where(num("Discrete Nitrate [uM]").isna(), "none",
                      np.where(bits.isna(), "unflagged",
                               np.where(bits.map(lambda b: b is not None and ACCEPTED_BIT in b and not (b & BAD_SAMPLE_BITS)), "accepted", "flagged")))
    rows = pd.DataFrame({"niskin": d["Niskin/Bottle Position"].astype(str), "time_utc": pd.to_datetime(d["CTD Bottle Closure Time [UTC]"], utc=True, errors="coerce"),
                         "pressure_dbar": num("CTD Pressure [db]"), "depth_m": num("CTD Depth [m]"), "temperature": num("CTD Temperature 1 [deg C]"),
                         "nitrate_bottle": num("Discrete Nitrate [uM]"), "nitrite_bottle": num("Discrete Nitrite [uM]"), "status": status,
                         "niskin_ok": nisk.map(lambda b: b is not None and ACCEPTED_BIT in b and not (b & BAD_NISKIN_BITS)).to_numpy()})
    rows["usable"] = rows["status"].isin(("accepted", "unflagged")) & rows["niskin_ok"]
    g = rows.groupby("niskin", sort=False)
    out = g.agg(time_utc=("time_utc", "first"), pressure_dbar=("pressure_dbar", "first"), depth_m=("depth_m", "first"),
                temperature=("temperature", "first"), nitrite_bottle=("nitrite_bottle", "mean"), niskin_ok=("niskin_ok", "first"),
                status=("status", lambda s: next((k for k in ("accepted", "unflagged", "flagged", "none") if k in set(s)), "none"))).reset_index()
    out["nitrate_bottle"] = g.apply(lambda x: x.loc[x["usable"], "nitrate_bottle"].mean(), include_groups=False).to_numpy()
    out["n_replicates"] = g.apply(lambda x: int(x["usable"].sum()), include_groups=False).to_numpy()
    out["usable"] = out["n_replicates"] > 0
    return out.sort_values("depth_m").reset_index(drop=True)


def outliers(bottle, sensor, threshold: float) -> np.ndarray:
    """True where |(bottle - sensor) - median(bottle - sensor)| > threshold (NaN pairs are not outliers)."""
    diff = np.asarray(bottle, float) - np.asarray(sensor, float)
    ok = np.isfinite(diff)
    if not ok.any():
        return np.zeros(diff.shape, bool)
    return ok & (np.abs(diff - np.median(diff[ok])) > threshold)


def matchup_stats(bottle, sensor, accuracy: float, outlier=None) -> dict:
    """Offset, RMSE before/after and the fit sensor = a + b * bottle over the finite pairs that are not outliers."""
    b, s = np.asarray(bottle, float), np.asarray(sensor, float)
    out_mask = np.zeros(b.shape, bool) if outlier is None else np.asarray(outlier, bool)
    ok = np.isfinite(b) & np.isfinite(s) & ~out_mask
    b, s = b[ok], s[ok]
    n = int(ok.sum())
    if n == 0:
        return {"n_matchups": 0, "n_outliers": int(out_mask.sum())}
    diff = b - s
    off = float(np.median(diff))
    out = {"n_matchups": n, "n_outliers": int(out_mask.sum()), "offset_um": off, "offset_iqr_um": float(np.subtract(*np.percentile(diff, [75, 25]))),
           "mean_diff_um": float(diff.mean()), "rmse_before_um": float(np.sqrt(np.mean(diff ** 2))),
           "rmse_after_um": float(np.sqrt(np.mean((diff - off) ** 2))), "max_abs_diff_um": float(np.abs(diff).max()),
           "within_accuracy": bool(abs(off) <= accuracy)}
    if n >= 3 and np.ptp(b) > 0:
        slope, intercept = np.polyfit(b, s, 1)
        r = np.corrcoef(b, s)[0, 1]
        out.update({"fit_slope": float(slope), "fit_intercept_um": float(intercept), "fit_r2": float(r * r)})
    return out
