"""Readers for Sea-Bird shipboard CTD files: processed .cnv and bottle-summary .btl."""
from __future__ import annotations

import re
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd


def read_cnv(path: str | Path) -> tuple[pd.DataFrame, dict]:
    """Read a processed .cnv. Returns (data, header). Column names come from the '# name N = short: long' header lines; repeated
    names get a numeric suffix.
    """
    lines = Path(path).read_text(errors="replace").splitlines(keepends=True)
    data_start = next(i + 1 for i, ln in enumerate(lines) if ln.strip().startswith("*END*"))
    names, header = [], {"processing": []}
    for ln in lines[:data_start]:
        s = ln.strip()
        m = re.match(r"# name \d+ = ([^:]+)", s)
        if m:
            names.append(m.group(1).strip())
        elif s.startswith("* NMEA Latitude"):
            header["nmea_lat"] = _nmea(s.split("=", 1)[1])
        elif s.startswith("* NMEA Longitude"):
            header["nmea_lon"] = _nmea(s.split("=", 1)[1])
        elif s.startswith("* NMEA UTC"):
            header["nmea_utc"] = pd.to_datetime(s.split("=", 1)[1].strip(), format="%b %d %Y %H:%M:%S", utc=True)
        elif s.startswith("# interval"):
            header["interval_s"] = float(s.split(":")[-1])
        elif s.startswith("# bad_flag"):
            header["bad_flag"] = float(s.split("=", 1)[1])
        elif s.startswith("# ") and "_date" in s.split("=")[0]:
            header["processing"].append(s[2:].split("=")[0].strip().replace("_date", ""))
    df = pd.read_csv(StringIO("".join(lines[data_start:])), sep=r"\s+", header=None)
    cols, seen = [], {}
    for n in names[: df.shape[1]]:
        seen[n] = seen.get(n, 0) + 1
        cols.append(n if seen[n] == 1 else f"{n}.{seen[n] - 1}")
    df.columns = cols
    if "bad_flag" in header:
        df = df.replace(header["bad_flag"], np.nan)
    if "timeS" not in df and "interval_s" in header:
        df["timeS"] = np.arange(len(df)) * header["interval_s"]
    return df, header


def _nmea(txt: str) -> float:
    deg, minutes, hemi = txt.split()
    v = float(deg) + float(minutes) / 60
    return -v if hemi in ("S", "W") else v


def downcast(df: pd.DataFrame, pcol: str = "prDM", min_p: float = 2.0, heave_tol: float = 0.5) -> pd.DataFrame:
    """Keep the downcast only: scans from min_p down to the pressure maximum, without ship-heave reversals.

    A scan is dropped when its pressure is more than `heave_tol` dbar shallower than the deepest
    pressure reached so far.
    """
    p = df[pcol].to_numpy(dtype=float)
    if not np.isfinite(p).any():
        raise ValueError(f"no finite {pcol} values in CTD file")
    imax = int(np.nanargmax(p))
    d = df.iloc[: imax + 1]
    d = d[d[pcol] >= min_p]
    pd_ = d[pcol].to_numpy(dtype=float)
    keep = pd_ >= np.maximum.accumulate(pd_) - heave_tol
    return d[keep]


def read_btl(path: str | Path) -> pd.DataFrame:
    """Parse a Sea-Bird .btl file: one row per bottle (the '(avg)' lines)."""
    lines = Path(path).read_text(errors="replace").splitlines()
    body = [ln for ln in lines if not ln.startswith(("*", "#"))]
    head = body[0].split()
    rows = []
    for i, ln in enumerate(body):
        if ln.rstrip().endswith("(avg)"):
            parts = ln.split()
            bottle = int(parts[0])
            date = " ".join(parts[1:4])
            vals = parts[4:-1]
            time = body[i + 1].split()[0]
            rec = {"bottle": bottle, "time_utc": pd.to_datetime(f"{date} {time}", format="%b %d %Y %H:%M:%S", utc=True)}
            rec.update({k: float(v) for k, v in zip(head[2:], vals)})
            rows.append(rec)
    return pd.DataFrame(rows)
