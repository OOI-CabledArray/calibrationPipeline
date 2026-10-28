"""OOI profile index (OOI-CabledArray/profileIndices) of the shallow profilers.

One row per profile: `profile` number, `start` (start of the upcast), `peak` (shallowest point: end of the upcast, start of the downcast)
and `end` (end of the downcast), UTC. Used to label the upcast samples of a profiler file and to cross-check rca_sal.profiler.label_upcasts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def read_index(path) -> pd.DataFrame:
    ix = pd.read_csv(path)
    missing = {"profile", "start", "peak", "end"} - set(ix.columns)
    if missing:
        raise ValueError(f"profile index {path} lacks columns {sorted(missing)}")
    for c in ("start", "peak", "end"):
        ix[c] = pd.to_datetime(ix[c], utc=True).dt.as_unit("ns")
    return ix.sort_values("start").reset_index(drop=True)


def label_by_index(prof: pd.DataFrame, ix: pd.DataFrame) -> pd.DataFrame:
    """Copy of the profiler frame with 'upcast_id' = the index's profile number for samples between a profile's start and
    peak (inclusive), -1 elsewhere, and 'w_ms' NaN (speed is not part of the index)."""
    out = prof.copy()
    t = out["time"].to_numpy("datetime64[ns]")
    start, peak = ix["start"].to_numpy("datetime64[ns]"), ix["peak"].to_numpy("datetime64[ns]")
    k = np.searchsorted(start, t, side="right") - 1          # last profile that started at or before each sample
    ok = (k >= 0)
    ok[ok] = t[ok] <= peak[k[ok]]
    out["upcast_id"] = np.where(ok, ix["profile"].to_numpy()[np.clip(k, 0, None)], -1).astype(int)
    out["w_ms"] = np.nan
    return out


def crosscheck(summ: pd.DataFrame, ix: pd.DataFrame, tol_min: float = 10.0) -> dict:
    """Does each upcast found from the profiler data match a profile in the index (start and end within tol_min)?"""
    rows = []
    for k, r in summ.iterrows():
        j = int((ix["start"] - r["start"]).abs().argmin())
        ds = (r["start"] - ix["start"].iloc[j]).total_seconds() / 60
        de = (r["end"] - ix["peak"].iloc[j]).total_seconds() / 60
        rows.append({"upcast_id": int(k), "index_profile": int(ix["profile"].iloc[j]),
                     "start_diff_min": round(ds, 2), "end_minus_peak_min": round(de, 2),
                     "match": bool(abs(ds) <= tol_min and abs(de) <= tol_min)})
    return {"tolerance_min": tol_min, "n_upcasts_found": len(rows), "n_matched": sum(r["match"] for r in rows), "upcasts": rows}


def profiles_after(ix: pd.DataFrame, t, n: int) -> pd.DataFrame:
    """The first n profiles whose upcast starts at or after t."""
    return ix[ix["start"] >= pd.Timestamp(t)].head(n)
