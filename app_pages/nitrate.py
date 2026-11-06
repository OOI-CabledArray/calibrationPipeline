import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import app_lib as L

year, rec = L.require_year("nitrate")
tab = L.get(year, "calibration_table.csv", variable="nitrate")
mat = L.get(year, "bottle_matchups.csv", variable="nitrate")
prof = L.get(year, "sensor_profiles.csv", variable="nitrate")
L.missing("calibration_table, bottle_matchups or sensor_profiles", tab, mat, prof)

L.header("SUNA nitrate vs bottles", year, rec, "nitrate")
ROLE = {"temperature_matchup": "nearest profile, by temperature", "depth_matchup": "nearest profile, by depth", "per_profile": "single profile"}
t = tab.set_index("variant_id")
vid = L.pick_variant("Comparison", list(t.index), rec["rca:primaryVariant"], "no3_variant",
                     lambda v: (f"Profile #{int(t.loc[v, 'profile_id'])} ({t.loc[v, 'hours_from_cast']:+.0f} h), by temperature"
                                if t.loc[v, "role"] == "per_profile" else
                                f"Nearest profile, by {t.loc[v, 'match_by']} (primary)" if t.loc[v, "role"] == "primary"
                                else ROLE[t.loc[v, "role"]].capitalize()))
r = t.loc[vid]
acc = rec["rca:parameters"]["nitrate"]["accuracy_um"]

if r.get("profile_kind") == "parked":
    st.warning(f"The profiler was parked: this SUNA record spans {r.depth_span_m:.0f} m, so the comparison is one point (the record's median against the "
               f"bottle profile interpolated at its {r.match_by}). Treat the offset as indicative.", icon=":material/pause_circle:")
if "deployment" in t and t["deployment"].nunique() > 1:
    st.info(f"The request spans an instrument exchange (deployments {', '.join(str(int(x)) for x in sorted(t['deployment'].unique()))}). "
            f"This comparison uses deployment {int(r.deployment)}.", icon=":material/swap_horiz:")
off = r.get("offset_um")
if pd.isna(off):
    st.warning("No bottle falls inside this profile's range, so there is no offset.", icon=":material/warning:")
else:
    ok = abs(off) <= acc
    L.answer(f"**Correction: add {off:+.2f} µmol/L to the SUNA nitrate** (median of bottle − sensor). "
             + (f"This is within the sensor's ±{acc} µmol/L accuracy." if ok else f"This is larger than the sensor's ±{acc} µmol/L accuracy."), ok)
    with st.container(horizontal=True):
        with L.card("Offset") as m:
            m("Offset (µmol/L)", L.fmt(off, 2, signed=True), help="median(bottle − SUNA) over the matchups; NO3 corrected = NO3 + offset")
            m("RMSE before (µmol/L)", L.fmt(r.rmse_before_um, 2))
            m("RMSE after (µmol/L)", L.fmt(r.rmse_after_um, 2), delta=L.fmt(r.rmse_after_um - r.rmse_before_um, 2, signed=True),
              delta_color="inverse")
            m("Bottles matched", f"{int(r.n_matchups)} of {int(r.n_bottles_usable)}",
              help="bottles inside the profile's temperature (or depth) range; outliers are left out")
        with L.card("Linear fit") as m:
            m("Fit slope", L.fmt(r.get("fit_slope"), 3), help="least squares SUNA = a + b × bottle; 1 means no gain error")
            m("Fit R²", L.fmt(r.get("fit_r2"), 3))
st.caption(f"OOI L2 nitrate (`salinity_corrected_nitrate`) from SUNA profile #{int(r.profile_id)}, {abs(r.hours_from_cast):.1f} h "
           f"{'after' if r.hours_from_cast >= 0 else 'before'} CTD cast {rec['rca:cruise']['cast']}, against the cast's discrete nitrate, "
           f"matched by {r.match_by}. Method: {rec['rca:method']}.")
L.show_flags(rec, notes=L.t2_notes(rec))

m = mat[mat.variant_id == vid].sort_values("depth_m")
p = prof[prof.variant_id == vid].sort_values("depth_bin_m")
good, bad = m[~m.outlier.astype(bool)], m[m.outlier.astype(bool)]
fig = make_subplots(rows=1, cols=3, column_widths=[0.38, 0.38, 0.24], horizontal_spacing=0.05,
                    subplot_titles=("Nitrate against depth", "Nitrate against temperature", "Bottle − SUNA"))
for col, x_sensor, y_sensor, y_bottle in ((1, p.nitrate, p.depth_bin_m, "depth_m"), (2, p.nitrate, p.temperature, "temperature")):
    show = col == 1
    fig.add_trace(go.Scatter(x=x_sensor, y=y_sensor, mode="lines", line=dict(color=L.C_RAW, dash="dash", width=2), name="SUNA (OOI L2)",
                             legendgroup="raw", showlegend=show), 1, col)
    if not pd.isna(off):
        fig.add_trace(go.Scatter(x=x_sensor + off, y=y_sensor, mode="lines", line=dict(color=L.C_OFF, width=2), name="SUNA + offset",
                                 legendgroup="cor", showlegend=show), 1, col)
    fig.add_trace(go.Scatter(x=good.nitrate_bottle, y=good[y_bottle], mode="markers", name="bottle", legendgroup="b", showlegend=show,
                             marker=dict(size=9, color=L.C_BOTTLE, symbol="square", line=dict(width=1, color="black")),
                             hovertemplate="Niskin %{text}<br>%{x:.2f} µmol/L<extra></extra>", text=good.niskin), 1, col)
    if len(bad):
        fig.add_trace(go.Scatter(x=bad.nitrate_bottle, y=bad[y_bottle], mode="markers", name="bottle left out (outlier)", legendgroup="o",
                                 showlegend=show, marker=dict(size=11, color="#999999", symbol="x"),
                                 hovertemplate="Niskin %{text}<br>%{x:.2f} µmol/L<extra></extra>", text=bad.niskin), 1, col)
fig.add_trace(go.Scatter(x=good.diff_um, y=good.depth_m, mode="markers", marker=dict(size=9, color=L.C_BOTTLE, symbol="square"), showlegend=False,
                         hovertemplate="Niskin %{text}<br>%{x:+.2f} µmol/L @ %{y:.0f} m<extra></extra>", text=good.niskin), 1, 3)
fig.add_vrect(x0=-acc, x1=acc, fillcolor="#2E7D32", opacity=0.08, line_width=0, col=3, row=1)
fig.add_vline(x=0, line_color="#555555", line_dash="dot", col=3, row=1)
if not pd.isna(off):
    fig.add_vline(x=off, line_color=L.C_OFF, col=3, row=1)
zmax = max(p.depth_bin_m.max(), m.depth_m.max()) + 10         # sensor profile and matched bottles; deeper bottles are not compared
fig.update_yaxes(range=[zmax, 0], title="depth (m)", col=1, row=1)
fig.update_yaxes(title="temperature (°C)", col=2, row=1)
fig.update_yaxes(range=[zmax, 0], col=3, row=1)
fig.update_xaxes(title="nitrate (µmol/L)", col=1, row=1)
fig.update_xaxes(title="nitrate (µmol/L)", col=2, row=1)
fig.update_xaxes(title="µmol/L", col=3, row=1)
L.style(fig, height=600, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=-0.12))
st.plotly_chart(fig, theme=None, alt="SUNA nitrate profile before and after the offset with the discrete bottles, against depth and against temperature, "
                                       "and the bottle minus SUNA difference against depth with the sensor accuracy band shaded")
st.caption(f"The shaded band in the right panel is the sensor accuracy (±{acc} µmol/L). The guide recommends matching by temperature, "
           "which follows the water mass when internal waves move it up or down between the cast and the profile.")

per_profile, matchups, comparisons = st.tabs(["Profile by profile", "Bottle matchups", "All comparisons"])
with per_profile:
    pp = tab[tab.role == "per_profile"]
    st.plotly_chart(L.per_cast_chart(pd.to_datetime(pp.profile_mean_time_utc, utc=True), pp.offset_um, pp.offset_iqr_um / 2, pp.profile_id,
                                     "profile", "offset %{y:+.2f} µmol/L", "offset, bottle − SUNA (µmol/L)", "mean time of SUNA profile (UTC)",
                                     rec["rca:cruise"]["cast_time_utc"], band=acc),
                    theme=None, alt="Offset from each SUNA profile in the request against its time, with the CTD cast time and the accuracy band")
    st.caption("Error bars are plus or minus half the IQR of that profile's bottle differences. A spread larger than the accuracy band "
               "means the offset depends on which profile is used.")
with matchups:
    st.dataframe(m[["niskin", "depth_m", "temperature", "nitrate_bottle", "nitrite_bottle", "sensor_nitrate", "diff_um", "outlier", "status"]],
                 hide_index=True, alt="Each bottle with its depth, temperature, discrete nitrate and nitrite, the SUNA value and the difference",
                 column_config={c: st.column_config.NumberColumn(format="%.2f") for c in ("depth_m", "temperature", "nitrate_bottle",
                                                                                         "nitrite_bottle", "sensor_nitrate", "diff_um")})
with comparisons:
    cols = ["variant_id", "role", "match_by", "profile_id", "hours_from_cast", "n_matchups", "n_outliers", "offset_um", "offset_iqr_um",
            "rmse_before_um", "rmse_after_um", "fit_slope", "fit_intercept_um", "fit_r2", "n_spikes_removed", "n_negative"]
    st.dataframe(tab[[c for c in cols if c in tab]], hide_index=True, alt="Offset, RMSE and fit for every comparison",
                 column_config={c: st.column_config.NumberColumn(format="%.3f") for c in cols if c.endswith(("_um", "_r2", "slope", "cast"))})
