"""Helpers for the viewer. Reads outputs/<run>/; performs no calibration computation."""
from __future__ import annotations

import json
import re
import tomllib
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from rca_sal.deployments import summarize
from rca_sal.pipeline import load_config

OUT = Path(__file__).resolve().parent / "outputs"
CONFIG = Path(__file__).resolve().parent / "config" / "cruises.toml"
C_REF, C_RAW, C_OFF, C_SLOPE, C_BOTTLE, C_UP = "#000000", "#8A5A00", "#C1272D", "#0050B3", "#00796B", "rgba(80,80,80,0.55)"
LEVEL_COLOR = {1: "green", 2: "blue", 3: "orange", 4: "red", None: "gray"}

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
REFDES = {"salinity": "profiler_reference_designator", "oxygen": "oxygen_reference_designator", "nitrate": "nitrate_reference_designator"}


def uploaded_runs() -> dict:
    """label -> outputs root, for runs made from files uploaded in this browser session."""
    return st.session_state.setdefault("uploaded_runs", {})


def folder(year, variable: str = "salinity") -> Path:
    """Output folder of a run. `year` is "2025" for the repository outputs or "2025 (uploaded)" for a session run."""
    root = Path(uploaded_runs()[year]) if year in uploaded_runs() else OUT
    return root / str(year).removesuffix(UPLOAD_SUFFIX) / ("" if variable == "salinity" else variable)


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
    """'2025 · Axial Base': the year and where; an uploaded run keeps its own label."""
    s = _site(run)
    return str(run) if s is None else f"{s[0]} · {s[2]['short_name']}"


def run_label(run, variable: str = "salinity") -> str:
    """'2025 · Axial Base · RS03AXPS-SF03A-2A-CTDPFA302' (the DOFSTA or NUTNR designator for variable='oxygen' or 'nitrate'); an uploaded
    run keeps its own label."""
    s = _site(run)
    return str(run) if s is None else f"{run_site(run)} · {s[2].get(REFDES[variable], '')}"


def run_caption(run) -> str:
    """Site name and the three instruments of the selected run, for the sidebar."""
    s = _site(run)
    if s is None:
        return "Run made from uploaded files."
    return f"{s[2]['name']}" + "".join(f"  \n{v.capitalize()}: `{s[2][k]}`" for v, k in REFDES.items() if k in s[2])


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
        st.error("outputs/ has no run folders. Run `python scripts/run_year.py --year 2025` first, or upload files on the Run your files page.", icon=":material/error:")
        st.stop()
    miss = get(year, "missing.json", "json", variable)
    if miss:
        st.error(f"**{run_site(year)}: missing input.** The {variable} pipeline stopped for this run and computed nothing.", icon=":material/block:")
        st.dataframe(pd.DataFrame(miss["missing"]), hide_index=True, alt=f"Input files missing for {year}")
        st.caption(f"checked {miss['checked_utc']}")
        st.stop()
    rec = get(year, "calibration_record.json", "json", variable)
    if rec is None:
        y, _, site = str(year).removesuffix(UPLOAD_SUFFIX).partition("-")
        cmd = f"python scripts/run_year.py --year {y}" + (f" --site {site}" if site else "") + ("" if variable == "salinity" else f" --variable {variable}")
        fix = "Upload its files on the Run your files page." if year in uploaded_runs() else f"Run `{cmd}`."
        st.error(f"{run_site(year)}: no {variable} calibration record. {fix}", icon=":material/error:")
        st.stop()
    return year, rec


def missing(name: str, *frames):
    """Stop the page with one message if any output table is absent."""
    if any(f is None for f in frames):
        st.error(f"Missing output file(s): {name}.", icon=":material/error:")
        st.stop()


def fmt(x, nd=4, signed=False):
    return "missing" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:+.{nd}f}" if signed else f"{x:.{nd}f}")


# --- deployment status ------------------------------------------------------------------------------------------------------------------

def _summary_signature() -> tuple:
    """Changes whenever a run's outputs or the config change, so the cached summary is rebuilt."""
    files = [CONFIG] + sorted(OUT.glob("*/calibration_record.json")) + sorted(OUT.glob("*/*/calibration_record.json")) \
        + sorted(OUT.glob("*/missing.json")) + sorted(OUT.glob("*/*/missing.json"))
    return tuple((str(f), f.stat().st_mtime) for f in files)


@st.cache_data(show_spinner="Building the deployment summary", max_entries=4)
def _summary(sig: tuple) -> dict:
    return summarize(load_config(CONFIG), OUT)


def summary() -> dict:
    """Deployment summary of the repository runs (rca_sal.deployments)."""
    return _summary(_summary_signature())


def status_text(s: dict) -> str:
    return s["label"] if s["level"] is None else f"{s['level']} · {s['label']}"


def badge(s: dict) -> str:
    return f":{LEVEL_COLOR[s['level']]}-badge[{status_text(s)}]"


def deployment(run, variable: str) -> dict | None:
    """Summary entry of a repository run for one variable; None for an uploaded run."""
    s = _site(run)
    if s is None:
        return None
    return next((d for g in summary()["instruments"] if g["variable"] == variable and g["site"] == s[1]
                 for d in g["deployments"] if d["run"] == str(run)), None)


# --- page layout ------------------------------------------------------------------------------------------------------------------------

def header(title: str | None, run, rec: dict, variable: str = "salinity", instrument: bool = True):
    """Short page title, then one line of context: run, instrument, cruise and cast, and the deployment status with what set it."""
    if title:
        st.title(title)
    cr = rec["rca:cruise"]
    parts = [f"**{run_site(run)}**"]
    s = _site(run)
    refdes = rec.get("rca:ooinet", {}).get("reference_designator") or (s[2].get(REFDES[variable]) if s else None)
    if instrument and refdes:
        parts.append(f"`{refdes}`")
    parts.append(f"{cr['cruise']} {cr['ship_cruise_id']} {cr['cast']}")
    d = deployment(run, variable)
    why = None
    if d:
        parts.append(f"{variable} status {badge(d['status'])}")
        why = "\n".join(f"- {x}" for x in d["status"].get("set_by", []) + d["status"].get("other_issues", [])) or None
    st.markdown(" · ".join(parts), help=why)


def answer(text: str, ok: bool = False):
    """The page's result in one sentence."""
    (st.success if ok else st.info)(text, icon=":material/check_circle:" if ok else ":material/tune:")


@contextmanager
def card(title: str):
    """Bordered group of metrics under a bold title, as wide as its metrics; groups wrap to the next line when the page is narrow.
    Yields st.metric sized to its content."""
    with st.container(border=True, width="content"):
        st.markdown(f"**{title}**")
        with st.container(horizontal=True, gap="medium"):
            yield lambda *a, **k: st.metric(*a, width="content", **k)


def pick_variant(label: str, options: list, primary: str, key: str, fmt=str) -> str:
    """Selectbox of the run's variants, defaulting to the record's primary. The choice is kept per run, so another run opens on its own primary."""
    return st.selectbox(label, options, index=options.index(primary) if primary in options else 0, key=f"{key}:{st.session_state.get('year')}",
                        format_func=fmt, width=520)


def t2_notes(rec) -> list[str]:
    t2 = rec["rca:twoPoint"]["T2"]
    return [f"Drift not corrected (T2 missing): {t2['reason']}."] if t2.get("status") == "missing" else []


def flag_list(rec, codes=None, notes=()):
    for f in [f for f in rec["rca:flags"] if not codes or f["code"] in codes] + [{"severity": "caveat", "text": n} for n in notes]:
        {"warning": st.warning, "caveat": st.info, "info": st.caption}.get(f["severity"], st.info)(f["text"])


def show_flags(rec, codes=None, notes=()):
    """The record's flags (only `codes` if given) and extra notes, in one expander whose label counts the warnings."""
    fl = [f for f in rec["rca:flags"] if not codes or f["code"] in codes]
    nw, nn = sum(f["severity"] == "warning" for f in fl), sum(f["severity"] != "warning" for f in fl) + len(notes)
    if nw + nn == 0:
        return
    label = " · ".join(([f":orange[**{nw} warning{'s' * (nw != 1)}**]"] if nw else []) + ([f"{nn} note{'s' * (nn != 1)}"] if nn else []))
    with st.expander(f"Flags and caveats: {label}", icon=":material/flag:"):
        flag_list(rec, codes, notes)


def per_cast_chart(x, y, err, ids, what: str, hover: str, ytitle: str, xtitle: str, cast_time: str,
                   ref=None, band=None):
    """One estimate per upcast or profile against its time, with ± half IQR bars, the CTD cast time and the selected variant's value (dashed)."""
    fig = go.Figure()
    if band is not None:
        fig.add_hrect(y0=-band, y1=band, fillcolor="#2E7D32", opacity=0.08, line_width=0)
    fig.add_trace(go.Scatter(x=x, y=y, mode="markers", customdata=[int(k) for k in ids], marker=dict(size=10, color=C_OFF),
                             error_y=dict(type="data", array=err, color="#444444", thickness=1.5),
                             hovertemplate=f"{what} #%{{customdata}}<br>{hover}<extra></extra>"))
    if ref is not None:
        fig.add_hline(y=ref, line_color=C_REF, line_dash="dash")
    fig.add_vline(x=pd.Timestamp(cast_time).to_pydatetime(), line_color=C_BOTTLE, line_dash="dot", annotation_text="CTD cast",
                  annotation_position="top right")
    fig.update_yaxes(title=ytitle)
    fig.update_xaxes(title=xtitle)
    return style(fig, height=340, margin=dict(l=10, r=10, t=30, b=10), showlegend=False)
