import plotly.graph_objects as go
import streamlit as st

import app_lib as L

year, rec = L.require_year()
t = L.get(year, "bottle_check.csv")
L.missing("bottle_check.csv", t)
bc = rec["rca:bottleCheck"]
prim = next((v for v in rec["rca:variants"] if v["variant_id"] == rec["rca:primaryVariant"]), {})

L.header("CTD vs Autosal", year, rec, instrument=False)
L.answer("**Ship CTD minus bottle salinity:** "
         + ", ".join(f"{s['sensor']} {L.fmt(s['median_diff_psu'], signed=True)} PSU (median of {s['n']})" for s in bc.values())
         + (f". The profiler is compared with `{prim['reference_sal_col']}`." if prim.get("reference_sal_col") else "."))
st.caption("CTD minus Autosal at the salinity Niskins: the check on the reference before it is used for the profiler.")

with st.container(horizontal=True):
    for s in bc.values():
        with L.card(f"{s['sensor']} · n = {s['n']}") as m:
            m("Median CTD-bottle (PSU)", L.fmt(s["median_diff_psu"], signed=True))
            m("Mean (PSU)", L.fmt(s["mean_diff_psu"], signed=True))
            m("RMSE (PSU)", L.fmt(s["rmse_psu"]))
            m("AN31 slope", L.fmt(s["an31_slope"], 5))
            if wo := s.get("without_outliers"):
                st.caption(f"Without {s['n_outliers_flagged']} flagged bottle(s): median {L.fmt(wo['median_diff_psu'], signed=True)}, "
                           f"RMSE {L.fmt(wo['rmse_psu'])} (n = {wo['n']})")
L.show_flags(rec, {"bottle_outlier", "bottle_excluded_by_flag", "autosal_header_date", "autosal_other_year", "ctd_downcast_only", "ctd_processing"})

fig = go.Figure()
for sensor, color, sym in (("sal00", L.C_OFF, "circle"), ("sal11", L.C_SLOPE, "diamond")):
    d = t[t.sensor == sensor]
    fig.add_trace(go.Scatter(
        x=d.diff_psu, y=d.DepSM, mode="markers+text", name=sensor, text=[f"Niskin {n}" for n in d.niskin],
        textposition="middle right", cliponaxis=False,
        marker=dict(size=11, color=color, symbol=sym, line=dict(width=[2 if o else 0 for o in d.outlier], color="black")),
        hovertemplate="%{text}<br>CTD-bottle %{x:+.4f} PSU<br>%{y:.0f} m<extra>" + sensor + "</extra>"))
fig.add_vline(x=0, line_color="#555555", line_dash="dot")
fig.update_yaxes(autorange="reversed", title="depSM at bottle closure (m)")
fig.update_xaxes(title="CTD salinity minus Autosal salinity (PSU)", zeroline=False)
L.style(fig, height=430, margin=dict(l=10, r=80, t=10, b=10), legend=dict(orientation="h", y=1.08))
st.plotly_chart(fig, theme=None, alt="Scatter of CTD minus Autosal salinity against depth at each salinity Niskin, for the primary and secondary CTD sensors")
st.caption("Black outline: more than the configured threshold from that sensor's median.")

st.subheader("Bottles")
st.dataframe(
    t.drop(columns=["record_id", "year"], errors="ignore"), hide_index=True,
    alt="Per-bottle comparison of CTD and Autosal salinity",
    column_config={c: st.column_config.NumberColumn(format="%.4f") for c in
                   ("diff_psu", "bottle_salinity", "Sal00", "Sal11", "ctd_sal_gradient_psu_per_m")})
