"""python scripts/run_year.py --year 2025 [--site RS01SBPS] [--variable oxygen]   ->   outputs/<run>/"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal.pipeline import MissingInputError, load_config, run_key, run_year  # noqa: E402
from rca_sal.pipeline_o2 import run_year_oxygen  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--site", help="site code, e.g. RS01SBPS (default: the config's default site)")
    ap.add_argument("--variable", choices=["salinity", "oxygen"], default="salinity")
    ap.add_argument("--check", action="store_true", help="only check that the year's inputs are configured, reachable and parseable")
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    ap.add_argument("--out", default=str(ROOT / "outputs"))
    a = ap.parse_args(argv)
    if a.check:
        from rca_sal.inputs_check import check_year_inputs, format_report
        info = check_year_inputs(a.year, a.config, oxygen=a.variable == "oxygen", site=a.site)
        print(format_report(info))
        return 0 if info["ready"] else 2
    try:
        rec = (run_year(a.year, a.config, a.out, site=a.site) if a.variable == "salinity" else run_year_oxygen(a.year, a.config, a.out, site=a.site))
    except MissingInputError as e:
        d = Path(a.out) / run_key(load_config(a.config), a.year, a.site) / ("oxygen" if a.variable == "oxygen" else "")
        d.mkdir(parents=True, exist_ok=True)
        (d / "missing.json").write_text(json.dumps(
            {"year": a.year, "status": "missing", "checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "missing": e.missing}, indent=2), encoding="utf8")
        print(f"MISSING inputs for {a.year}; nothing computed.", file=sys.stderr)
        for m in e.missing:
            print(f"  {m['role']}: {m['path']}", file=sys.stderr)
        return 2
    p = next(v for v in rec["rca:variants"] if v["variant_id"] == rec["rca:primaryVariant"])
    if a.variable == "oxygen":
        print(f"{a.year}{'' if not a.site else ' ' + a.site}: wrote oxygen outputs  primary={p['variant_id']} ctd_gain={p['ctd_gain']:.5f} dofsta_gain={p['dofsta_gain']:.5f}")
    else:
        print(f"{a.year}{'' if not a.site else ' ' + a.site}: wrote {Path(a.out) / run_key(load_config(a.config), a.year, a.site)}  primary={p['variant_id']} "
              f"offset={p['offset_psu']:+.4f} PSU  slope={p['an31_slope']:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
