from pathlib import Path

import pandas as pd
import streamlit as st

import app_lib as L

st.title("Provenance and export")
choice = st.segmented_control("Variable", ["Salinity", "Oxygen", "Nitrate"], default="Salinity", key="prov_variable", required=True)
variable = choice.lower()
year, rec = L.require_year(variable)
run = rec["prov:wasGeneratedBy"]
L.header(None, year, rec, variable)
st.caption(f"Record {rec['@id']} · created {rec['dateCreated']} · OOINet request folder `{rec['rca:ooinet']['request_folder']}`")

mime = {".json": "application/ld+json", ".csv": "text/csv", ".parquet": "application/octet-stream"}
with st.container(horizontal=True):
    for n in ["calibration_record.json"] + [o["name"] for o in rec["rca:outputs"]]:
        p = L.folder(year, variable) / n
        if p.is_file():
            st.download_button(n, L.load_bytes(str(p), L.mtime(year, n, variable)), file_name=f"{year}_{variable}_{n}",
                               mime=mime.get(Path(n).suffix, "text/plain"), icon=":material/download:", key=f"dl_{variable}_{n}")

nf = len(rec["rca:flags"])
inputs, outputs, flags, params, code, full = st.tabs(["Inputs", "Outputs", f"Flags ({nf})", "Parameters", "Code", "Full record"])
with inputs:
    st.dataframe(pd.DataFrame([{"role": u["rca:role"], "file": u["name"], "repository path or GitHub URL": u["contentUrl"], "sha256": u["sha256"],
                                "bytes": u["contentSize"], "modified_utc": u["dateModified"]} for u in run["prov:used"]]),
                 hide_index=True, alt="Input files with repository path or GitHub URL, SHA-256, size and modification time")
    st.markdown("**Input checks**")
    st.json(rec["rca:inputChecks"], expanded=False)
with outputs:
    st.dataframe(pd.DataFrame(rec["rca:outputs"]), hide_index=True, alt="Output files with SHA-256 and size")
with flags:
    L.flag_list(rec)
with params:
    left, right = st.columns(2)
    with left:
        st.markdown("**Parameters**")
        st.json(rec["rca:parameters"], expanded=False)
    with right:
        st.markdown("**Cast and T1 / T2**")
        st.json({"cruise": rec["rca:cruise"], "twoPoint": rec["rca:twoPoint"]}, expanded=False)
with code:
    st.dataframe(pd.DataFrame(run["prov:wasAssociatedWith"]["rca:codeFiles"]), hide_index=True, alt="Code files with SHA-256")
    st.markdown("**Runtime**")
    st.json(run["prov:wasAssociatedWith"]["rca:runtime"], expanded=False)
with full:
    st.json(rec, expanded=False)
