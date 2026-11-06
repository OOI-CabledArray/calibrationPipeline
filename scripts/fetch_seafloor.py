"""Request OOINet data of the seafloor CTDs around their bottle samples, one request at a time.

  python scripts/fetch_seafloor.py --list                 windows and whether each is already saved
  python scripts/fetch_seafloor.py --max 3                submit, wait for and save the next 3 missing windows

Windows come from the instrument inventory (rca_sal.instruments.request_windows) for the instruments in [seafloor] instruments in the
config. Saved requests are listed in data/raw/ooinet/seafloor_requests.json; request folders are stored unchanged.
Credentials: OOI_API_USERNAME, OOI_API_TOKEN.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal import github_data, instruments, ooinet  # noqa: E402
from rca_sal.pipeline import load_config  # noqa: E402
from scripts.instrument_inventory import cruise_summaries  # noqa: E402

RAW = ROOT / "data" / "raw" / "ooinet"
MANIFEST = RAW / "seafloor_requests.json"
FMT = "%Y-%m-%dT%H:%M:%S.000Z"


def windows(cfg: dict, table: str = "seafloor", rds: list[str] | None = None) -> pd.DataFrame:
    """Request windows of the instruments in cfg[table] (or rds) over the cruises up to cfg[table] last_year ([inventory] last_year if absent)."""
    par, sf = cfg["inventory"], cfg[table]
    sources = github_data.sources_from_config(cfg, ROOT)
    discrete, assets = sources[None], sources["asset_management"]
    summaries = [pd.read_csv(discrete.fetch("github:" + p)[0], low_memory=False, encoding_errors="replace")
                 for p in cruise_summaries(discrete.tree(), sf.get("last_year", par["last_year"]))]
    dep = instruments.read_deployments([pd.read_csv(assets.fetch(f"github[asset_management]:deployment/{s}_Deploy.csv")[0], low_memory=False)
                                        for s in par["sites"]])
    dep = dep[dep["rd"].isin(rds or sf["instruments"])]
    m = instruments.match_samples(pd.concat(summaries, ignore_index=True), dep, par["max_distance_km"], par["max_distance_rov_km"],
                                  par["max_depth_difference_m"], par["max_gap_hours"])
    return instruments.request_windows(m, sf["window_gap_h"], sf["pad_h"])


def request(rd: str, method: str, stream: str, begin: str, end: str, done: list[dict], manifest: Path, wait_min: float) -> None:
    """Submit one request, wait for it, save it unchanged and add it to the manifest (HTTP 404 is recorded as no data)."""
    entry = {"ref_des": rd, "method": method, "stream": stream, "begin": begin, "end": end}
    try:
        resp = ooinet.submit(rd, method, stream, begin, end)
    except ooinet.OoinetError as e:
        if "404" not in str(e):
            raise
        print(f"{rd} {begin} .. {end}: OOINet has no data ({e})", flush=True)
        done.append({**entry, "stamp": None, "folder": None, "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "result": "no data (HTTP 404)"})
        manifest.write_text(json.dumps(done, indent=2) + "\n", encoding="utf8")
        return
    stamp = re.search(r"/(\d{8}T\d{9}Z)-", resp["allURLs"][1]).group(1)
    print(f"{rd} {begin} .. {end}: request {stamp}", flush=True)
    out = ooinet.download(resp, RAW, stamp, wait_timeout_s=wait_min * 60)
    done.append({**entry, "stamp": stamp, "folder": out.relative_to(ROOT).as_posix(),
                 "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    manifest.write_text(json.dumps(done, indent=2) + "\n", encoding="utf8")
    print(f"  saved {out.relative_to(ROOT)}: " + ", ".join(f"{f.name[:40]} {f.stat().st_size:,} B" for f in sorted(out.glob('*.nc'))), flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--max", type=int, default=1, help="number of missing windows to request")
    ap.add_argument("--wait-min", type=float, default=30)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    w = windows(cfg)
    done = json.loads(MANIFEST.read_text(encoding="utf8")) if MANIFEST.is_file() else []
    have = {(d["ref_des"], d["begin"], d["end"]) for d in done}
    w["key"] = [(r.rd, r.begin.strftime(FMT), r.end.strftime(FMT)) for r in w.itertuples()]
    w["saved"] = w["key"].isin(have)
    if a.list:
        print(w.drop(columns="key").to_string(index=False))
        print(f"{int(w['saved'].sum())} of {len(w)} saved")
        return 0
    method, stream = cfg["seafloor"]["stream"].split("/", 1)
    for r in w[~w["saved"]].head(a.max).itertuples():
        rd, begin, end = r.key
        request(rd, method, stream, begin, end, done, MANIFEST, a.wait_min)
    return 0


if __name__ == "__main__":
    sys.exit(main())
