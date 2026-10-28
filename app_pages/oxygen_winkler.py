import numpy as np
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import app_lib as L

year, rec = L.require_year("oxygen")
tab = L.get(year, "calibration_table.csv", variable="oxygen")
wk = L.get(year, "winkler_check.csv", variable="oxygen")
cp = L.get(year, "ctd_o2_profiles.csv", variable="oxygen")
if any(x is None for x in (tab, wk, cp)):
    st.error("calibration_table, winkler_check or ctd_o2_profiles is missing.", icon=":material/error:")
    st.stop()

cr = rec["rca:cruise"]
main = tab[tab.role != "per_upcast"].set_index("variant_id")
vid = st.sidebar.selectbox("Variant", list(main.index), key="o2_variant_w",
                           index=int(np.flatnonzero(main.index == rec["rca:primaryVariant"])[0]),
                           format_func=lambda v: f"{v}  [{main.loc[v, 'role']}]")
r = main.loc[vid]

st.title(f"CTD vs Winkler oxygen, {cr['cruise']} {cr['ship_cruise_id']} {cr['cast']} ({L.run_site(year)})")
st.caption("Step 1 of the gain: the shipboard SBE43 is calibrated to the Winkler titrations. "
           "CTD gain = median(Winkler / CTD at the Winkler depths); the calibrated CTD is then the reference for DOFSTA.")
with st.container(horizontal=True):
    st.metric("CTD gain", L.fmt(r.ctd_gain, 5), help="corrected CTD O2 = raw CTD O2 x gain", border=True)
    st.metric("IQR of ratios", L.fmt(r.ctd_gain_iqr), border=True)
    st.metric("Winkler samples", f"{int(r.n_winkler)}", help="two titrations per Niskin", border=True)
st.caption(f"{r.description}. Matching: Winkler {r.winkler_depth_col} against CTD {r.ctd_depth_col}, {r.reference_filter} scans.")

w = wk[wk.variant_id == vid]
c = cp[cp.variant_id == vid].sort_values("depth_m")
fig = make_subplots(rows=1, cols=2, column_widths=[0.62, 0.38], shared_yaxes=True, horizontal_spacing=0.04,
                    subplot_titles=("Oxygen (umol/kg)", "Winkler / CTD ratio"))
fig.add_trace(go.Scatter(x=c.o2_ctd_umolkg, y=c.depth_m, mode="lines", line=dict(color=L.C_RAW, dash="dash", width=2), name="CTD SBE43 (raw)"), 1, 1)
fig.add_trace(go.Scatter(x=c.o2_ctd_calibrated_umolkg, y=c.depth_m, mode="lines", line=dict(color=L.C_REF, width=2.5), name="CTD SBE43 x gain"), 1, 1)
fig.add_trace(go.Scatter(x=w.o2_winkler_umolkg, y=w.winkler_depth_m if r.winkler_depth_col == "depth_m" else w.winkler_pressure_dbar,
                         mode="markers", name="Winkler", marker=dict(size=10, color=L.C_BOTTLE, symbol="square", line=dict(width=1, color="black")),
                         text=[f"Niskin {int(n)}" for n in w.niskin], hovertemplate="%{text}<br>%{x:.1f} umol/kg @ %{y:.0f}<extra></extra>"), 1, 1)
fig.add_trace(go.Scatter(x=w.ratio_winkler_over_ctd, y=w.winkler_depth_m if r.winkler_depth_col == "depth_m" else w.winkler_pressure_dbar,
                         mode="markers", showlegend=False, marker=dict(size=10, color=L.C_OFF),
                         text=[f"Niskin {int(n)}" for n in w.niskin], hovertemplate="%{text}<br>ratio %{x:.4f}<extra></extra>"), 1, 2)
fig.add_vline(x=r.ctd_gain, line_color=L.C_REF, line_dash="dash", col=2, row=1, annotation_text=f"gain {r.ctd_gain:.4f}")
fig.update_yaxes(autorange="reversed", title="depth (m)", col=1, row=1)
fig.update_yaxes(autorange="reversed", col=2, row=1)
fig.update_xaxes(title="O2 (umol/kg)", col=1, row=1)
fig.update_xaxes(title="Winkler / CTD", col=2, row=1)
L.style(fig, height=560, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=-0.12))
st.plotly_chart(fig, theme=None, alt="Shipboard SBE43 oxygen profile before and after the gain, with Winkler samples, and the Winkler to CTD ratio at each sample depth")

st.dataframe(w.drop(columns=["variant_id", "record_id", "year"], errors="ignore"), hide_index=True,
             alt="Winkler titrations against the CTD oxygen at the same depth",
             column_config={c: st.column_config.NumberColumn(format="%.4f") for c in
                            ("ratio_winkler_over_ctd", "o2_winkler_umolkg", "o2_ctd_at_winkler_umolkg")})
L.show_flags(rec, {"ctd_o2_converted", "ctd_processing", "winkler_flagged"})
