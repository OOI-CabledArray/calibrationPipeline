"""Run every salinity and oxygen run in config/cruises.toml, then write the deployment summary.

  python scripts/run_all.py

Equivalent to scripts/run_year.py for each year, site and variable followed by scripts/deployments.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import deployments as summary_script  # noqa: E402
import run_year  # noqa: E402

from rca_sal.pipeline import load_config  # noqa: E402


def runs(cfg: dict):
    """(year, site or None, variable) for every configured run: the default site first, then the other sites."""
    for year in sorted(cfg["years"]):
        y = cfg["years"][year]
        for site in [None, *sorted(y.get("sites", {}))]:
            c = y if site is None else y["sites"][site]
            yield year, site, "salinity"
            if "oxygen" in c:
                yield year, site, "oxygen"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    ap.add_argument("--out", default=str(ROOT / "outputs"))
    a = ap.parse_args(argv)
    failed = []
    for year, site, variable in runs(load_config(a.config)):
        args = ["--year", year, "--variable", variable, "--config", a.config, "--out", a.out] + (["--site", site] if site else [])
        try:
            code = run_year.main(args)
        except Exception as e:                       # noqa: BLE001 - reported, the remaining runs continue
            print(f"{year} {site or ''} {variable}: {type(e).__name__}: {e}", file=sys.stderr)
            code = 1
        if code:
            failed.append((year, site, variable, code))
    summary_script.main(["--config", a.config, "--out", a.out])
    for year, site, variable, code in failed:
        print(f"FAILED ({code}): {year} {site or 'default site'} {variable}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
