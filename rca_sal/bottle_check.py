"""CTD versus Autosal bottle salinity.

For each Niskin, d_i = S_ctd,i - S_bottle,i, with S_ctd the CTD salinity averaged over the bottle closure (.btl) and S_bottle the
Autosal salinity of the sample. Reported: median, mean, IQR and RMSE of d, and the AN31 conductivity slope
m = sum(a_i b_i) / sum(a_i^2), a = CTD conductivity, b = conductivity of S_bottle at the CTD temperature and pressure (PSS-78).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .pss78 import conductivity_from_salinity


@dataclass
class BottleCheck:
    sensor: str                 # "primary (sal00)" / "secondary (sal11)"
    n: int
    median_diff_psu: float      # CTD - bottle
    mean_diff_psu: float
    iqr_psu: float
    rmse_psu: float
    an31_slope: float           # sum(a*b)/sum(a^2), a = CTD cond, b = bottle cond at CTD T,P
    table: pd.DataFrame

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("table")
        return d


def match_bottles(btl: pd.DataFrame, samples: pd.DataFrame, autosal: pd.DataFrame) -> pd.DataFrame:
    """Join .btl averages <- Niskin position <- salinity sample id <- Autosal result."""
    m = samples.merge(btl, left_on="niskin", right_on="bottle", how="left", validate="1:1")
    m = m.merge(autosal[["bottle_id", "ratio_2rt", "salinity", "autosal_note"]], on="bottle_id", how="left")
    m = m.rename(columns={"salinity": "bottle_salinity"})
    return m


def check(matched: pd.DataFrame, sal_col: str = "Sal00", cond_col: str = "C0S/m", t_col: str = "T090C",
          p_col: str = "PrDM", label: str = "primary (sal00)") -> BottleCheck:
    t = matched.dropna(subset=["bottle_salinity", sal_col]).copy()
    t["diff_psu"] = t[sal_col] - t["bottle_salinity"]
    t["bottle_cond_sm"] = conductivity_from_salinity(t["bottle_salinity"], t[t_col], t[p_col])
    d = t["diff_psu"].to_numpy()
    a, b = t[cond_col].to_numpy(), t["bottle_cond_sm"].to_numpy()
    q75, q25 = np.percentile(d, [75, 25]) if len(d) else (np.nan, np.nan)
    return BottleCheck(
        sensor=label, n=len(t),
        median_diff_psu=float(np.median(d)) if len(d) else np.nan,
        mean_diff_psu=float(np.mean(d)) if len(d) else np.nan,
        iqr_psu=float(q75 - q25),
        rmse_psu=float(np.sqrt(np.mean(d ** 2))) if len(d) else np.nan,
        an31_slope=float(np.sum(a * b) / np.sum(a * a)) if len(d) else np.nan,
        table=t,
    )
