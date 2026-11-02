"""Salinity offset and oxygen gain of the seafloor CTDs against bottle samples (rca_sal/seafloor.py).

Uses the request folders listed in data/raw/ooinet/seafloor_requests.json (scripts/fetch_seafloor.py) and the bottles of the
discrete summaries. Writes outputs/seafloor/bottle_comparison.csv, deployment_summary.csv and record.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal import github_data, instruments, ooi_qc, seafloor  # noqa: E402
from rca_sal.pipeline import load_config  # noqa: E402
from scripts.fetch_seafloor import MANIFEST  # noqa: E402
from scripts.instrument_inventory import cruise_summaries  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    ap.add_argument("--out", default=str(ROOT / "outputs" / "seafloor"))
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    par, sf = cfg["inventory"], cfg["seafloor"]
    sources = github_data.sources_from_config(cfg, ROOT)
    discrete, assets = sources[None], sources["asset_management"]
    files, summaries, deps = [], [], []
    for p in cruise_summaries(discrete.tree(), par["last_year"]):
        local, info = discrete.fetch("github:" + p)
        summaries.append(pd.read_csv(local, low_memory=False, encoding_errors="replace"))
        files.append({"url": info.url, "commit": info.commit, "sha256": hashlib.sha256(local.read_bytes()).hexdigest()})
    for s in par["sites"]:
        local, info = assets.fetch(f"github[asset_management]:deployment/{s}_Deploy.csv")
        deps.append(pd.read_csv(local, low_memory=False))
        files.append({"url": info.url, "commit": info.commit, "sha256": hashlib.sha256(local.read_bytes()).hexdigest()})
    dep = instruments.read_deployments(deps)
    dep = dep[dep["rd"].isin(sf["instruments"])]
    matches = instruments.match_samples(pd.concat(summaries, ignore_index=True), dep, par["max_distance_km"], par["max_distance_rov_km"],
                                        par["max_depth_difference_m"], par["max_gap_hours"])
    bottles = seafloor.bottle_table(matches)

    half = pd.Timedelta(minutes=sf["half_window_min"])
    parts = []
    for r in json.loads(MANIFEST.read_text(encoding="utf8")):
        if not r["folder"]:
            continue
        folder = ROOT / r["folder"]
        nc = next(f for f in sorted(folder.glob("*.nc")) if r["ref_des"] in f.name)
        ann_file = next((f for f in folder.glob("*annotations.json") if r["ref_des"] in f.name), None)
        ann = json.loads(ann_file.read_text(encoding="utf8")).get("annotations", []) if ann_file else []
        b = bottles[(bottles["rd"] == r["ref_des"]) & (bottles["time"] >= pd.Timestamp(r["begin"])) & (bottles["time"] <= pd.Timestamp(r["end"]))]
        if b.empty:
            continue
        values = seafloor.window_values(seafloor.read_instrument(nc), b["time"], half, ann, r["ref_des"])
        parts.append(seafloor.compare(b, values).assign(request=r["stamp"]))
        files.append({"url": r["folder"] + "/" + nc.name, "sha256": hashlib.sha256(nc.read_bytes()).hexdigest()})
    table = pd.concat(parts, ignore_index=True)
    used = table[table["n_samples"] > 0]
    summary = seafloor.deployment_summary(used)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "bottle_comparison.csv", index=False, float_format="%.6g")
    summary.to_csv(out / "deployment_summary.csv", index=False, float_format="%.6g")
    record = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "codeVersion": ooi_qc.code_version(ROOT),
              "parameters": {**par, **sf}, "inputs": files}
    (out / "record.json").write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf8")
    pd.set_option("display.width", 250)
    print(summary.drop(columns=["first_bottle", "last_bottle"]).to_string(index=False))
    print(f"\n{len(used)} bottle comparisons with instrument data, {len(table) - len(used)} without, {len(bottles)} bottles in total -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
