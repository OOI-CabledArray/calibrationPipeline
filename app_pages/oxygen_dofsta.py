import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import app_lib as L

year, rec = L.require_year("oxygen")
tab = L.get(year, "calibration_table.csv", variable="oxygen")
prof = L.get(year, "binned_profiles.csv", variable="oxygen")
wk = L.get(year, "winkler_check.csv", variable="oxygen")
cp = L.get(year, "ctd_o2_profiles.csv", variable="oxygen")
if any(x is None for x in (tab, prof, wk, cp)):
    st.error("calibration_table, binned_profiles, winkler_check or ctd_o2_profiles is missing.", icon=":material/error:")
    st.stop()

cr = rec["rca:cruise"]
main = tab[tab.role != "per_upcast"].set_index("variant_id")
vid = st.sidebar.selectbox("Variant", list(main.index), key="o2_variant_d",
                           index=int(np.flatnonzero(main.index == rec["rca:primaryVariant"])[0]),
                           format_func=lambda v: f"{v}  [{main.loc[v, 'role']}]")
r = tab.set_index("variant_id").loc[vid]

st.title(f"{rec['rca:ooinet'].get('reference_designator') or 'DOFSTA'} vs calibrated CTD oxygen ({L.run_site(year)})")
st.caption(f"{r.description}. {int(r.n_dofsta_samples):,} DOFSTA samples, {int(r.n_bins)} depth bins"
           f"{', 5-bin smoothed' if r.smoothed else ', unsmoothed'}.")
with st.container(horizontal=True):
    st.metric("DOFSTA gain", L.fmt(r.dofsta_gain, 5), border=True,
              help="median(Winkler-calibrated CTD / DOFSTA) over depth bins; corrected = raw x gain. It already includes the CTD gain, so do not multiply by it again.")
    st.metric("IQR of ratios", L.fmt(r.dofsta_gain_iqr), border=True)
    st.metric("RMSE before (umol/kg)", L.fmt(r.rmse_before_umolkg, 2), border=True)
    st.metric("RMSE after gain (umol/kg)", L.fmt(r.rmse_after_umolkg, 2), delta=L.fmt(r.rmse_after_umolkg - r.rmse_before_umolkg, 2, signed=True),
              delta_color="inverse", border=True)
with st.container(horizontal=True):
    st.metric("CTD gain (step 1)", L.fmt(r.ctd_gain, 5), border=True)
    st.metric("DOFSTA vs Winkler, direct", L.fmt(r.dofsta_vs_winkler_direct_gain, 5), border=True,
              help="median(Winkler / DOFSTA at the Winkler depths), skipping the CTD: a cross-check on the two-step gain")
st.caption(f"T1 time: DOFSTA mean {r.t1_mean_profiler_time_utc[:19]} UTC; CTD cast {r.t1_cast_time_utc[:19]} UTC ({r.mean_profiler_minus_cast_h:+.1f} h).")

v = prof[prof.variant_id == vid]
up = prof[prof.variant_id.str.fullmatch(r"upcast_\d+_depSM")]
c = cp[cp.variant_id == vid].sort_values("depth_m")
w = wk[wk.variant_id == vid]
wz = w.winkler_depth_m if r.winkler_depth_col == "depth_m" else w.winkler_pressure_dbar
fig = make_subplots(rows=1, cols=2, column_widths=[0.68, 0.32], shared_yaxes=True, horizontal_spacing=0.03,
                    subplot_titles=("Oxygen (umol/kg)", "Residual vs calibrated CTD (umol/kg)"))
for i, (_, u) in enumerate(up.groupby("variant_id")):
    fig.add_trace(go.Scatter(x=u.raw, y=u.depth_bin_m, mode="lines", line=dict(color=L.C_UP, width=1), name="raw upcasts",
                             legendgroup="up", showlegend=i == 0,
                             hovertemplate=u.variant_id.iloc[0] + "<br>%{x:.1f} @ %{y} m<extra></extra>"), 1, 1)
fig.add_trace(go.Scatter(x=c.o2_ctd_calibrated_umolkg, y=c.depth_m, mode="lines", line=dict(color=L.C_REF, width=2.5), name="CTD x CTD gain"), 1, 1)
fig.add_trace(go.Scatter(x=w.o2_winkler_umolkg, y=wz, mode="markers", name="Winkler",
                         marker=dict(size=9, color=L.C_BOTTLE, symbol="square", line=dict(width=1, color="black"))), 1, 1)
fig.add_trace(go.Scatter(x=v.raw, y=v.depth_bin_m, mode="lines", line=dict(color=L.C_RAW, dash="dash", width=2), name="DOFSTA raw"), 1, 1)
fig.add_trace(go.Scatter(x=v.corrected, y=v.depth_bin_m, mode="lines", line=dict(color=L.C_OFF, width=2), name="DOFSTA x gain"), 1, 1)
for col, color, dash in (("raw", L.C_RAW, "dash"), ("corrected", L.C_OFF, "solid")):
    fig.add_trace(go.Scatter(x=v[col] - v.reference, y=v.depth_bin_m, mode="lines", line=dict(color=color, dash=dash, width=1.5), showlegend=False), 1, 2)
fig.add_vline(x=0, line_color="#555555", line_dash="dot", col=2, row=1)
fig.update_yaxes(autorange="reversed", title="depth (m)", col=1, row=1)
fig.update_yaxes(autorange="reversed", col=2, row=1)
fig.update_xaxes(title="O2 (umol/kg)", col=1, row=1)
fig.update_xaxes(title="DOFSTA - CTD (umol/kg)", col=2, row=1)
L.style(fig, height=650, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=-0.1))
st.plotly_chart(fig, theme=None, alt="Oxygen against depth for the Winkler-calibrated CTD, Winkler samples, raw DOFSTA upcasts and the raw and gain-corrected DOFSTA profile, "
                                       "with the residual against the calibrated CTD alongside")

st.subheader("Gain upcast by upcast")
pu = tab[tab.role == "per_upcast"].copy()
pu["t"] = pd.to_datetime(pu.t1_mean_profiler_time_utc, utc=True)
f2 = go.Figure()
f2.add_trace(go.Scatter(x=pu.t, y=pu.dofsta_gain, mode="markers+text", text=[f"#{int(k)}" for k in pu.upcast_id], textposition="top center",
                        marker=dict(size=10, color=L.C_OFF), error_y=dict(type="data", array=pu.dofsta_gain_iqr / 2, color="#444444", thickness=1.5),
                        hovertemplate="upcast %{text}<br>gain %{y:.4f}<extra></extra>"))
f2.add_hline(y=r.dofsta_gain, line_color=L.C_REF, line_dash="dash", annotation_text=f"{vid}: {r.dofsta_gain:.4f}")
f2.add_vline(x=pd.Timestamp(r.t1_cast_time_utc).to_pydatetime(), line_color=L.C_BOTTLE, line_dash="dot", annotation_text="CTD cast")
f2.update_yaxes(title="DOFSTA gain")
f2.update_xaxes(title="mean time of upcast (UTC)")
L.style(f2, height=340, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
st.plotly_chart(f2, theme=None, alt="DOFSTA gain estimated from each upcast against its mean time, with the CTD cast time and the selected variant's gain marked")
st.caption("Error bars are plus or minus half the IQR of that upcast's per-bin ratios.")

st.subheader("All variants")
cols = ["variant_id", "role", "ctd_gain", "dofsta_gain", "dofsta_gain_iqr", "rmse_before_umolkg", "rmse_after_umolkg",
        "dofsta_vs_winkler_direct_gain", "n_dofsta_samples", "n_winkler", "t1_mean_profiler_time_utc"]
st.dataframe(tab[cols], hide_index=True, alt="Gains and RMSE for every oxygen variant",
             column_config={c: st.column_config.NumberColumn(format="%.4f") for c in cols if "gain" in c or "rmse" in c})
t2 = rec["rca:twoPoint"]["T2"]
if t2.get("status") == "missing":
    st.warning(f"T2 missing: {t2['reason']}", icon=":material/event_busy:")
L.show_flags(rec, {"ooinet_annotation", "ctd_o2_converted"})
