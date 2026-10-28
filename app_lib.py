"""Helpers for the viewer. Reads outputs/<run>/; performs no calibration computation."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

OUT = Path(__file__).resolve().parent / "outputs"
CONFIG = Path(__file__).resolve().parent / "config" / "cruises.toml"
C_REF, C_RAW, C_OFF, C_SLOPE, C_BOTTLE, C_UP = "#000000", "#8A5A00", "#C1272D", "#0050B3", "#00796B", "rgba(80,80,80,0.55)"

# Fixed white chart background with dark text, independent of the app theme.
LAYOUT = dict(template="plotly_white", paper_bgcolor="white", plot_bgcolor="white", font=dict(color="#111111", size=13))
AXIS = dict(gridcolor="#D0D0D0", linecolor="#111111", zerolinecolor="#777777",
            tickfont=dict(color="#111111"), title_font=dict(color="#111111"))


def style(fig, **kw):
    fig.update_layout(**LAYOUT, **kw)
    fig.update_xaxes(**AXIS)
    fig.update_yaxes(**AXIS)
    fig.update_annotations(font=dict(color="#111111"))
    return fig


def _mtime(p: Path) -> float:
    return p.stat().st_mtime if p.exists() else 0.0


# File mtime is part of the cache key, so regenerated outputs invalidate the cache.
@st.cache_data(show_spinner=False, max_entries=64)
def _load_json(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf8"))


@st.cache_data(show_spinner=False, max_entries=64)
def _load_csv(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path)


@st.cache_data(show_spinner=False, max_entries=16)
def load_bytes(path: str, mtime: float) -> bytes:
    return Path(path).read_bytes()


UPLOAD_SUFFIX = " (uploaded)"
RUN_KEY = re.compile(r"^\d{4}(-[A-Z0-9]+)?$")      # "2025" (default site) or "2025-RS01SBPS"


def uploaded_runs() -> dict:
    """label -> outputs root, for runs made from files uploaded in this browser session."""
    return st.session_state.setdefault("uploaded_runs", {})


def folder(year, variable: str = "salinity") -> Path:
    """Output folder of a run. `year` is "2025" for the repository outputs or "2025 (uploaded)" for a session run."""
    root = Path(uploaded_runs()[year]) if year in uploaded_runs() else OUT
    return root / str(year).removesuffix(UPLOAD_SUFFIX) / ("oxygen" if variable == "oxygen" else "")


def mtime(year, name: str, variable: str = "salinity") -> float:
    return _mtime(folder(year, variable) / name)


@st.cache_data(show_spinner=False)
def _config(mtime: float) -> dict:
    with open(CONFIG, "rb") as f:
        return tomllib.load(f)


def _site(run) -> tuple[str, str, dict] | None:
    """(year, site code, the config's [sites.<code>] table) of a repository run key ('2025' = the default site, '2025-RS01SBPS'); None for an
    uploaded run or a site the config does not know."""
    if run in uploaded_runs() or not RUN_KEY.match(str(run)):
        return None
    cfg = _config(_mtime(CONFIG))
    year, _, site = str(run).partition("-")
    site = site or cfg["sites"]["default"]
    table = cfg["sites"].get(site)
    return None if table is None else (year, site, table)


def run_site(run) -> str:
    """'2025 · Axial Base': the year and where, for pages that are not about one profiler instrument."""
    s = _site(run)
    return str(run) if s is None else f"{s[0]} · {s[2]['short_name']}"


def run_label(run, variable: str = "salinity") -> str:
    """'2025 · Axial Base · RS03AXPS-SF03A-2A-CTDPFA302' (the DOFSTA designator for variable='oxygen'); an uploaded run keeps its own label."""
    s = _site(run)
    if s is None:
        return str(run)
    return f"{run_site(run)} · {s[2]['oxygen_reference_designator' if variable == 'oxygen' else 'profiler_reference_designator']}"


def run_caption(run) -> str:
    """Both instruments of the selected run, for the sidebar."""
    s = _site(run)
    if s is None:
        return "Run made from uploaded files."
    return f"{s[2]['name']}  \nSalinity: `{s[2]['profiler_reference_designator']}`  \nOxygen: `{s[2]['oxygen_reference_designator']}`"


def years_on_disk() -> list[str]:
    """Repository runs (newest year first; the default site's run before the other sites' "<year>-<site>" runs) followed by
    runs uploaded in this session."""
    names = [d.name for d in OUT.iterdir() if d.is_dir() and RUN_KEY.match(d.name)] if OUT.is_dir() else []
    repo = sorted(names, key=lambda n: (-int(n[:4]), n))
    return repo + list(uploaded_runs())


def get(year, name: str, kind: str = "csv", variable: str = "salinity"):
    p = folder(year, variable) / name
    if not p.is_file():
        return None
    return (_load_json if kind == "json" else _load_csv)(str(p), _mtime(p))


def require_year(variable: str = "salinity") -> tuple[str, dict]:
    """Selected year and its calibration record, or an explicit 'missing' message and st.stop()."""
    year = st.session_state.get("year")
    if year is None:
        st.error("outputs\\ has no year folders. Run `python scripts/run_year.py --year 2025` first, or upload files on the Run your files page.", icon=":material/error:")
        st.stop()
    miss = get(year, "missing.json", "json", variable)
    if miss:
        st.error(f"**{year}: missing input.** The pipeline stopped for this year and computed nothing.", icon=":material/block:")
        st.dataframe(pd.DataFrame(miss["missing"]), hide_index=True, alt=f"Input files missing for {year}")
        st.caption(f"checked {miss['checked_utc']}")
        st.stop()
    rec = get(year, "calibration_record.json", "json", variable)
    if rec is None:
        y, _, site = str(year).removesuffix(UPLOAD_SUFFIX).partition("-")
        cmd = f"python scripts/run_year.py --year {y}" + (f" --site {site}" if site else "") + (" --variable oxygen" if variable == "oxygen" else "")
        st.error(f"{year}: no {variable} calibration record in outputs. Run `{cmd}`.", icon=":material/error:")
        st.stop()
    return year, rec


def fmt(x, nd=4, signed=False):
    return "missing" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:+.{nd}f}" if signed else f"{x:.{nd}f}")


def show_flags(rec, codes=None):
    for f in rec["rca:flags"]:
        if codes and f["code"] not in codes:
            continue
        {"warning": st.warning, "caveat": st.info, "info": st.caption}.get(f["severity"], st.info)(f["text"])
