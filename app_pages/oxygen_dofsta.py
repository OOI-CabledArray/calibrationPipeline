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
L.missing("calibration_table, binned_profiles, winkler_check or ctd_o2_profiles", tab, prof, wk, cp)

L.header("DOFSTA vs calibrated CTD", year, rec, "oxygen")
main = tab[tab.role != "per_upcast"].set_index("variant_id")
vid = L.pick_variant("Variant", list(main.index), rec["rca:primaryVariant"], "o2_variant_d", lambda v: f"{v}  [{main.loc[v, 'role']}]")
r = main.loc[vid]

L.answer(f"**Correction: multiply the DOFSTA oxygen by {L.fmt(r.dofsta_gain, 5)}** (median of Winkler-calibrated CTD / DOFSTA over "
         f"{int(r.n_bins)} depth bins). The gain already includes the CTD gain, so do not multiply by that again.")
with st.container(horizontal=True):
    with L.card("DOFSTA gain") as m:
        m("DOFSTA gain", L.fmt(r.dofsta_gain, 5),
          help="median(Winkler-calibrated CTD / DOFSTA) over depth bins; corrected = raw x gain. It already includes the CTD gain.")
        m("IQR of ratios", L.fmt(r.dofsta_gain_iqr))
        m("RMSE before (µmol/kg)", L.fmt(r.rmse_before_umolkg, 2))
        m("RMSE after (µmol/kg)", L.fmt(r.rmse_after_umolkg, 2), delta=L.fmt(r.rmse_after_umolkg - r.rmse_before_umolkg, 2, signed=True),
          delta_color="inverse")
    with L.card("Cross-checks") as m:
        m("CTD gain (step 1)", L.fmt(r.ctd_gain, 5), help="median(Winkler / CTD): the gain of the ship's SBE43")
        m("Direct vs Winkler", L.fmt(r.dofsta_vs_winkler_direct_gain, 5),
          help="median(Winkler / DOFSTA at the Winkler depths), skipping the CTD: a cross-check on the two-step gain")
st.caption(f"{r.description}. {int(r.n_dofsta_samples):,} DOFSTA samples, {int(r.n_bins)} depth bins{', 5-bin smoothed' if r.smoothed else ', unsmoothed'}. "
           f"DOFSTA mean time {r.t1_mean_profiler_time_utc[:19]} UTC, CTD cast {r.t1_cast_time_utc[:19]} UTC ({r.mean_profiler_minus_cast_h:+.1f} h).")
L.show_flags(rec, {"ooinet_annotation", "ctd_o2_converted"}, L.t2_notes(rec))

v = prof[prof.variant_id == vid]
up = prof[prof.variant_id.str.fullmatch(r"upcast_\d+_depSM")]
zmax = v.depth_bin_m.max() + 10                                 # plot the profiler's depth range; a full-depth ship cast would squash it
c = cp[(cp.variant_id == vid) & (cp.depth_m <= zmax)].sort_values("depth_m")
w = wk[wk.variant_id == vid]
wz = w.winkler_depth_m if r.winkler_depth_col == "depth_m" else w.winkler_pressure_dbar
w, wz = w[wz <= zmax], wz[wz <= zmax]
fig = make_subplots(rows=1, cols=2, column_widths=[0.68, 0.32], shared_yaxes=True, horizontal_spacing=0.03,
                    subplot_titles=("Oxygen (µmol/kg)", "Residual vs calibrated CTD (µmol/kg)"))
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
fig.update_yaxes(range=[zmax, 0])
fig.update_yaxes(title="depth (m)", col=1, row=1)
fig.update_xaxes(title="O2 (µmol/kg)", col=1, row=1)
fig.update_xaxes(title="DOFSTA - CTD (µmol/kg)", col=2, row=1)
L.style(fig, height=650, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=-0.1))
st.plotly_chart(fig, theme=None, alt="Oxygen against depth for the Winkler-calibrated CTD, Winkler samples, raw DOFSTA upcasts and the raw and gain-corrected DOFSTA profile, "
                                       "with the residual against the calibrated CTD alongside")

per_upcast, variants = st.tabs(["Upcast by upcast", "All variants"])
with per_upcast:
    pu = tab[tab.role == "per_upcast"]
    st.plotly_chart(L.per_cast_chart(pd.to_datetime(pu.t1_mean_profiler_time_utc, utc=True), pu.dofsta_gain, pu.dofsta_gain_iqr / 2, pu.upcast_id,
                                     "upcast", "gain %{y:.4f}", "DOFSTA gain", "mean time of upcast (UTC)", r.t1_cast_time_utc,
                                     ref=r.dofsta_gain),
                    theme=None, alt="DOFSTA gain estimated from each upcast against its mean time, with the CTD cast time and the selected variant's gain marked")
    st.caption(f"Error bars are plus or minus half the IQR of that upcast's per-bin ratios. Dashed line: {vid} ({r.dofsta_gain:.4f}).")
with variants:
    cols = ["variant_id", "role", "ctd_gain", "dofsta_gain", "dofsta_gain_iqr", "rmse_before_umolkg", "rmse_after_umolkg",
            "dofsta_vs_winkler_direct_gain", "n_dofsta_samples", "n_winkler", "t1_mean_profiler_time_utc"]
    st.dataframe(tab[cols], hide_index=True, alt="Gains and RMSE for every oxygen variant",
                 column_config={c: st.column_config.NumberColumn(format="%.4f") for c in cols if "gain" in c or "rmse" in c})
