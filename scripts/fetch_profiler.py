"""Request shallow-profiler CTDPFA data (DOFSTA with --variable oxygen) around the CTD cast of a year; --site selects the site.

  python scripts/fetch_profiler.py --year 2024      submit a request for the cast time +/- [ooinet] half_window_h
  python scripts/fetch_profiler.py --resume         save every pending request that OOINet has completed
  python scripts/fetch_profiler.py --list           list pending requests
  python scripts/fetch_profiler.py --year 2024 --variable oxygen --match-profiler
                                                    DOFSTA over the time span of the configured CTDPFA file

The cast time is read from the .cnv header named in config/cruises.toml. Completed requests are saved unchanged in
data/raw/ooinet/<request timestamp>/ and the config lines to add are printed; the config is not edited. Pending requests are stored in
data/raw/ooinet/pending_requests.json. Credentials: OOI_API_USERNAME, OOI_API_TOKEN.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rca_sal import ooinet  # noqa: E402
from rca_sal.inputs_check import check_year_inputs  # noqa: E402
from rca_sal.pipeline import load_config, run_key, site_year  # noqa: E402

RAW = ROOT / "data" / "raw" / "ooinet"
PENDING = RAW / "pending_requests.json"


def load_pending() -> list[dict]:
    return json.loads(PENDING.read_text(encoding="utf8")) if PENDING.is_file() else []


def save_pending(items: list[dict]) -> None:
    if items:
        PENDING.parent.mkdir(parents=True, exist_ok=True)
        PENDING.write_text(json.dumps(items, indent=2), encoding="utf8")
    elif PENDING.is_file():
        PENDING.unlink()


def finish(entry: dict, wait_s: float) -> bool:
    """Download a request if OOINet has completed it (waiting up to wait_s). Returns True when it was saved."""
    urls = ooinet.request_urls(entry["ref_des"], entry["method"], entry["stream"], entry["stamp"], entry["user"])
    try:
        out = ooinet.download({"allURLs": urls}, RAW, entry["stamp"], wait_timeout_s=wait_s)
    except ooinet.OoinetError as e:
        print(f"  {entry['year']}{'-' + entry['site'] if entry.get('site') else ''} ({entry['stamp']}): still pending ({e})")
        return False
    oxy = entry.get("variable") == "oxygen"
    print(f"{entry['year']}: saved {out.relative_to(ROOT)}")
    for f in sorted(out.iterdir()):
        print(f"  {f.name}  {f.stat().st_size:,} bytes")
    ncs = sorted(f for f in out.glob("*.nc") if entry["ref_des"] in f.name)
    if len(ncs) != 1:
        print(f"  WARNING: expected one {entry['ref_des']} .nc, found {len(ncs)}; the window may span a deployment change", file=sys.stderr)
        return True
    rel = out.relative_to(ROOT).as_posix()
    print("\n  add to config/cruises.toml:")
    tbl = f'years.{entry["year"]}' + (f'.sites.{entry["site"]}' if entry.get("site") else "") + (".oxygen" if oxy else "")
    print(f'  [{tbl}] ooinet_request_dir = "{rel}"')
    print(f'  [{tbl}.inputs] {"dofsta_nc" if oxy else "profiler_nc"} = "{rel}/{ncs[0].name}"')
    for role, pat in (("ooinet_status", "status.json"), ("ooinet_provenance", "aggregate_provenance.json"),
                      ("ooinet_annotations", "annotations.json")):
        m = next((f for f in out.glob("*.json") if f.name.endswith(pat) and (entry["ref_des"] in f.name or pat == "status.json")), None)
        if m:
            print(f'  {role} = "{rel}/{m.name}"')
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int)
    ap.add_argument("--site", help="site code, e.g. RS01SBPS (default: the config's default site)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--wait-min", type=float, default=5, help="how long to wait for OOINet before leaving the request pending")
    ap.add_argument("--config", default=str(ROOT / "config" / "cruises.toml"))
    ap.add_argument("--half-window-h", type=float, default=None)
    ap.add_argument("--variable", choices=["salinity", "oxygen"], default="salinity", help="CTDPFA (salinity) or DOFSTA (oxygen)")
    ap.add_argument("--match-profiler", action="store_true", help="request exactly the time span of the year's configured CTDPFA "
                    "file (profiler_nc), so the DOFSTA covers the same upcasts")
    ap.add_argument("--begin", help="explicit window start (UTC, e.g. 2023-08-28T18:00:00Z) instead of cast +/- half window; needs "
                    "--end, or with --from-index extends the window back to this time")
    ap.add_argument("--end", help="explicit window end (UTC)")
    ap.add_argument("--from-index", type=int, metavar="N", help="request the first N profiles of the official profile index that start at or "
                    "after the cast (window = their start/end +/- [ooinet] index_pad_min), instead of cast +/- half window")
    a = ap.parse_args(argv)
    pending = load_pending()
    if a.list:
        for e in pending:
            print(f"{e['year']}{'-' + e['site'] if e.get('site') else ''} {e.get('variable', 'salinity')}  {e['stamp']}  {e['begin']} .. {e['end']}  submitted {e['submitted_utc']}")
        print(f"{len(pending)} pending")
        return 0
    if a.resume:
        left = [e for e in pending if not finish(e, wait_s=0)]
        save_pending(left)
        print(f"{len(left)} still pending")
        return 0 if not left else 4
    if a.year is None:
        ap.error("give --year, --resume or --list")
    cfg = load_config(a.config)
    y = site_year(cfg, a.year, a.site)
    half = a.half_window_h if a.half_window_h is not None else cfg["ooinet"]["half_window_h"]
    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")  # noqa: E731
    if a.from_index:
        from rca_sal import github_data, profile_index
        cast = check_year_inputs(a.year, a.config, site=a.site).get("cnv", {}).get("cast_start_utc")
        if cast is None:
            print(f"{a.year}: cannot read the cast time from the .cnv (is reference_cnv reachable?)", file=sys.stderr)
            return 2
        spec = y["inputs"].get("profile_index_csv")
        if spec is None:
            print(f"{a.year}: no profile_index_csv configured for this year", file=sys.stderr)
            return 2
        resolved, _ = github_data.resolve_inputs(github_data.sources_from_config(cfg, ROOT), {"profile_index_csv": spec})
        prof = profile_index.profiles_after(profile_index.read_index(resolved["profile_index_csv"]), cast, a.from_index)
        if prof.empty:
            print(f"{a.year}: the profile index has no profile after the cast ({cast:%Y-%m-%d %H:%M} UTC)", file=sys.stderr)
            return 2
        pad = pd.Timedelta(minutes=cfg["ooinet"]["index_pad_min"])
        t0, t1 = prof["start"].min() - pad, prof["end"].max() + pad
        if a.begin:
            t0 = min(t0, pd.Timestamp(a.begin))
        print(f"{a.year}: cast {cast:%Y-%m-%d %H:%M} UTC; profiles {int(prof['profile'].iloc[0])}..{int(prof['profile'].iloc[-1])} "
              f"({prof['start'].min():%Y-%m-%d %H:%M} to {prof['end'].max():%Y-%m-%d %H:%M}, {(prof['start'].min() - cast).total_seconds() / 3600:.1f} h after the cast); "
              f"requesting {fmt(t0)} to {fmt(t1)}")
    elif a.match_profiler:
        from rca_sal.pipeline import _resolve
        from rca_sal.profiler import read_ooinet_nc, read_profiler
        nc = y["inputs"].get("profiler_nc")
        if nc is None:
            print(f"{a.year}: no profiler_nc configured to match", file=sys.stderr)
            return 2
        paths = [_resolve(p, ROOT) for p in (nc if isinstance(nc, list) else [nc])]
        t = (read_ooinet_nc(paths[0]) if len(paths) == 1 else read_profiler(paths))["time"]
        t0, t1 = t.min().floor("s"), t.max().ceil("s")
        print(f"{a.year}: requesting {fmt(t0)} to {fmt(t1)} (time span of {Path(paths[0]).name})")
    elif a.begin or a.end:
        if not (a.begin and a.end):
            ap.error("--begin and --end go together")
        t0, t1 = pd.Timestamp(a.begin), pd.Timestamp(a.end)
        print(f"{a.year}: requesting {fmt(t0)} to {fmt(t1)} (explicit window)")
    else:
        cast = check_year_inputs(a.year, a.config, site=a.site).get("cnv", {}).get("cast_start_utc")
        if cast is None:
            print(f"{a.year}: cannot read the cast time from the .cnv (is reference_cnv reachable?)", file=sys.stderr)
            return 2
        t0, t1 = cast - pd.Timedelta(hours=half), cast + pd.Timedelta(hours=half)
        print(f"{a.year}: cast {cast:%Y-%m-%d %H:%M} UTC; requesting {fmt(t0)} to {fmt(t1)}")
    if a.variable == "oxygen":
        st = cfg["sites"][a.site or cfg["sites"]["default"]]
        ref_des, method_stream = st["oxygen_reference_designator"], st["oxygen_stream"]
    else:
        ref_des, method_stream = y["profiler_reference_designator"], y["profiler_stream"]
    method, stream = method_stream.split("/", 1)
    resp = ooinet.submit(ref_des, method, stream, fmt(t0), fmt(t1))
    entry = {"year": a.year, **({"site": a.site} if a.site else {}), "variable": a.variable,
             "stamp": re.search(r"/(\d{8}T\d{9}Z)-", resp["allURLs"][1]).group(1),
             "ref_des": ref_des, "method": method, "stream": stream, "begin": fmt(t0), "end": fmt(t1),
             "user": ooinet.async_user(resp), "submitted_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    key = lambda e: (e["year"], e.get("site"), e.get("variable", "salinity"))  # noqa: E731
    save_pending([e for e in pending if key(e) != key(entry)] + [entry])
    if finish(entry, wait_s=a.wait_min * 60):
        save_pending([e for e in load_pending() if e["stamp"] != entry["stamp"]])
        return 0
    print(f"  recorded in {PENDING.relative_to(ROOT)}; run `python scripts/fetch_profiler.py --resume` later")
    return 4


if __name__ == "__main__":
    sys.exit(main())
