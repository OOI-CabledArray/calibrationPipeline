"""Readers for the Autosal salinity workbook and the SampleList sheet of the cast-log workbook."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

from .instruments import ACCEPTED_BIT, BAD_NISKIN_BITS, BAD_SAMPLE_BITS
from .oxygen import SUMMARY_MISSING, summary_flag_bits


def normalize_bottle_id(x) -> str:
    """Sample/bottle IDs differ between workbooks ("SA-0367", "SAL-123", 19, 19.0, "415"): compare on the number alone.
    Anything without a number (e.g. "Chk SW") is returned stripped and unchanged."""
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    s = str(x).strip()
    m = re.fullmatch(r"(?:[A-Za-z]+[- ]*)?0*(\d+)(?:\.0+)?", s)
    return m.group(1) if m else s


ID_HEADERS = ("uw bottle id", "salt bottle #")


def read_autosal(path: str | Path) -> tuple[pd.DataFrame, dict]:
    """Read the 'Salinity Sample Analyses' sheet of the Autosal workbook (Guildline Autosal 8400B).

    Returns (samples, header). samples: bottle_id, ratio_2rt, salinity, notes; salinity is the value computed by the laboratory.
    header: standard batch, K15 and other header cells.
    """
    ws = openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    header = {}
    for r in rows[:12]:
        for i, v in enumerate(r):
            if isinstance(v, str):
                key = v.strip().rstrip(":").lower()
                nxt = r[i + 1] if i + 1 < len(r) else None
                if key in ("std. batch no.", "k15 value", "salinity", "std. 2rt", "date", "analyst", "bath temp.", "client", "filename"):
                    header[key] = nxt
                elif v.startswith("Salinity Sample Analyses"):
                    header["instrument"] = v
    norm = lambda x: str(x).strip().lower() if x else ""  # noqa: E731
    hdr_i = next((i for i, r in enumerate(rows) if any(norm(x) in ID_HEADERS for x in r)), None)
    if hdr_i is None:
        raise ValueError(f"no bottle-ID column ({' / '.join(ID_HEADERS)}) found in {path}")
    hdr = [norm(x) for x in rows[hdr_i]]
    c = next(i for i, x in enumerate(hdr) if x in ID_HEADERS)
    c_rt = next((i for i, x in enumerate(hdr) if x.startswith("avg")), c + 1)
    c_sal = next((i for i, x in enumerate(hdr) if x == "salinity"), c + 2)
    c_note = next((i for i, x in enumerate(hdr) if x == "notes"), None)
    out = []
    for r in rows[hdr_i + 1:]:
        bid, rt, sal = r[c], r[c_rt], r[c_sal]
        note = r[c_note] if c_note is not None and len(r) > c_note else None
        if bid is None or not isinstance(sal, (int, float)):
            continue
        out.append({"bottle_id": normalize_bottle_id(bid), "ratio_2rt": rt, "salinity": float(sal), "autosal_note": note})
    df = pd.DataFrame(out)
    df["is_standard_check"] = ~df["bottle_id"].str.fullmatch(r"\d+")
    return df, header


def read_sample_list(path: str | Path) -> pd.DataFrame:
    """SampleList tab: one row per Niskin x sample bottle."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows = list(wb["SampleList"].iter_rows(values_only=True))
    df = pd.DataFrame(rows[1:], columns=rows[0])
    return df.dropna(how="all")


def read_cast_list(path: str | Path) -> pd.DataFrame:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows = list(wb["CastList"].iter_rows(values_only=True))
    return pd.DataFrame(rows[1:], columns=rows[0]).dropna(how="all")


def salinity_samples_for_cast(sample_list: pd.DataFrame, cast: str) -> pd.DataFrame:
    """Niskin position, target depth and salinity bottle number (e.g. 'SA-415') for one cast."""
    s = sample_list[(sample_list["Cast"] == cast) & sample_list["Salinity Bottle Number"].notna()]
    out = s[["Cast", "Niskin/Bottle Position", "Niskin Flag", "Target Depth", "Salinity Bottle Number",
             "Discrete Salinity Flag", "Discrete Salinity Replicate Flag"]].copy()
    out.columns = ["cast", "niskin", "niskin_flag", "target_depth_m", "sample_id", "salinity_flag", "replicate_flag"]
    out["niskin"] = out["niskin"].astype(int)
    out["sample_id"] = out["sample_id"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    out["bottle_id"] = out["sample_id"].map(normalize_bottle_id)
    return out.reset_index(drop=True)


def read_summary_salinity(path: str | Path, cast: str, niskin_ok_text: str, salinity_ok_text: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bottle salinity of one cast from an OOI discrete summary CSV, shaped like salinity_samples_for_cast and read_autosal (samples, autosal).

    One row per Niskin; its salinity is the mean of the replicates that are acceptable (bit 2, no questionable/bad bit) in both the sample and
    replicate flags, or of all replicates when none is. The summary's bit flags are written as the text the bottle check reads: the Niskin flag
    as niskin_ok_text when acceptable, the salinity flag as salinity_ok_text when acceptable; a flag that is present but not acceptable is
    'not acceptable'; an absent flag stays empty (the bottle is not excluded). The laboratory's 2rt ratio is not in the summary.
    """
    d = pd.read_csv(path, low_memory=False, encoding_errors="replace")
    d = d[d["Cast"].astype(str) == str(cast)]
    if d.empty:
        raise ValueError(f"cast {cast!r} is not in {path}")
    sal = pd.to_numeric(d["Discrete Salinity [psu]"], errors="coerce").where(lambda s: s != SUMMARY_MISSING)
    d = d.assign(sal=sal)[sal.notna()]

    def accepted(col, bad):
        bits = d[col].map(summary_flag_bits)
        return bits.isna().to_numpy(), bits.map(lambda b: b is not None and ACCEPTED_BIT in b and not (b & bad)).to_numpy()

    s_absent, s_ok = accepted("Discrete Salinity Flag", BAD_SAMPLE_BITS)
    r_absent, r_ok = accepted("Discrete Salinity Replicate Flag", BAD_SAMPLE_BITS)
    n_absent, n_ok = accepted("Niskin Flag", BAD_NISKIN_BITS)
    d = d.assign(good=(s_ok | s_absent) & (r_ok | r_absent), s_absent=s_absent, s_ok=s_ok, r_absent=r_absent, r_ok=r_ok, n_absent=n_absent, n_ok=n_ok)
    text = lambda absent, ok, ok_text: np.where(absent, None, np.where(ok, ok_text, "not acceptable"))  # noqa: E731
    rows = []
    for niskin, g in d.groupby(d["Niskin/Bottle Position"].astype(int), sort=True):
        use = g[g["good"]] if g["good"].any() else g
        first = g.iloc[0]
        rows.append({"cast": str(cast), "niskin": int(niskin), "niskin_flag": text(first.n_absent, first.n_ok, niskin_ok_text).item(),
                     "target_depth_m": pd.to_numeric(first["CTD Depth [m]"], errors="coerce"), "sample_id": f"{cast} Niskin {int(niskin)}",
                     "salinity_flag": text(use.s_absent.all(), use.s_ok.any(), salinity_ok_text).item(),
                     "replicate_flag": text(use.r_absent.all(), use.r_ok.any(), "acceptable").item(),
                     "bottle_id": str(int(niskin)), "salinity": float(use["sal"].mean()), "n_replicates": int(len(use))})
    out = pd.DataFrame(rows, columns=["cast", "niskin", "niskin_flag", "target_depth_m", "sample_id", "salinity_flag", "replicate_flag", "bottle_id",
                                      "salinity", "n_replicates"])
    autosal = pd.DataFrame({"bottle_id": out["bottle_id"], "ratio_2rt": np.nan, "salinity": out["salinity"],
                            "autosal_note": [f"mean of {n} replicate(s) in the discrete summary" for n in out["n_replicates"]], "is_standard_check": False})
    return out.drop(columns=["salinity", "n_replicates"]), autosal
