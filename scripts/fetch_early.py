"""Request OOINet data of the shallow profilers around the cruises before the first configured year, one request at a time.

  python scripts/fetch_early.py --list                    every request and whether it is already saved
  python scripts/fetch_early.py --max 3                   submit, wait for and save the next 3 missing requests
  python scripts/fetch_early.py --ship                    save the shipboard files of the same cruises (CTD casts, sample workbooks, profile indices)

Windows come from the instrument inventory (rca_sal.instruments.request_windows) for the profilers named in [sites], over the cruises up
to [early] last_year; windows of one site that overlap are merged. Each window is requested for the site's CTD, oxygen sensor and nitrate
sensor, and the explicit longer CTD and oxygen windows of [[early.extensions]] are added. Saved requests are listed in
data/raw/ooinet/early_requests.json; request folders are stored unchanged.
Credentials: OOI_API_USERNAME, OOI_API_TOKEN.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal import github_data, instruments  # noqa: E402
from rca_sal.pipeline import load_config  # noqa: E402
from scripts.fetch_seafloor import FMT, RAW, request, windows  # noqa: E402
from scripts.instrument_inventory import cruise_summaries  # noqa: E402

MANIFEST = RAW / "early_requests.json"
VARIABLES = {"salinity": "profiler", "oxygen": "oxygen", "nitrate": "nitrate"}      # variable -> key prefix in [sites.<site>]
SAMPLE_DATA = re.compile(r"(Salinity|Oxygen|Nutrients?)(_Oxygen)?_Sample_Data", re.I)


def site_tables(cfg: dict) -> dict[str, dict]:
    return {s: t for s, t in cfg["sites"].items() if isinstance(t, dict)}


def requests(cfg: dict) -> pd.DataFrame:
    """One row per request: variable, rd, method, stream, begin, end (as sent), bottles; in time order."""
    sites = site_tables(cfg)
    w = instruments.merge_windows(windows(cfg, "early", [t["profiler_reference_designator"] for t in sites.values()]))
    rows = []
    for r in w.itertuples():
        site = sites[r.rd.split("-")[0]]
        for var, prefix in VARIABLES.items():
            method, stream = site[f"{prefix}_stream"].split("/", 1)
            rows.append({"variable": var, "rd": site[f"{prefix}_reference_designator"], "method": method, "stream": stream,
                         "begin": r.begin.strftime(FMT), "end": r.end.strftime(FMT), "bottles": r.bottles})
    for e in cfg["early"].get("extensions", []):            # longer CTD and oxygen windows ([[early.extensions]])
        for var in ("salinity", "oxygen"):
            prefix = VARIABLES[var]
            method, stream = sites[e["site"]][f"{prefix}_stream"].split("/", 1)
            rows.append({"variable": var, "rd": sites[e["site"]][f"{prefix}_reference_designator"], "method": method, "stream": stream,
                         "begin": pd.Timestamp(e["begin"]).strftime(FMT), "end": pd.Timestamp(e["end"]).strftime(FMT), "bottles": 0})
    return pd.DataFrame(rows).sort_values(["begin", "rd"], kind="stable").reset_index(drop=True)


def ship_paths(cfg: dict) -> dict[str | None, list[str]]:
    """Repository paths per source: for each cruise up to [early] last_year the summary, CTD casts (.cnv, .btl) and salinity, oxygen and
    nutrient sample workbooks; and the profile index of each site and year up to last_year."""
    sources = github_data.sources_from_config(cfg, ROOT)
    discrete, index = sources[None], sources["profile_indices"]
    tree = list(discrete.tree())
    last = cfg["early"]["last_year"]
    discrete_paths = []
    for summary in cruise_summaries(discrete.tree(), last):
        discrete_paths.append(summary)
        folder = next(d for d in sorted({p.split("/")[1] for p in tree if p.startswith("CruiseData/")})
                      if d.startswith(summary.split("/")[-1].replace("_Discrete_Summary.csv", "")))
        for p in tree:
            if not p.startswith(f"CruiseData/{folder}/"):
                continue
            name, depth = p.split("/")[-1], p.count("/")
            if (depth == 3 and p.split("/")[2] == "CTD Data" and name.lower().endswith((".cnv", ".btl"))) or \
               (depth == 2 and SAMPLE_DATA.search(name) and name.lower().endswith((".xls", ".xlsx", ".xlsm"))):
                discrete_paths.append(p)
    wanted = {f"{s}_profiles_{y}.csv" for s in site_tables(cfg) for y in range(2014, last + 1)}
    return {None: discrete_paths, "profile_indices": sorted(p for p in index.tree() if p in wanted)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--max", type=int, default=1, help="number of missing requests to make")
    ap.add_argument("--wait-min", type=float, default=30)
    ap.add_argument("--ship", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if a.ship:
        sources = github_data.sources_from_config(cfg, ROOT)
        total = 0
        for key, paths in ship_paths(cfg).items():
            for p in paths:
                info = sources[key].fetch(f"github{'[' + key + ']' if key else ''}:{p}")[1]
                total += info.size
            print(f"{sources[key].repo}: {len(paths)} files", flush=True)
        print(f"{total / 1e6:.1f} MB in data/cache/github/ (verified against the repository's git blob hashes)")
        return 0
    r = requests(cfg)
    done = json.loads(MANIFEST.read_text(encoding="utf8")) if MANIFEST.is_file() else []
    have = {(d["ref_des"], d["begin"], d["end"]) for d in done}
    r["saved"] = [(x.rd, x.begin, x.end) in have for x in r.itertuples()]
    if a.list:
        print(r.drop(columns=["method", "stream"]).to_string())
        print(f"{int(r['saved'].sum())} of {len(r)} saved")
        return 0
    for x in r[~r["saved"]].head(a.max).itertuples():
        request(x.rd, x.method, x.stream, x.begin, x.end, done, MANIFEST, a.wait_min)
    return 0


if __name__ == "__main__":
    sys.exit(main())
