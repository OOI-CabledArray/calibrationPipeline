import json

import pandas as pd
import streamlit as st

import app_lib as L
from rca_sal.deployments import to_yaml

LEVEL_TINT = {1: "#E8F5E9", 2: "#E3F2FD", 3: "#FFF3E0", 4: "#FFEBEE"}
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
            if "reference_extrapolated" in d["basis"]:
                entry["reference_extrapolated"] = d["basis"]["reference_extrapolated"]
        deps[key] = entry
    return {"refdes": g["refdes"], "deployments": deps}


def _details(det: dict) -> str:
    return "; ".join(f"{k}={json.dumps(v) if isinstance(v, (dict, list)) else v}" for k, v in det.items() if v is not None)


summary = L.summary()
crit = summary["criteria"]
labels = {int(k): v for k, v in crit["level_labels"].items()}

st.title("Deployments")
st.caption("One entry per instrument and deployment, read from the committed run outputs. Each check is graded **pass**, "
           "**marginal** or **fail** against its target and limit; its **severity** decides how far that can lower the status, and "
           "the deployment takes the worst level reached. Targets, limits and severities are in `[deployments]` of the config and are "
           "provisional.")

with st.container(horizontal=True, vertical_alignment="bottom"):
    variable = st.segmented_control("Variable", ["Salinity", "Oxygen", "Nitrate"], default="Salinity", key="dep_variable", required=True)
    st.space("stretch")
    st.download_button("YAML", to_yaml(summary), file_name="deployments.yaml", mime="text/yaml", icon=":material/download:",
                       help="Full summary of every instrument, deployment and check")
    st.download_button("JSON", json.dumps(summary, indent=2), file_name="deployments.json", mime="application/json",
                       icon=":material/download:", help="Full summary of every instrument, deployment and check")

with st.expander("How the status is set", icon=":material/help:"):
    sev = crit["severity_levels"]
    st.markdown("  \n".join(L.badge({"level": k, "label": v}) for k, v in sorted(labels.items())))
    st.dataframe(pd.DataFrame([{"severity": s, "marginal check gives level": v["marginal"], "failed check gives level": v["fail"]}
                               for s, v in sev.items()]), hide_index=True, alt="Status level reached by a marginal or failed check of each severity")
    for var in ("salinity", "oxygen", "nitrate"):
        st.markdown(f"**{var.capitalize()} checks**")
        st.dataframe(pd.DataFrame([{"check": k, "severity": v["severity"], "target": v["target"], "limit": v["limit"],
                                    "better": v.get("better", "lower")} for k, v in crit[var].items()]),
                     hide_index=True, alt=f"Targets, limits and severities of the {var} checks")

for g in [g for g in summary["instruments"] if g["variable"] == variable.lower()]:
    done = [d for d in g["deployments"] if d["calibration"] == "calibrated"]
    with st.container(border=True):
        st.subheader(f"{g['site_name'] or g['site']}  ·  `{g['refdes']}`")
        st.markdown(f"{len(done)} of {len(g['deployments'])} deployments calibrated: "
                    + " ".join(f":{L.LEVEL_COLOR[lv]}-badge[{sum(d['status']['level'] == lv for d in done)} {name}]" for lv, name in sorted(labels.items())))
        rows = pd.DataFrame([{
            "deployment": d["deployment"], "year": d["year"], "cast": d["cast"], "status": L.status_text(d["status"]),
            "set by": "; ".join(f"{c['criterion']} {c['outcome']}" for c in d.get("checks", [])
                               if c.get("level") == d["status"]["level"] and c["outcome"] in ("marginal", "fail"))
                      or d["status"].get("reason", ""),
            "offset to add": (d.get("flat_offset") or {}).get("value"),
            "function": (d.get("function_offset") or {}).get("apply"),
            "all checks pass": d["status"].get("all_checks_pass"),
        } for d in g["deployments"]])
        tint = [f"background-color: {LEVEL_TINT[lv]}" if (lv := d["status"]["level"]) in LEVEL_TINT else "" for d in g["deployments"]]
        st.dataframe(rows.style.apply(lambda _: tint, subset=["status"]), hide_index=True,
                     alt=f"Deployments of {g['refdes']} with offset, function and status",
                     column_config={"offset to add": st.column_config.NumberColumn(
                                        format="%+.4f", help="Flat correction: corrected = raw + this value (salinity: minus the profiler page's median(profiler - CTD))"),
                                    "deployment": st.column_config.NumberColumn(format="%d"),
                                    "set by": st.column_config.TextColumn(width="medium")})
        with st.expander("Checks behind each status", type="compact"):
            for d in done:
                s = d["status"]
                st.markdown(f"**Deployment {d['deployment']}** ({d['year']}, {d['cast']}, variant `{d['basis']['variant']}`) {L.badge(s)}")
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
        with st.expander("Compact summary (YAML)", type="compact"):
            st.code(to_yaml(compact_summary(g)), language="yaml")

st.caption(f"Generated {summary['generated_utc']}. Margins are positive inside the target or limit and negative outside it. "
           f"Conventions: {summary['conventions']}")
