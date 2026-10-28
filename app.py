"""Streamlit viewer for the calibration outputs in outputs/<run>/ and for runs on uploaded files."""
from pathlib import Path

import streamlit as st

import app_lib

st.set_page_config(page_title="RCA calibration", page_icon=str(Path(__file__).parent / "assets" / "icons" / "icon-192.png"), layout="wide")

# A run finished on the upload page is selected before the selector is created.
if (pending := st.session_state.pop("_select_run", None)) is not None:
    st.session_state["year"] = pending

runs = app_lib.years_on_disk()
st.html("<style>[data-testid='stSidebar'] { min-width: min(27rem, 90vw); }</style>")   # Sidebar width that fits the full run label.
if runs:
    # The URL carries the run key (?year=2022-RS01SBPS). bind="query-params" would put the display label in the URL when format_func is set.
    if "year" not in st.session_state and st.query_params.get("year") in runs:
        st.session_state["year"] = st.query_params["year"]
    st.sidebar.selectbox("Run: year, site, instrument", runs, key="year", format_func=app_lib.run_label)
    st.query_params["year"] = st.session_state["year"]
    st.sidebar.caption(app_lib.run_caption(st.session_state["year"]))

page = st.navigation({
    "Summary": [
        st.Page("app_pages/deployments.py", title="Deployments", icon=":material/fact_check:"),
    ],
    "Salinity": [
        st.Page("app_pages/bottles.py", title="CTD vs Autosal", icon=":material/science:", default=True),
        st.Page("app_pages/profiler.py", title="Profiler vs CTD", icon=":material/vertical_align_bottom:"),
    ],
    "Oxygen": [
        st.Page("app_pages/oxygen_winkler.py", title="CTD vs Winkler", icon=":material/biotech:"),
        st.Page("app_pages/oxygen_dofsta.py", title="DOFSTA vs CTD", icon=":material/air:"),
    ],
    "Your data": [
        st.Page("app_pages/upload.py", title="Run your files", icon=":material/upload_file:"),
    ],
    "Provenance": [
        st.Page("app_pages/provenance.py", title="Provenance and export", icon=":material/fingerprint:"),
    ],
})
page.run()
