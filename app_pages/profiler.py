import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import app_lib as L

year, rec = L.require_year()
tab, prof = L.get(year, "calibration_table.csv"), L.get(year, "binned_profiles.csv")
ref, bt, ups = L.get(year, "reference_ctd_profile.csv"), L.get(year, "bottle_check.csv"), L.get(year, "upcasts.csv")
if any(x is None for x in (tab, prof, ref, bt, ups)):
    st.error("One of calibration_table, binned_profiles, reference_ctd_profile, bottle_check or upcasts is missing.", icon=":material/error:")
    st.stop()

main = tab[tab.role != "per_upcast"].set_index("variant_id")
vid = st.sidebar.selectbox(
    "Variant", list(main.index), key="variant",
    index=int(np.flatnonzero(main.index == rec["rca:primaryVariant"])[0]),
    format_func=lambda v: f"{v}  [{main.loc[v, 'role']}]")
r = tab.set_index("variant_id").loc[vid]
cr = rec["rca:cruise"]

st.title(f"Profiler vs reference CTD ({L.run_label(year)})")
st.caption(f"{r.description}. Reference: {cr['cast']} {r.reference_sal_col} on {r.reference_depth_col}, {r.reference_filter} scans. "
           f"{int(r.n_profiler_samples):,} profiler samples, {int(r.n_bins)} depth bins.")

with st.container(horizontal=True):
    st.metric("Offset (PSU)", L.fmt(r.offset_psu, signed=True), help="median(profiler - CTD); corrected = raw - offset", border=True)
    st.metric("IQR (PSU)", L.fmt(r.iqr_psu), border=True)
    st.metric("RMSE before (PSU)", L.fmt(r.rmse_before_psu), border=True)
with st.container(horizontal=True):
    st.metric("RMSE after offset (PSU)", L.fmt(r.rmse_after_offset_common_bins_psu),
              delta=L.fmt(r.rmse_after_offset_common_bins_psu - r.rmse_before_common_bins_psu, signed=True), delta_color="inverse",
              help="Same bins as the slope: those with conductivity.", border=True)
    st.metric("AN31 slope", L.fmt(r.an31_slope, 6), help="Conductivity slope, offset 0; corrected salinity = S(slope x C, T, P).", border=True)
    st.metric("RMSE after slope (PSU)", L.fmt(r.rmse_after_slope_common_bins_psu),
              delta=L.fmt(r.rmse_after_slope_common_bins_psu - r.rmse_before_common_bins_psu, signed=True), delta_color="inverse", border=True)
st.caption(
    f"Bottom row compares offset and slope on identical bins (those with conductivity); RMSE before on those bins is "
    f"{L.fmt(r.rmse_before_common_bins_psu)} PSU and the deltas are against it. Top-row RMSE uses all {int(r.n_bins)} bins as in the "
    f"notebook (after offset: {L.fmt(r.rmse_after_offset_psu)}). T1 time: profiler mean {r.t1_mean_profiler_time_utc[:19]} UTC; "
    f"CTD cast {r.t1_cast_time_utc[:19]} UTC ({r.mean_profiler_minus_cast_h:+.1f} h).")

v = prof[prof.variant_id == vid]
up = prof[prof.variant_id.str.fullmatch(r"upcast_\d+_depSM")]
rd = ref[ref.in_downcast_reference] if r.reference_filter == "downcast" else ref
fig = make_subplots(rows=1, cols=2, column_widths=[0.68, 0.32], shared_yaxes=True, horizontal_spacing=0.03,
                    subplot_titles=("Salinity", "Residual vs reference (PSU)"))
for i, (_, u) in enumerate(up.groupby("variant_id")):
    fig.add_trace(go.Scatter(x=u.raw, y=u.depth_bin_m, mode="lines", line=dict(color=L.C_UP, width=1),
                             name="raw upcasts", legendgroup="up", showlegend=i == 0,
                             hovertemplate=u.variant_id.iloc[0] + "<br>%{x:.3f} PSU @ %{y} m<extra></extra>"), 1, 1)
fig.add_trace(go.Scatter(x=rd[r.reference_sal_col], y=rd[r.reference_depth_col], mode="lines",
                         line=dict(color=L.C_REF, width=2.5), name=f"CTD {r.reference_sal_col}"), 1, 1)
bs = bt[bt.sensor == "sal00"]
fig.add_trace(go.Scatter(x=bs.bottle_salinity, y=bs.DepSM, mode="markers", name="Autosal",
                         marker=dict(size=10, color=L.C_BOTTLE, symbol="square", line=dict(width=1, color="black")),
                         text=[f"Niskin {n} ({s})" for n, s in zip(bs.niskin, bs.sample_id)],
                         hovertemplate="%{text}<br>%{x:.4f} PSU @ %{y:.0f} m<extra></extra>"), 1, 1)
fig.add_trace(go.Scatter(x=v.raw, y=v.depth_bin_m, mode="lines", line=dict(color=L.C_RAW, dash="dash", width=2), name="profiler raw"), 1, 1)
fig.add_trace(go.Scatter(x=v.offset_corrected, y=v.depth_bin_m, mode="lines", line=dict(color=L.C_OFF, width=2), name="offset-corrected"), 1, 1)
fig.add_trace(go.Scatter(x=v.slope_corrected, y=v.depth_bin_m, mode="lines", line=dict(color=L.C_SLOPE, width=2, dash="dot"), name="slope-corrected"), 1, 1)
for col, name, color, dash in (("raw", "raw", L.C_RAW, "dash"), ("offset_corrected", "offset-corrected", L.C_OFF, "solid"),
                               ("slope_corrected", "slope-corrected", L.C_SLOPE, "dot")):
    fig.add_trace(go.Scatter(x=v[col] - v.reference, y=v.depth_bin_m, mode="lines", line=dict(color=color, dash=dash, width=1.5),
                             name=name, showlegend=False), 1, 2)
fig.add_vline(x=0, line_color="#555555", line_dash="dot", col=2, row=1)
fig.update_yaxes(autorange="reversed", title="depth (m)", col=1, row=1)
fig.update_yaxes(autorange="reversed", col=2, row=1)
fig.update_xaxes(title="practical salinity (PSU)", col=1, row=1)
fig.update_xaxes(title="profiler - CTD (PSU)", col=2, row=1)
L.style(fig, height=650, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=-0.1))
st.plotly_chart(fig, theme=None,
                alt="Salinity against depth for the reference CTD, Autosal bottles, raw upcasts and the raw, offset-corrected and slope-corrected "
                    "profiler profile, with the residual against the CTD alongside")

st.subheader("Offset upcast by upcast")
pu = tab[tab.role == "per_upcast"].copy()
pu["t"] = pd.to_datetime(pu.t1_mean_profiler_time_utc, utc=True)
f2 = go.Figure()
f2.add_trace(go.Scatter(x=pu.t, y=pu.offset_psu, mode="markers+text", text=[f"#{int(k)}" for k in pu.upcast_id],
                        textposition="top center", marker=dict(size=10, color=L.C_OFF), name="per-upcast offset",
                        error_y=dict(type="data", array=pu.iqr_psu / 2, color="#444444", thickness=1.5),
                        hovertemplate="upcast %{text}<br>%{y:+.4f} PSU<extra></extra>"))
f2.add_hline(y=r.offset_psu, line_color=L.C_REF, line_dash="dash", annotation_text=f"{vid}: {r.offset_psu:+.4f}")
f2.add_vline(x=pd.Timestamp(r.t1_cast_time_utc).to_pydatetime(), line_color=L.C_BOTTLE, line_dash="dot", annotation_text="CTD cast")
f2.update_yaxes(title="offset (PSU)")
f2.update_xaxes(title="mean time of upcast (UTC)")
L.style(f2, height=340, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
st.plotly_chart(f2, theme=None, alt="Offset estimated from each upcast against its mean time, with the CTD cast time and the selected variant's offset marked")
st.caption("Error bars are plus or minus half the IQR of that upcast's per-bin differences.")

st.subheader("All variants")
cols = ["variant_id", "role", "offset_psu", "iqr_psu", "rmse_before_psu", "rmse_after_offset_psu", "an31_slope",
        "rmse_after_slope_common_bins_psu", "n_profiler_samples", "n_bins_outside_reference", "t1_mean_profiler_time_utc"]
st.dataframe(tab[cols], hide_index=True, alt="Offset, IQR, RMSE and AN31 slope for every variant",
             column_config={c: st.column_config.NumberColumn(format="%.4f") for c in cols if c.endswith("_psu")}
             | {"an31_slope": st.column_config.NumberColumn(format="%.6f")})
st.caption("n_bins_outside_reference: bins the shipboard CTD profile does not reach; left out unless the variant reproduces the notebook "
           "(reference_extrapolated).")
pix = rec.get("rca:profileIndex")
if pix:
    cc = pix["crosscheck_with_own_upcasts"] or {}
    with st.expander("Official profile index cross-check", expanded=False):
        st.write(f"The OOI profile index (OOI-CabledArray/profileIndices) lists {pix['n_profiles_in_data']} profiles inside this profiler file. "
                 f"{cc.get('n_matched', 0)} of the {cc.get('n_upcasts_found', 0)} upcasts found here from the depth record start and end within "
                 f"{cc.get('tolerance_min', 0):g} min of an index profile; variants named `index_*` use the index's upcast windows directly.")
        if cc.get("upcasts"):
            st.dataframe(pd.DataFrame(cc["upcasts"]), hide_index=True, alt="Each upcast found in the profiler data and its matching index profile")
t2 = rec["rca:twoPoint"]["T2"]
if t2.get("status") == "missing":
    st.warning(f"T2 missing: {t2['reason']}", icon=":material/event_busy:")
