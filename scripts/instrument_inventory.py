"""List the CTD and oxygen instruments that have bottle samples in the shared discrete summaries.

Reads every cruise's Discrete Summary from the [github] repository, the instrument deployment tables from
[github_sources.asset_management], pairs bottles with instruments by time, position and depth (rca_sal.instruments), and writes
outputs/instrument_inventory.csv (one row per instrument, cruise and timing) and outputs/instrument_inventory.json (inputs, parameters, code version).
Cruises after [inventory] last_year are not read.

With --streams, the streams and time coverage OOINet lists for each instrument found are written to outputs/instrument_streams.csv
(an inventory query, not a data request; needs OOI_API_USERNAME and OOI_API_TOKEN). The listing changes as data arrive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal import github_data, instruments, ooi_qc, ooinet  # noqa: E402
from rca_sal.pipeline import load_config  # noqa: E402


def cruise_summaries(paths, last_year: int) -> list[str]:
    """Path of each cruise's Discrete Summary: FinalSummaries/ when present, else the file inside the cruise folder."""
    out = []
    for folder in sorted({p.split("/")[1] for p in paths if p.startswith("CruiseData/Cabled-")}):
        year = int(re.search(r"_(\d{4})-\d{1,2}-\d{1,2}$", folder).group(1))
        if year > last_year:
            continue
        stem = folder.rsplit("_", 1)[0]
        final = f"FinalSummaries/{stem}_Discrete_Summary.csv"
        inside = f"CruiseData/{folder}/{stem}_Discrete_Summary.csv"
        pick = final if final in paths else inside if inside in paths else None
        if pick:
            out.append(pick)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    ap.add_argument("--out", default=str(ROOT / "outputs"))
    ap.add_argument("--streams", action="store_true", help="also list the OOINet streams and time coverage of each instrument")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    par = cfg["inventory"]
    sources = github_data.sources_from_config(cfg, ROOT)
    discrete, assets = sources[None], sources["asset_management"]

    files, summaries = [], []
    for path in cruise_summaries(discrete.tree(), par["last_year"]):
        local, info = discrete.fetch("github:" + path)
        summaries.append(pd.read_csv(local, low_memory=False, encoding_errors="replace"))
        files.append((local, info))
    deployments = []
    for site in par["sites"]:
        local, info = assets.fetch(f"github[asset_management]:deployment/{site}_Deploy.csv")
        deployments.append(pd.read_csv(local, low_memory=False))
        files.append((local, info))

    matches = instruments.match_samples(pd.concat(summaries, ignore_index=True), instruments.read_deployments(deployments),
                                        par["max_distance_km"], par["max_distance_rov_km"], par["max_depth_difference_m"], par["max_gap_hours"])
    inv = instruments.inventory(matches)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    inv.to_csv(out / "instrument_inventory.csv", index=False, float_format="%.3f")
    record = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "codeVersion": ooi_qc.code_version(ROOT),
              "parameters": par, "rows": len(inv),
              "inputs": [{"url": i.url, "repo": i.repo, "commit": i.commit, "gitBlob": i.blob_sha, "bytes": i.size,
                          "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p, i in files]}
    (out / "instrument_inventory.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf8")

    show = inv.groupby(["reference_designator", "placement", "timing"]).agg(
        cruises=("cruise", "nunique"), bottles=("bottles_niskin_ok", "sum"),
        sal_accepted=("salinity_accepted", "sum"), sal_unflagged=("salinity_unflagged", "sum"),
        o2_accepted=("oxygen_accepted", "sum"), o2_unflagged=("oxygen_unflagged", "sum"),
        first=("year", "min"), last=("year", "max")).reset_index()
    print(show.to_string(index=False))
    print(f"\n{len(inv)} instrument-cruise rows -> {out / 'instrument_inventory.csv'}")
    if a.streams:
        queried = datetime.now(timezone.utc).isoformat(timespec="seconds")
        found = {rd: ooinet.stream_times(rd) for rd in sorted(inv["reference_designator"].unique())}
        names = {rd: ooinet.stream_parameters(rd) for rd in found}
        pick = lambda rd, stream, word: ";".join(k for k in names[rd].get(stream, []) if word in k)  # noqa: E731
        rows = [{"reference_designator": rd, "method": s["method"], "stream": s["stream"], "records": s["count"], "first_record": s["beginTime"],
                 "last_record": s["endTime"], "salinity_parameters": pick(rd, s["stream"], "salinity"),
                 "oxygen_parameters": pick(rd, s["stream"], "oxygen"), "queried": queried} for rd, streams in found.items() for s in streams]
        rows += [{"reference_designator": rd, "queried": queried} for rd, streams in found.items() if not streams]
        pd.DataFrame(rows).to_csv(out / "instrument_streams.csv", index=False)
        print(f"{sum(bool(v) for v in found.values())} instruments with streams, {sum(not v for v in found.values())} not in OOINet "
              f"-> {out / 'instrument_streams.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
