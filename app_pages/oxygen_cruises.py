"""Shipboard CTD oxygen against Winkler samples, one regression per cruise, across the repository's oxygen runs."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import app_lib as L
from rca_sal.oxygen import ctd_winkler_fit

SITE_STYLE = (("#2a78d6", "circle", "●"), ("#eb6834", "square", "■"), ("#1baf7a", "triangle-up", "▲"))
C_ONE = "#777777"

all_bottles = L.winkler_bottles()
st.title("Shipboard CTD vs Winkler by cruise")
if all_bottles.empty:
    st.error("No oxygen run in outputs/ has a winkler_check.csv.", icon=":material/error:")
    st.stop()

site_names = sorted(all_bottles.site.unique())
style = dict(zip(site_names, SITE_STYLE))                     # a site keeps its colour and symbol whatever the filter shows
sites = st.pills("Sites", site_names, selection_mode="multi", default=site_names, key="o2_cruise_sites")
b = all_bottles[all_bottles.site.isin(sites)]
if b.empty:
    st.info("Select at least one site.", icon=":material/filter_alt:")
    st.stop()

cruises = [(k, g, ctd_winkler_fit(g.ctd, g.winkler)) for k, g in b.groupby(["year", "cruise", "ship_cruise_id"])]
casts = pd.DataFrame([{"Year": k[0], "Cruise": k[1], "Site": k[2], "Cast": k[3], **ctd_winkler_fit(g.ctd, g.winkler),
                       "depth_min": g.depth_m.min(), "depth_max": g.depth_m.max()}
                      for k, g in b.groupby(["year", "cruise", "site", "cast"])]).sort_values(["Year", "Site"])

offsets = {k[1]: f["median_offset"] for k, _, f in cruises}
low, high = min(offsets, key=offsets.get), max(offsets, key=offsets.get)
L.answer(f"**Before any gain, the median shipboard CTD minus Winkler runs from {offsets[low]:+.1f} µmol/kg ({low}) to {offsets[high]:+.1f} µmol/kg ({high})** "
         f"across {len(cruises)} cruises, {len(casts)} casts and {len(b)} Niskins.")
st.caption("These are the casts chosen to calibrate each profiler, not every cast of a cruise. Shipboard CTD oxygen is the raw SBE43 at the bottle depth "
           "(the run's primary variant); replicate titrations are averaged per Niskin; bad-flagged bottles and bottles with no shipboard CTD value at their depth "
           "are left out. Bottles reach different depths from cruise to cruise, which affects slopes: check the depth range in the table.")


def site_legend():
    st.markdown(" &nbsp; ".join(f"<span style='color:{style[s][0]}'>{style[s][2]}</span> {s}" for s in sites), unsafe_allow_html=True)


def add_site_points(fig, g, x, y, text):
    for site, h in g.groupby("site"):
        c, sym, _ = style[site]
        fig.add_trace(go.Scatter(x=x(h), y=y(h), mode="markers", name=site, marker=dict(size=9, color=c, symbol=sym, line=dict(width=1, color="white")),
                                 text=text(h), hovertemplate="%{text}<extra></extra>", showlegend=False))


# --- offset per cruise ----------------------------------------------------------------------------------------------------------------------
st.subheader("Offset per cruise")
labels = {k[1]: f"{k[1]}<br>{k[0]}" for k, _, _ in cruises}
fig = go.Figure()
fig.add_hline(y=0, line_color=C_ONE)
fig.add_trace(go.Scatter(x=[labels[k[1]] for k, _, _ in cruises], y=[f["median_offset"] for _, _, f in cruises], mode="markers", name="Cruise median",
                         marker=dict(symbol="line-ew", size=26, line=dict(width=3, color=L.C_REF)), customdata=[f["n"] for _, _, f in cruises],
                         hovertemplate="%{x}<br>cruise median %{y:+.1f} µmol/kg (%{customdata} Niskins)<extra></extra>", showlegend=False))
add_site_points(fig, casts.rename(columns={"Site": "site"}), lambda h: h.Cruise.map(labels), lambda h: h.median_offset,
                lambda h: [f"{r.site} {r.Cast}<br>median {r.median_offset:+.1f} µmol/kg, n = {r.n}" for r in h.itertuples()])
fig.update_xaxes(categoryorder="array", categoryarray=[labels[k[1]] for k, _, _ in cruises])
fig.update_yaxes(title="Shipboard CTD − Winkler (µmol/kg)")
L.style(fig, height=340, margin=dict(l=10, r=10, t=10, b=10))
site_legend()
st.plotly_chart(fig, theme=None, alt="Median shipboard CTD minus Winkler oxygen of each cruise, with the median of each cast marked by site")
st.caption("Black bar: median over the cruise's Niskins. Symbols: median of each cast.")

# --- regression panels ----------------------------------------------------------------------------------------------------------------------
st.subheader("Regression per cruise")
lo, hi = float(min(b.ctd.min(), b.winkler.min())), float(max(b.ctd.max(), b.winkler.max()))
pad = 0.05 * (hi - lo)
lim = [lo - pad, hi + pad]
site_legend()
st.caption("Same axes in every panel. Solid line: Winkler = intercept + slope × shipboard CTD. Dashed line: 1:1. Points above the dashed line have a shipboard CTD value below the Winkler.")
with st.container(horizontal=True, gap="small"):
    for (year, cruise, ship), g, f in cruises:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=lim, y=lim, mode="lines", line=dict(color=C_ONE, dash="dash", width=1.5), hoverinfo="skip", showlegend=False))
        if np.isfinite(f["slope"]):
            ends = np.array([g.ctd.min(), g.ctd.max()])
            fig.add_trace(go.Scatter(x=ends, y=f["intercept"] + f["slope"] * ends, mode="lines", line=dict(color=L.C_REF, width=2), hoverinfo="skip", showlegend=False))
        add_site_points(fig, g, lambda h: h.ctd, lambda h: h.winkler,
                        lambda h: [f"{r.site} {r.cast} Niskin {int(r.niskin)}, {r.depth_m:.0f} m<br>Shipboard CTD {r.ctd:.1f}, Winkler {r.winkler:.1f} µmol/kg" for r in h.itertuples()])
        fit = f"slope {f['slope']:.3f} · R² {f['r2']:.3f}" if np.isfinite(f["slope"]) else "too few bottles for a line"
        fig.update_xaxes(range=lim, title="Shipboard CTD O₂ (µmol/kg)")
        fig.update_yaxes(range=lim, title="Winkler O₂ (µmol/kg)")
        L.style(fig, width=330, height=360, margin=dict(l=10, r=10, t=56, b=10),
                title=dict(text=f"<b>{cruise}</b> {ship} ({year})<br><span style='font-size:12px'>n = {f['n']} · offset {f['median_offset']:+.1f} · {fit}</span>",
                           font=dict(size=14, color="#111111"), x=0.02, y=0.97))
        st.plotly_chart(fig, theme=None, width=330, alt=f"Shipboard CTD oxygen against Winkler oxygen for {cruise}, {fit}")

# --- tables ---------------------------------------------------------------------------------------------------------------------------------
NUM = {"Median shipboard CTD − Winkler": "%+.1f", "Cast medians, low": "%+.1f", "Cast medians, high": "%+.1f", "Median Winkler / shipboard CTD": "%.3f", "Slope": "%.3f", "Slope SE": "%.3f",
       "Intercept": "%.1f", "R²": "%.3f", "RMSE about line": "%.1f", "Shallowest (m)": "%.0f", "Deepest (m)": "%.0f"}
config = {c: st.column_config.NumberColumn(format=v) for c, v in NUM.items()}


def stats(f, depth_min, depth_max):
    return {"Niskins": f["n"], "Shallowest (m)": depth_min, "Deepest (m)": depth_max, "Median shipboard CTD − Winkler": f["median_offset"], "Median Winkler / shipboard CTD": f["median_ratio"],
            "Slope": f["slope"], "Slope SE": f["slope_se"], "Intercept": f["intercept"], "R²": f["r2"], "RMSE about line": f["rmse"]}


st.subheader("Numbers per cruise")
rows = []
for (year, cruise, ship), g, f in cruises:
    c = casts[casts.Cruise == cruise]
    rows.append({"Cruise": cruise, "Ship ID": ship, "Year": year, "Casts": len(c), "Cast medians, low": c.median_offset.min(), "Cast medians, high": c.median_offset.max(),
                 **stats(f, g.depth_m.min(), g.depth_m.max())})
st.dataframe(pd.DataFrame(rows), hide_index=True, column_config=config, alt="Regression of Winkler on shipboard CTD oxygen and median offset for each cruise")
st.caption("Offsets, intercept and RMSE are in µmol/kg. Where the cast medians of a cruise are far apart, the pooled line mixes casts that disagree: read them per cast below.")

with st.expander("Numbers per cast", icon=":material/table_rows:"):
    st.dataframe(pd.DataFrame([{"Cruise": r.Cruise, "Year": r.Year, "Site": r.Site, "Cast": r.Cast,
                                **stats({k: getattr(r, k) for k in ("n", "median_offset", "median_ratio", "slope", "slope_se", "intercept", "r2", "rmse")}, r.depth_min, r.depth_max)}
                               for r in casts.itertuples()]),
                 hide_index=True, column_config=config, alt="Regression of Winkler on shipboard CTD oxygen and median offset for each cast")
