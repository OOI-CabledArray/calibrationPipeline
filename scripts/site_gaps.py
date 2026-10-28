"""Time between each shipboard CTD cast at a shallow-profiler site and the nearest profile of that profiler.

  python scripts/site_gaps.py                       all sites, all configured years
  python scripts/site_gaps.py --site RS01SBPS       one site
  python scripts/site_gaps.py --csv docs/site_gaps.csv

Candidate casts: CastList rows whose Target Asset names the site, Station is "Shallow Profiler" and at least one salinity sample exists.
Cast time: NMEA start time in the cast .cnv. Profiles: OOI profile index. Gap = hours from the cast time to the nearest upcast (0 if the
cast lies inside one); also the number of profiles within +/-6 h and +/-24 h. Nothing is requested from OOINet.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal import github_data, profile_index, seabird  # noqa: E402
from rca_sal.pipeline import load_config  # noqa: E402
from site_casts import cast_list, salinity_sample_counts  # noqa: E402


def candidates(site: str, cast_log, samples) -> list[dict]:
    return [r for r in cast_list(cast_log)
            if str(r["Cast"]).startswith("CTD") and site in [s.strip() for s in str(r.get("Target Asset")).split(",")]
            and "shallow profiler" in str(r.get("Station")).lower() and samples.get(str(r["Cast"]), 0) > 0]


def gap_row(cast_time: pd.Timestamp, ix: pd.DataFrame) -> dict:
    zero = pd.Timedelta(0)
    gap = (ix["start"] - cast_time).clip(lower=zero) + (cast_time - ix["peak"]).clip(lower=zero)
    after, before = ix[ix["start"] >= cast_time], ix[ix["peak"] <= cast_time]
    h = lambda td: round(td.total_seconds() / 3600, 2)  # noqa: E731
    return {"nearest_profile_gap_h": h(gap.min()) if len(ix) else None,
            "profiles_within_6h": int(((ix["start"] > cast_time - pd.Timedelta(hours=6)) & (ix["start"] < cast_time + pd.Timedelta(hours=6))).sum()),
            "profiles_within_24h": int(((ix["start"] > cast_time - pd.Timedelta(hours=24)) & (ix["start"] < cast_time + pd.Timedelta(hours=24))).sum()),
            "first_profile_after_h": h(after["start"].iloc[0] - cast_time) if len(after) else None,
            "last_profile_before_h": h(cast_time - before["peak"].iloc[-1]) if len(before) else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site")
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    ap.add_argument("--csv")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    srcs = github_data.sources_from_config(cfg, ROOT)
    sites = [a.site] if a.site else [s for s in cfg["sites"] if s != "default"]
    rows = []
    for year, y in sorted(cfg["years"].items()):
        spec = y.get("inputs", {}).get("cast_log_xlsm")
        cnv_spec = y.get("inputs", {}).get("reference_cnv")
        if not spec or not cnv_spec:
            continue
        log = github_data.resolve_inputs(srcs, {"x": spec})[0]["x"]
        samples = salinity_sample_counts(log)
        folder, name = cnv_spec.rsplit("/", 1)
        stem = re.sub(r"CTD-\d+", "{cast}", name)
        for site in sites:
            for r in candidates(site, log, samples):
                try:
                    cnv = github_data.resolve_inputs(srcs, {"x": f"{folder}/{stem.format(cast=r['Cast'])}"})[0]["x"]
                    cast_time = seabird.read_cnv(cnv)[1]["nmea_utc"]
                    idx_spec = f"github[profile_indices]:{cfg['profile_index']['file'].format(site=site, year=cast_time.year)}"
                    ix = profile_index.read_index(github_data.resolve_inputs(srcs, {"x": idx_spec})[0]["x"])
                except Exception as e:                    # noqa: BLE001 - reported per cast, not hidden
                    rows.append({"year": int(year), "site": site, "cast": r["Cast"], "note": f"{type(e).__name__}: {e}"})
                    continue
                rows.append({"year": int(year), "site": site, "cast": r["Cast"], "cast_utc": cast_time.strftime("%Y-%m-%d %H:%M"),
                             "salinity_samples": samples[str(r["Cast"])], **gap_row(pd.Timestamp(cast_time), ix)})
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(df.to_string(index=False))
    if a.csv:
        Path(a.csv).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(a.csv, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
