"""Readers for the Autosal salinity workbook and the SampleList sheet of the cast-log workbook."""
from __future__ import annotations

import re
from pathlib import Path

import openpyxl
import pandas as pd


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
