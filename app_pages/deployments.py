import json
from pathlib import Path

import pandas as pd
import streamlit as st

import app_lib as L
from rca_sal.deployments import summarize, to_yaml
from rca_sal.pipeline import load_config

CONFIG = Path(__file__).resolve().parents[1] / "config" / "cruises.toml"
LEVEL_COLOR = {1: "green", 2: "blue", 3: "orange", 4: "red", None: "gray"}
OUTCOME_COLOR = {"pass": "green", "marginal": "orange", "fail": "red", "n/a": "gray"}


def _signature() -> tuple:
    """Changes whenever a run's outputs or the config change, so the cached summary is rebuilt."""
    files = [CONFIG] + sorted(L.OUT.glob("*/calibration_record.json")) + sorted(L.OUT.glob("*/oxygen/calibration_record.json")) \
        + sorted(L.OUT.glob("*/missing.json")) + sorted(L.OUT.glob("*/oxygen/missing.json"))
    return tuple((str(f), f.stat().st_mtime) for f in files)


@st.cache_data(show_spinner=False, max_entries=4)
def load_summary(sig: tuple) -> dict:
    return summarize(load_config(CONFIG), L.OUT)


def status_text(s: dict) -> str:
    return s["label"] if s["level"] is None else f"{s['level']} · {s['label']}"


def badge(s: dict) -> str:
    return f":{LEVEL_COLOR[s['level']]}-badge[{status_text(s)}]"


STATUS_BOOLEANS = ("all_checks_pass", "n_pass", "n_marginal", "n_fail", "n_not_assessed", "set_by_checks")


def compact_summary(g: dict) -> dict:
    """The compact per-instrument view, machine readable: per deployment the flat offset, function, status and, for every check, its
    booleans (passed, within_limit, sets_status), outcome and value; plus the annotation and reference-range booleans."""
    deps = {}
    for d in g["deployments"]:
        key = d["deployment"] if d["deployment"] is not None else f"{d['year']} ({d['calibration']})"
        s = d["status"]
        entry = {"year": d["year"], "cast": d["cast"], "calibrated": d["calibrated"],
                 "flat_offset": (d.get("flat_offset") or {}).get("value"),
                 "function_offset": (d.get("function_offset") or {}).get("apply"),
                 "status": {"level": s["level"], "label": s["label"], **({"reason": s["reason"]} if "reason" in s else {}),
                            **({k: s[k] for k in STATUS_BOOLEANS} if "all_checks_pass" in s else {})}}
        if d.get("checks"):
            entry["checks"] = {c["criterion"]: {"severity": c["severity"], "outcome": c["outcome"], "assessed": c["assessed"],
                                                "passed": c["passed"], "within_limit": c["within_limit"], "sets_status": c["sets_status"],
                                                "value": c["value"], "units": c["units"], "summary": c["summary"]} for c in d["checks"]}
            entry["annotations"] = {k: d["annotations"][k] for k in ("has_annotations_on_samples", "has_exclusion_annotation")}
            entry["reference_extrapolated"] = d["basis"]["reference_extrapolated"]
        deps[key] = entry
    return {"refdes": g["refdes"], "deployments": deps}


def _details(det: dict) -> str:
    return "; ".join(f"{k}={json.dumps(v) if isinstance(v, (dict, list)) else v}" for k, v in det.items() if v is not None)


summary = load_summary(_signature())
crit = summary["criteria"]
labels = {int(k): v for k, v in crit["level_labels"].items()}

st.title("Deployments")
st.caption("One entry per instrument and deployment, read from the committed run outputs. Each check is graded **pass**, "
           "**marginal** or **fail** against its target and limit; its **severity** decides how far that can lower the status, and "
           "the deployment takes the worst level reached. Targets, limits and severities are in `[deployments]` of the config and are "
           "provisional.")

with st.expander("How the status is set"):
    sev = crit["severity_levels"]
    st.markdown("  \n".join(f":{LEVEL_COLOR[k]}-badge[{k} · {v}]" for k, v in sorted(labels.items())))
    st.dataframe(pd.DataFrame([{"severity": s, "marginal check gives level": v["marginal"], "failed check gives level": v["fail"]}
                               for s, v in sev.items()]), hide_index=True, alt="Status level reached by a marginal or failed check of each severity")
    for var in ("salinity", "oxygen"):
        st.markdown(f"**{var.capitalize()} checks**")
        st.dataframe(pd.DataFrame([{"check": k, "severity": v["severity"], "target": v["target"], "limit": v["limit"],
                                    "better": v.get("better", "lower")} for k, v in crit[var].items()]),
                     hide_index=True, alt=f"Targets, limits and severities of the {var} checks")

variable = st.segmented_control("Variable", ["Salinity", "Oxygen"], default="Salinity", key="dep_variable")
groups = [g for g in summary["instruments"] if g["variable"] == (variable or "Salinity").lower()]

with st.container(horizontal=True):
    st.download_button("Download YAML", to_yaml(summary), file_name="deployments.yaml", mime="text/yaml",
                       icon=":material/download:")
    st.download_button("Download JSON", json.dumps(summary, indent=2), file_name="deployments.json", mime="application/json",
                       icon=":material/download:")

for g in groups:
    st.subheader(f"{g['site_name'] or g['site']}  ·  `{g['refdes']}`")
    done = [d for d in g["deployments"] if d["calibration"] == "calibrated"]
    with st.container(horizontal=True):
        st.metric("Deployments calibrated", f"{len(done)} of {len(g['deployments'])}", border=True)
        for lv, name in sorted(labels.items()):
            st.metric(name.capitalize(), sum(d["status"]["level"] == lv for d in done), border=True)
    rows = [{
        "deployment": d["deployment"], "year": d["year"], "cast": d["cast"], "status": status_text(d["status"]),
        "all checks pass": d["status"].get("all_checks_pass"),
        "flat offset": (d.get("flat_offset") or {}).get("value"),
        "function": (d.get("function_offset") or {}).get("apply"),
        "set by": "; ".join(f"{c['criterion']} {c['outcome']}" for c in d.get("checks", [])
                           if c.get("level") == d["status"]["level"] and c["outcome"] in ("marginal", "fail"))
                  or d["status"].get("reason", ""),
    } for d in g["deployments"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True, alt=f"Deployments of {g['refdes']} with offset, function and status",
                 column_config={"flat offset": st.column_config.NumberColumn(format="%+.4f"),
                                "deployment": st.column_config.NumberColumn(format="%d")})
    with st.expander("Checks behind each status"):
        for d in done:
            s = d["status"]
            st.markdown(f"**Deployment {d['deployment']}** ({d['year']}, {d['cast']}, variant `{d['basis']['variant']}`) {badge(s)}")
            for line in s["set_by"]:
                st.markdown(f"- sets the status: {line}")
            for line in s["other_issues"]:
                st.markdown(f"- also: {line}")
            st.dataframe(pd.DataFrame([{
                "check": c["criterion"], "severity": c["severity"], "outcome": c["outcome"], "level": c["level"],
                "passed": c["passed"], "within limit": c["within_limit"], "sets status": c["sets_status"],
                "value": c["value"], "units": c["units"], "target": c["target"], "limit": c["limit"],
                "margin to target": c["margin_to_target"], "margin to limit": c["margin_to_limit"],
                "result": c["summary"], "details": _details(c["details"]),
            } for c in d["checks"]]).astype({"value": str, "margin to target": str, "margin to limit": str}), hide_index=True,
                alt=f"Checks for deployment {d['deployment']}: severity, outcome, value against target and limit, and the numbers behind it",
                column_config={"result": st.column_config.TextColumn(width="large"),
                               "details": st.column_config.TextColumn(width="large")})
    with st.expander("Compact summary (YAML)"):
        st.code(to_yaml(compact_summary(g)), language="yaml")

st.caption(f"Generated {summary['generated_utc']}. Margins are positive inside the target or limit and negative outside it. "
           f"Conventions: {summary['conventions']}")
