"""Match the shipboard bottle samples in the OOI discrete summaries to the CTD and oxygen instruments deployed where they were taken.

A bottle row is paired with an instrument when
  - the instrument was deployed at the bottle closure time (OOI asset-management deployment table), or the closure time is within
    max_gap_hours of a deployment's start or stop (timing 'gap': the cruise swaps instruments, so samples often fall between two deployments),
  - the cast start position is within max_distance_km (ship casts) or max_distance_rov_km (ROV bottles, taken at the instrument) of
    the deployment position, and
  - for fixed-depth instruments, the bottle depth is within max_depth_difference_m of the deployment depth.
Profiler instruments sweep a depth range, so no depth test is applied to them. The summary's Target Asset label is not used for
matching; whether it names the instrument or its site is recorded in label_names_instrument.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .eos80 import depth_from_pressure
from .oxygen import SUMMARY_MISSING, summary_flag_bits

ROV_CAST_BIT = 13                                  # summaryBuilder/flags.csv, castFlags: ROV bottle sample
ACCEPTED_BIT = 2                                   # discreteSampleFlags / niskinFlags: acceptable / no problems noted
BAD_SAMPLE_BITS = {3, 4}                           # discreteSampleFlags: questionable, bad
BAD_NISKIN_BITS = {3, 4, 5, 6, 7}                  # niskinFlags: leaking, out of water, vent open, misfire, unknown problem
EARTH_RADIUS_KM = 6371.0
SENSOR_CLASSES = ("CTDPF", "CTDBP", "DOSTA", "DOFST")
_PLACEMENTS = {"SF": "shallow profiler", "PC": "200 m platform", "DP": "deep profiler", "SP": "surface-piercing profiler",
               "RID": "surface mooring", "LJ": "seafloor", "MJ": "seafloor"}
FIXED_DEPTH = {"200 m platform", "surface mooring", "seafloor"}


def instrument_code(rd: str) -> str:
    """CTDPFB301 from RS03AXBS-LJ03A-12-CTDPFB301."""
    return rd.split("-")[3]


def instrument_class(rd: str) -> str:
    """'CTD' (CTDPF*, CTDBP*) or 'oxygen' (DOSTA*, DOFST*). CTD streams that include an optode or SBE 43 oxygen are listed by
    scripts/instrument_inventory.py --streams."""
    return "CTD" if instrument_code(rd).startswith("CTD") else "oxygen"


def placement(rd: str) -> str:
    """Where the instrument sits, from the node code of its reference designator (SF01A, PC03A, DP01B, LJ01A, MJ03B, RID27, SP001)."""
    node = rd.split("-")[1]
    for prefix, name in _PLACEMENTS.items():
        if node.startswith(prefix):
            return name
    raise ValueError(f"unknown node code in {rd!r}")


def read_deployments(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """CTD and oxygen deployments from asset-management deployment tables (deployment/<site>_Deploy.csv).

    Columns: rd, deployment, start, stop (UTC, open-ended deployments have stop = NaT), lat, lon, depth_m.
    """
    d = pd.concat(frames, ignore_index=True)
    d = d[d["Reference Designator"].str.split("-").str[3].str.startswith(SENSOR_CLASSES, na=False)]
    out = pd.DataFrame({"rd": d["Reference Designator"], "deployment": d["deploymentNumber"].astype(int),
                        "start": pd.to_datetime(d["startDateTime"], utc=True), "stop": pd.to_datetime(d["stopDateTime"], utc=True),
                        "lat": d["lat"].astype(float), "lon": d["lon"].astype(float), "depth_m": d["deployment_depth"].astype(float)})
    return out.sort_values(["rd", "deployment"]).reset_index(drop=True)


def distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance (haversine, mean Earth radius 6371 km)."""
    p = np.pi / 180
    a = np.sin((np.asarray(lat2) - lat1) * p / 2) ** 2 + np.cos(lat1 * p) * np.cos(np.asarray(lat2) * p) * np.sin((np.asarray(lon2) - lon1) * p / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def _status(value: pd.Series, flag: pd.Series) -> pd.Series:
    """'accepted' (value with the acceptable bit and no questionable/bad bit), 'unflagged' (value, no flag recorded),
    'flagged' (value with any other flag) or 'none' (no value)."""
    bits = flag.map(summary_flag_bits)
    has_value = value.notna()
    accepted = bits.map(lambda b: b is not None and ACCEPTED_BIT in b and not (b & BAD_SAMPLE_BITS))
    out = np.where(~has_value, "none", np.where(bits.isna(), "unflagged", np.where(accepted, "accepted", "flagged")))
    return pd.Series(out, index=value.index)


def prepare_summary(summary: pd.DataFrame) -> pd.DataFrame:
    """One row per summary row with numeric fill values removed, sample status columns and a bottle key (cruise, cast, Niskin)."""
    s = summary.copy()
    for c in ("Discrete Salinity [psu]", "Discrete Oxygen [mL/L]", "CTD Pressure [db]", "CTD Depth [m]"):
        s[c] = pd.to_numeric(s[c], errors="coerce").replace(SUMMARY_MISSING, np.nan)
    s["time"] = pd.to_datetime(s["CTD Bottle Closure Time [UTC]"], utc=True, errors="coerce")
    s["bottle"] = s["Cruise"].astype(str) + "|" + s["Cast"].astype(str) + "|" + s["Niskin/Bottle Position"].astype(str)
    s["salinity_status"] = _status(s["Discrete Salinity [psu]"], s["Discrete Salinity Flag"])
    s["oxygen_status"] = _status(s["Discrete Oxygen [mL/L]"], s["Discrete Oxygen Flag"])
    s["niskin_ok"] = s["Niskin Flag"].map(summary_flag_bits).map(lambda b: b is not None and ACCEPTED_BIT in b and not (b & BAD_NISKIN_BITS))
    s["rov"] = s["Cast Flag"].map(summary_flag_bits).map(lambda b: b is not None and ROV_CAST_BIT in b)
    z = depth_from_pressure(s["CTD Pressure [db]"], s["Start Latitude [degrees]"])
    s["depth_m"] = np.where(np.isfinite(z), z, s["CTD Depth [m]"])
    return s


EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def _hours(ts: pd.Series) -> np.ndarray:
    return ((ts - EPOCH) / pd.Timedelta(hours=1)).to_numpy(float)


def match_samples(summary: pd.DataFrame, deployments: pd.DataFrame, max_distance_km: float, max_distance_rov_km: float,
                  max_depth_difference_m: float, max_gap_hours: float) -> pd.DataFrame:
    """Every (bottle row, instrument) pair that satisfies the time, position and depth tests (see the module docstring).

    The deployment compared with is the one containing the bottle closure time, else the one whose start or stop is nearest in time.
    Added columns: rd, deployment, distance_km, depth_difference_m (fixed-depth instruments), gap_hours (0 when deployed), timing
    ('deployed' or 'gap'), label_names_instrument.
    """
    s = prepare_summary(summary)
    s = s[s["time"].notna() & s["Start Latitude [degrees]"].notna() & s["Start Longitude [degrees]"].notna()].reset_index(drop=True)
    th, lat, lon, rov = _hours(s["time"]), s["Start Latitude [degrees]"].to_numpy(float), s["Start Longitude [degrees]"].to_numpy(float), s["rov"].to_numpy(bool)
    limit_km = np.where(rov, max_distance_rov_km, max_distance_km)
    pairs = []
    for rd, dep in deployments.groupby("rd", sort=True):
        fixed = placement(rd) in FIXED_DEPTH
        start = _hours(dep["start"])
        stop = np.where(dep["stop"].notna(), _hours(dep["stop"].fillna(EPOCH)), np.inf)
        gap = np.maximum(np.maximum(start[None, :] - th[:, None], th[:, None] - stop[None, :]), 0.0)
        k = gap.argmin(axis=1)
        g = gap[np.arange(len(th)), k]
        km = distance_km(dep["lat"].to_numpy(float)[k], dep["lon"].to_numpy(float)[k], lat, lon)
        dz = (s["depth_m"].to_numpy(float) - dep["depth_m"].to_numpy(float)[k])
        keep = (g <= max_gap_hours) & (km <= limit_km)
        if fixed:
            keep &= np.abs(dz) <= max_depth_difference_m
        pairs.append(s[keep].assign(rd=rd, deployment=dep["deployment"].to_numpy()[k][keep], distance_km=km[keep],
                                    depth_difference_m=np.abs(dz[keep]) if fixed else np.nan, gap_hours=g[keep],
                                    timing=np.where(g[keep] == 0, "deployed", "gap")))
    m = pd.concat(pairs, ignore_index=True)
    m["label_names_instrument"] = [any(c.strip() in (instrument_code(rd), rd.split("-")[0]) for c in str(lab).split(","))
                                   for rd, lab in zip(m["rd"], m["Target Asset"])]
    return m


STATUSES = ("accepted", "unflagged", "flagged", "none")      # best first


def inventory(matches: pd.DataFrame) -> pd.DataFrame:
    """One row per (instrument, cruise, timing): number of bottles (unique cast and Niskin), the discrete salinity status (CTDs only) and
    discrete oxygen status counted over bottles with an acceptable Niskin flag, and the match geometry.

    A bottle with replicate rows takes the best status among them (order in STATUSES).
    """
    rank = {k: i for i, k in enumerate(STATUSES)}
    rows = []
    for (rd, cruise, timing), g in matches.groupby(["rd", "Cruise", "timing"], sort=True):
        ok = g[g["niskin_ok"]]
        depth = g["depth_difference_m"]
        row = {"reference_designator": rd, "instrument": instrument_code(rd), "class": instrument_class(rd), "placement": placement(rd), "cruise": cruise,
               "year": int(g["time"].min().year), "timing": timing, "deployment": int(g["deployment"].iloc[0]), "bottles": int(g["bottle"].nunique()),
               "bottles_niskin_ok": int(ok["bottle"].nunique()), "rov_bottles": int(g.loc[g["rov"], "bottle"].nunique()),
               "distance_km_median": float(g["distance_km"].median()), "distance_km_max": float(g["distance_km"].max()),
               "depth_difference_m_median": float(depth.median()) if depth.notna().any() else np.nan,
               "depth_difference_m_max": float(depth.max()) if depth.notna().any() else np.nan,
               "gap_hours_median": float(g["gap_hours"].median()), "gap_hours_max": float(g["gap_hours"].max()),
               "label_names_instrument": bool(g["label_names_instrument"].all())}
        for variable in ("salinity", "oxygen"):
            if variable == "salinity" and instrument_class(rd) != "CTD":
                row.update({f"{variable}_{c}": np.nan for c in STATUSES})
                continue
            col = f"{variable}_status"
            best = ok.assign(_rank=ok[col].map(rank)).groupby("bottle")["_rank"].min().map(dict(enumerate(STATUSES)))
            counts = best.value_counts()
            row.update({f"{variable}_{c}": int(counts.get(c, 0)) for c in STATUSES})
        rows.append(row)
    return pd.DataFrame(rows)


def request_windows(matches: pd.DataFrame, gap_h: float, pad_h: float) -> pd.DataFrame:
    """Time windows to request per instrument: bottle closure times (Niskin acceptable) closer than gap_h are one window, padded by pad_h.

    Columns: rd, begin, end (UTC), bottles.
    """
    rows = []
    for rd, g in matches[matches["niskin_ok"]].groupby("rd", sort=True):
        t = g.drop_duplicates("bottle")["time"].sort_values().reset_index(drop=True)
        for _, c in t.groupby((t.diff() > pd.Timedelta(hours=gap_h)).cumsum()):
            rows.append({"rd": rd, "begin": c.min() - pd.Timedelta(hours=pad_h), "end": c.max() + pd.Timedelta(hours=pad_h), "bottles": len(c)})
    return pd.DataFrame(rows, columns=["rd", "begin", "end", "bottles"])
