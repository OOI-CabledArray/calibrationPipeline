import plotly.graph_objects as go
import streamlit as st

import app_lib as L

year, rec = L.require_year()
cr = rec["rca:cruise"]
st.title(f"CTD vs Autosal, {cr['cruise']} {cr['ship_cruise_id']} {cr['cast']} ({L.run_site(year)})")
st.caption("CTD minus bottle at the salinity Niskins. This is the check on the reference before it is used for the profiler.")

bc = rec["rca:bottleCheck"]
with st.container(horizontal=True):
    for s in bc.values():
        with st.container(border=True):
            st.markdown(f"**{s['sensor']}** (n = {s['n']})")
            with st.container(horizontal=True):
                st.metric("Median CTD-bottle (PSU)", L.fmt(s["median_diff_psu"], signed=True))
                st.metric("Mean (PSU)", L.fmt(s["mean_diff_psu"], signed=True))
            with st.container(horizontal=True):
                st.metric("RMSE (PSU)", L.fmt(s["rmse_psu"]))
                st.metric("AN31 slope", L.fmt(s["an31_slope"], 5))
            wo = s.get("without_outliers")
            if wo:
                st.caption(f"Without {s['n_outliers_flagged']} flagged bottle(s): median {L.fmt(wo['median_diff_psu'], signed=True)}, "
                           f"RMSE {L.fmt(wo['rmse_psu'])} (n={wo['n']})")

t = L.get(year, "bottle_check.csv")
if t is None:
    st.error("bottle_check.csv is missing.", icon=":material/error:")
    st.stop()

fig = go.Figure()
for sensor, color, sym in (("sal00", L.C_OFF, "circle"), ("sal11", L.C_SLOPE, "diamond")):
    d = t[t.sensor == sensor]
    fig.add_trace(go.Scatter(
        x=d.diff_psu, y=d.DepSM, mode="markers+text", name=sensor, text=[f"Niskin {n}" for n in d.niskin],
        textposition="middle right",
        marker=dict(size=11, color=color, symbol=sym, line=dict(width=[2 if o else 0 for o in d.outlier], color="black")),
        hovertemplate="%{text}<br>CTD-bottle %{x:+.4f} PSU<br>%{y:.0f} m<extra>" + sensor + "</extra>"))
fig.add_vline(x=0, line_color="#555555", line_dash="dot")
fig.update_yaxes(autorange="reversed", title="depSM at bottle closure (m)")
fig.update_xaxes(title="CTD salinity minus Autosal salinity (PSU)", zeroline=False)
L.style(fig, height=430, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.08))
st.plotly_chart(fig, theme=None, alt="Scatter of CTD minus Autosal salinity against depth at each salinity Niskin, for the primary and secondary CTD sensors")
st.caption("Black outline: more than the configured threshold from that sensor's median.")

st.dataframe(
    t.drop(columns=["record_id", "year"], errors="ignore"), hide_index=True,
    alt="Per-bottle comparison of CTD and Autosal salinity",
    column_config={c: st.column_config.NumberColumn(format="%.4f") for c in
                   ("diff_psu", "bottle_salinity", "Sal00", "Sal11", "ctd_sal_gradient_psu_per_m")})
L.show_flags(rec, {"bottle_outlier", "autosal_header_date", "autosal_other_year", "ctd_downcast_only"})
