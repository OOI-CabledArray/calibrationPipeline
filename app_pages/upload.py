from pathlib import Path

import streamlit as st

import app_lib as L
from rca_sal import uploads as U

settings = U.upload_settings(L.OUT.parent / "config" / "cruises.toml")

st.title("Run your files")
st.caption("Upload a verification cruise's files and run the same salinity and oxygen pipelines the repository outputs came from. "
           "Files are kept only in a temporary workspace for this browser session; nothing is added to the repository.")

with st.expander("Which files are needed?", icon=":material/help:"):
    st.markdown(
        "**Salinity** needs all five of: the profiler CTDPFA NetCDF, the shipboard `.cnv` and `.btl`, the Autosal workbook and the "
        "cast log. **Oxygen** needs the DOFSTA NetCDF and the Winkler workbook plus the same `.cnv` and `.btl`. Supply either set or both. "
        "The `.cnv` must contain `sal00`, `prDM` and `depSM` (and `sbeox0ML/L`, `t090C` for oxygen) and hold the downcast.")

with st.form("upload_form"):
    st.subheader("Cruise and cast")
    with st.container(horizontal=True):
        year = st.number_input("Year", min_value=2010, max_value=2100, step=1, value=None, placeholder="2025")
        cast = st.text_input("CTD cast name", placeholder="CTD-007", help="As written in the cast log and the Winkler workbook.")
        cruise = st.text_input("Cruise", placeholder="Cabled-16")
        ship_id = st.text_input("Ship cruise ID", placeholder="AT50-41")
    st.subheader("Salinity files")
    up = {}
    for role, (label, suffixes) in U.SALINITY_ROLES.items():
        up[role] = st.file_uploader(label, type=[s.lstrip(".") for s in suffixes], key=f"up_{role}")
    st.subheader("Oxygen files")
    for role, (label, suffixes) in U.OXYGEN_ROLES.items():
        up[role] = st.file_uploader(label, type=[s.lstrip(".") for s in suffixes], key=f"up_{role}")
    with st.expander("Optional: reproduce a notebook window"):
        st.caption("UTC start and end of the profiler file the notebook used, e.g. 2025-08-18T00:13:29Z. Needed only for the exact-reproduction variant.")
        with st.container(horizontal=True):
            sal_start = st.text_input("Salinity window start")
            sal_end = st.text_input("Salinity window end")
        with st.container(horizontal=True):
            ox_start = st.text_input("Oxygen window start")
            ox_end = st.text_input("Oxygen window end")
    submitted = st.form_submit_button("Run pipelines", type="primary", icon=":material/play_arrow:")

if submitted:
    windows = {"salinity": (sal_start.strip(), sal_end.strip()) if sal_start.strip() and sal_end.strip() else None,
               "oxygen": (ox_start.strip(), ox_end.strip()) if ox_start.strip() and ox_end.strip() else None}
    files = {r: (f.name, f.getvalue()) for r, f in up.items() if f is not None}
    meta = {"year": year, "cast": cast, "cruise": cruise.strip(), "ship_cruise_id": ship_id.strip()}
    st.session_state.pop("upload_result", None)
    try:
        if "workspace" not in st.session_state:
            st.session_state["workspace"] = str(U.new_workspace(settings["workspace_max_age_h"]))
        with st.status("Running pipelines", expanded=True) as status:
            res = U.run_uploaded(Path(st.session_state["workspace"]), files, meta, windows, L.OUT.parent / "config" / "cruises.toml")
            for name in res.records:
                st.write(f":material/check_circle: {name} finished")
            for name, why in res.errors.items():
                st.write(f":material/error: {name} failed")
            status.update(label="Finished" if not res.errors else "Finished with errors", state="error" if res.errors and not res.records else "complete")
        if res.records:
            L.uploaded_runs()[res.label] = str(res.outputs_root)
        st.session_state["upload_result"] = res
        if res.records:
            st.session_state["_select_run"] = res.label
            st.rerun()
    except U.UploadError as e:
        st.error(str(e), icon=":material/error:")

res = st.session_state.get("upload_result")
if res is not None:
    st.subheader(f"Result: {res.label}")
    for name, rec in res.records.items():
        prim = next(v for v in rec["rca:variants"] if v["variant_id"] == rec["rca:primaryVariant"])
        with st.container(border=True):
            st.markdown(f"**{name.capitalize()}**, primary variant `{prim['variant_id']}`")
            with st.container(horizontal=True):
                if name == "salinity":
                    st.metric("Offset (PSU)", L.fmt(prim["offset_psu"], signed=True))
                    st.metric("IQR (PSU)", L.fmt(prim["iqr_psu"]))
                    st.metric("AN31 slope", L.fmt(prim["an31_slope"], 6))
                else:
                    st.metric("CTD gain", L.fmt(prim["ctd_gain"], 5))
                    st.metric("DOFSTA gain", L.fmt(prim["dofsta_gain"], 5))
                    st.metric("RMSE after (umol/kg)", L.fmt(prim["rmse_after_umolkg"], 2))
            for f in rec["rca:flags"]:
                if f["severity"] == "warning":
                    st.warning(f["text"])
    for name, msg in res.errors.items():
        st.error(f"**{name.capitalize()} failed.** {msg}", icon=":material/error:")
    for name, why in res.skipped.items():
        st.info(f"{name.capitalize()} skipped: {why}.", icon=":material/info:")
    if res.records:
        st.caption("The run is now selectable as the year in the sidebar; open any page to see it.")
        st.download_button("Download all outputs (zip)", U.zip_outputs(res.outputs_root, int(res.label.split()[0])),
                           file_name=f"{res.label.split()[0]}_uploaded_outputs.zip", mime="application/zip", icon=":material/download:")
