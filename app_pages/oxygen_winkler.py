import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import app_lib as L

year, rec = L.require_year("oxygen")
tab = L.get(year, "calibration_table.csv", variable="oxygen")
wk = L.get(year, "winkler_check.csv", variable="oxygen")
cp = L.get(year, "ctd_o2_profiles.csv", variable="oxygen")
L.missing("calibration_table, winkler_check or ctd_o2_profiles", tab, wk, cp)

L.header("CTD vs Winkler", year, rec, "oxygen", instrument=False)
main = tab[tab.role != "per_upcast"].set_index("variant_id")
vid = L.pick_variant("Variant", list(main.index), rec["rca:primaryVariant"], "o2_variant_w", lambda v: f"{v}  [{main.loc[v, 'role']}]")
r = main.loc[vid]

L.answer(f"**Ship SBE43 oxygen × {L.fmt(r.ctd_gain, 5)} matches the Winkler titrations** (median of Winkler / CTD over {int(r.n_winkler)} samples). "
         "This calibrated CTD is the reference for the DOFSTA gain.")
with L.card("CTD gain (step 1)") as m:
    m("CTD gain", L.fmt(r.ctd_gain, 5), help="corrected CTD O2 = raw CTD O2 x gain")
    m("IQR of ratios", L.fmt(r.ctd_gain_iqr))
    m("Winkler samples", f"{int(r.n_winkler)}", help="two titrations per Niskin")
st.caption(f"{r.description}. Matching: Winkler {r.winkler_depth_col} against CTD {r.ctd_depth_col}, {r.reference_filter} scans.")
L.show_flags(rec, {"ctd_o2_converted", "ctd_processing", "winkler_flagged", "winkler_flag_missing", "winkler_flag_bit0", "winkler_flag_bit7",
                   "winkler_from_summary"})

w = wk[wk.variant_id == vid]
c = cp[cp.variant_id == vid].sort_values("depth_m")
wz = w.winkler_depth_m if r.winkler_depth_col == "depth_m" else w.winkler_pressure_dbar
fig = make_subplots(rows=1, cols=2, column_widths=[0.62, 0.38], shared_yaxes=True, horizontal_spacing=0.04,
                    subplot_titles=("Oxygen (µmol/kg)", "Winkler / CTD ratio"))
fig.add_trace(go.Scatter(x=c.o2_ctd_umolkg, y=c.depth_m, mode="lines", line=dict(color=L.C_RAW, dash="dash", width=2), name="CTD SBE43 (raw)"), 1, 1)
fig.add_trace(go.Scatter(x=c.o2_ctd_calibrated_umolkg, y=c.depth_m, mode="lines", line=dict(color=L.C_REF, width=2.5), name="CTD SBE43 x gain"), 1, 1)
fig.add_trace(go.Scatter(x=w.o2_winkler_umolkg, y=wz, mode="markers", name="Winkler",
                         marker=dict(size=10, color=L.C_BOTTLE, symbol="square", line=dict(width=1, color="black")),
                         text=[f"Niskin {int(n)}" for n in w.niskin], hovertemplate="%{text}<br>%{x:.1f} µmol/kg @ %{y:.0f}<extra></extra>"), 1, 1)
fig.add_trace(go.Scatter(x=w.ratio_winkler_over_ctd, y=wz, mode="markers", showlegend=False, marker=dict(size=10, color=L.C_OFF),
                         text=[f"Niskin {int(n)}" for n in w.niskin], hovertemplate="%{text}<br>ratio %{x:.4f}<extra></extra>"), 1, 2)
fig.add_vline(x=r.ctd_gain, line_color=L.C_REF, line_dash="dash", col=2, row=1, annotation_text=f"gain {r.ctd_gain:.4f}")
fig.update_yaxes(autorange="reversed", title="depth (m)", col=1, row=1)
fig.update_yaxes(autorange="reversed", col=2, row=1)
fig.update_xaxes(title="O2 (µmol/kg)", col=1, row=1)
fig.update_xaxes(title="Winkler / CTD", col=2, row=1)
L.style(fig, height=560, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=-0.12))
st.plotly_chart(fig, theme=None, alt="Shipboard SBE43 oxygen profile before and after the gain, with Winkler samples, and the Winkler to CTD ratio at each sample depth")

st.subheader("Winkler titrations")
st.dataframe(w.drop(columns=["variant_id", "record_id", "year"], errors="ignore"), hide_index=True,
             alt="Winkler titrations against the CTD oxygen at the same depth",
             column_config={c: st.column_config.NumberColumn(format="%.4f") for c in
                            ("ratio_winkler_over_ctd", "o2_winkler_umolkg", "o2_ctd_at_winkler_umolkg")})
