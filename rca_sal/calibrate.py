"""Profiler versus reference CTD: salinity offset and AN31 conductivity slope.

Profiler samples are averaged in depth bins, salinity is smoothed with a centred rolling mean, and the reference profile is interpolated
linearly onto the bin mid-points z_k. With s_k the binned profiler value and r_k the reference value:
    offset = median_k(s_k - r_k),   RMSE = sqrt(mean_k((s_k - r_k)^2)).
The AN31 slope is computed on the same bins (an31_slope).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd

from . import correction
from .pss78 import conductivity_from_salinity, salinity_from_conductivity


def bin_and_smooth(depth, values: dict, bin_size: float = 1.0, smooth_window: int = 5,
                   smooth: tuple = ("salinity_psu",)) -> pd.DataFrame:
    """Average each variable in depth bins (a, b], labelled by the bin mid-point; empty bins are NaN. Variables named in `smooth`
    get a centred rolling mean over `smooth_window` bins (min_periods=1). Equivalent to xarray groupby_bins(...).mean()
    (tests/test_binning.py).
    """
    depth = np.asarray(depth, dtype=float)
    if depth.size == 0 or not np.isfinite(depth).any():
        raise ValueError("no finite depths to bin")
    edges = np.arange(0, np.nanmax(depth) + bin_size, bin_size)
    out = pd.DataFrame({k: np.asarray(v, dtype=float) for k, v in values.items()})
    out["_bin"] = pd.cut(depth, edges)
    g = out.groupby("_bin", observed=False)
    b = g.mean()
    b["n_samples"] = g.size()
    b.index = [iv.mid for iv in b.index]
    b.index.name = "depth_bin_m"
    for k in smooth:
        b[k] = b[k].rolling(smooth_window, center=True, min_periods=1).mean()
    return b


def reference_at(bin_depth, ref_depth, ref_value, extrapolate: bool = True) -> np.ndarray:
    """Reference profile interpolated linearly onto the bin mid-points.

    extrapolate=True holds the reference constant above its shallowest and below its deepest sample (numpy.interp); extrapolate=False
    returns NaN there. Reference points are sorted with DataFrame.sort_values: repeated depths from ship heave make numpy.interp depend on the
    order of ties, a difference of order 1e-4 PSU.
    """
    ref = pd.DataFrame({"d": np.asarray(ref_depth, dtype=float), "v": np.asarray(ref_value, dtype=float)})
    ref = ref[np.isfinite(ref["d"]) & np.isfinite(ref["v"])].sort_values("d")
    z = np.asarray(bin_depth, dtype=float)
    at = np.interp(z, ref["d"], ref["v"])
    return at if extrapolate else np.where((z < ref["d"].min()) | (z > ref["d"].max()), np.nan, at)


@dataclass
class CorrectionResult:
    kind: str
    value: float                 # offset (additive) or gain (multiplicative)
    iqr: float                   # IQR of the per-bin discrepancy
    rmse: float                  # RMSE of (sensor - reference) before correction
    n_bins: int
    depth_min_m: float
    depth_max_m: float
    rmse_after: float            # RMSE of (corrected sensor - reference) after applying `value`
    diffs: pd.Series = field(repr=False, default=None)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("diffs")
        return d


def estimate_correction(binned: pd.DataFrame, ref_depth, ref_value, col: str = "salinity_psu",
                        kind: str = "additive", depth_range: tuple | None = None, extrapolate: bool = True) -> CorrectionResult:
    """Median discrepancy over depth bins, with the IQR and RMSE of the same differences."""
    z = binned.index.to_numpy(dtype=float)
    ref_at = reference_at(z, ref_depth, ref_value, extrapolate)
    sensor = binned[col].to_numpy()
    valid = np.isfinite(sensor) & np.isfinite(ref_at)
    if depth_range is not None:
        valid &= (z >= depth_range[0]) & (z <= depth_range[1])
    if not valid.any():
        raise ValueError("no overlapping depth bins between profiler and reference")
    disc = correction.pointwise(kind, sensor[valid], ref_at[valid])
    value = float(np.median(disc))
    diff = sensor[valid] - ref_at[valid]
    q75, q25 = np.percentile(disc, [75, 25])
    after = correction.apply(kind, sensor[valid], value) - ref_at[valid]
    return CorrectionResult(kind, value, float(q75 - q25), float(np.sqrt(np.mean(diff ** 2))),
                            int(valid.sum()), float(z[valid].min()), float(z[valid].max()),
                            float(np.sqrt(np.mean(after ** 2))), pd.Series(diff, index=z[valid]))


def salinity_offset(binned, ref_depth, ref_sal, col="salinity_psu", depth_range=None, extrapolate=True) -> CorrectionResult:
    return estimate_correction(binned, ref_depth, ref_sal, col, "additive", depth_range, extrapolate)


def an31_slope(binned: pd.DataFrame, ref_depth, ref_sal, smooth_window: int = 5,
               col: str = "salinity_psu", offset: float | None = None, extrapolate: bool = True) -> dict:
    """Sea-Bird AN31 conductivity slope with zero offset.

    alpha_k = profiler conductivity in bin k, beta_k = conductivity of the reference salinity at the profiler temperature and pressure
    (PSS-78), slope = sum(alpha_k beta_k) / sum(alpha_k^2).

    The salinity S(slope * C, T, P) is smoothed like the offset path, and both RMSEs are computed on the same bins against the same
    reference. Bins whose smoothed value comes only from bridging an empty bin are excluded. If `offset` is given, the RMSE after the
    additive offset on these bins is returned as `rmse_after_offset`.
    """
    z_all = binned.index.to_numpy(dtype=float)
    ref_all = reference_at(z_all, ref_depth, ref_sal, extrapolate)
    need = ["conductivity_sm", "temperature_c", "pressure_dbar", col]
    ok = binned[need].notna().all(axis=1).to_numpy() & np.isfinite(ref_all)
    if not ok.any():
        raise ValueError("no bins with conductivity, temperature, pressure and reference")
    b = binned.loc[ok]
    alpha = b["conductivity_sm"].to_numpy()
    t, p = b["temperature_c"].to_numpy(), b["pressure_dbar"].to_numpy()
    beta = conductivity_from_salinity(ref_all[ok], t, p)
    slope = float(np.sum(alpha * beta) / np.sum(alpha ** 2))
    s_slope = pd.Series(np.nan, index=binned.index)
    s_slope[ok] = salinity_from_conductivity(alpha * slope, t, p)
    s_slope = s_slope.rolling(smooth_window, center=True, min_periods=1).mean()
    sensor = binned[col].to_numpy()
    both = ok & np.isfinite(s_slope.to_numpy())
    r_pre = sensor[both] - ref_all[both]
    r_post = s_slope.to_numpy()[both] - ref_all[both]
    r_off = sensor[both] - offset - ref_all[both] if offset is not None else None
    return {"slope": slope, "n_bins": int(both.sum()),
            "rmse_after_offset": float(np.sqrt(np.mean(r_off ** 2))) if r_off is not None else None,
            "rmse_before": float(np.sqrt(np.mean(r_pre ** 2))),
            "rmse_after": float(np.sqrt(np.mean(r_post ** 2))),
            "residual_median_after": float(np.median(r_post)),
            "salinity_slope_corrected": s_slope}
