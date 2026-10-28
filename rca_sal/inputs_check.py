"""Read-only input check for one run: whether each configured input is reachable and parseable.
No calibration quantity is computed.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import discrete, github_data, seabird
from . import oxygen as ox
from .pipeline import OXYGEN_BOTTLE_ROLES, OXYGEN_REQUIRED, PROJECT_ROOT, SALINITY_REQUIRED, _resolve, load_config, site_year


def _row(role, status, detail="", spec=None):
    return {"role": role, "status": status, "detail": detail, "source": spec}


def _resolve_one(cfg, root, role, spec, source):
    if github_data.is_github_spec(spec):
        local, info = source.fetch(spec)
        return Path(local), f"github {info.commit[:10]}"
    return _resolve(spec, root), "local"


def check_year_inputs(year: int, config_path: str | Path, root: Path = PROJECT_ROOT, oxygen: bool = False, site: str | None = None) -> dict:
    cfg = load_config(config_path)
    ycfg = site_year(cfg, year, site)
    ocfg = ycfg.get("oxygen") or {}
    inputs = dict(ycfg.get("inputs", {}))
    if oxygen:
        inputs |= ocfg.get("inputs", {})
    roles = (OXYGEN_REQUIRED + tuple(r for r in OXYGEN_BOTTLE_ROLES if r in inputs)) if oxygen else SALINITY_REQUIRED
    if oxygen and not any(r in inputs for r in OXYGEN_BOTTLE_ROLES):
        roles += (OXYGEN_BOTTLE_ROLES[0],)
    cast = (ocfg.get("cast") if oxygen else None) or ycfg.get("cast")
    source = github_data.source_from_config(cfg, root)
    rows, paths = [], {}
    for role in roles:
        spec = inputs.get(role)
        if spec is None:
            rows.append(_row(role, "MISSING", "not configured for this year"))
            continue
        try:
            p, where = _resolve_one(cfg, root, role, spec, source)
            if not p.is_file():
                rows.append(_row(role, "MISSING", f"file not found: {p}", spec))
                continue
            paths[role] = p
            rows.append(_row(role, "ok", f"{where}, {p.stat().st_size:,} bytes", spec))
        except Exception as e:                       # noqa: BLE001 - reported, not hidden
            rows.append(_row(role, "MISSING", f"{type(e).__name__}: {e}", spec))
    info: dict = {"year": year, "cast": cast, "inputs": rows}

    def parse(role, fn):
        if role in paths:
            try:
                return fn(paths[role])
            except Exception as e:                   # noqa: BLE001
                next(r for r in rows if r["role"] == role).update(status="UNREADABLE", detail=f"{type(e).__name__}: {e}")
        return None

    ctd = parse("reference_cnv", seabird.read_cnv)
    if ctd is not None:
        df, hdr = ctd
        need = ["sal00", "sal11", "prDM", "depSM"] + (["sbeox0ML/L", "t090C"] if oxygen else [])
        miss = [c for c in need if c not in df.columns and not (c == "sbeox0ML/L" and "sbox0Mm/Kg" in df.columns)]
        info["cnv"] = {"scans": int(len(df)), "max_pressure_dbar": float(df["prDM"].max()), "processing": hdr.get("processing"),
                       "cast_start_utc": hdr.get("nmea_utc"), "missing_columns": miss}
        if miss:
            next(r for r in rows if r["role"] == "reference_cnv").update(status="INCOMPLETE", detail=f"no column(s) {miss}")
    btl = parse("reference_btl", seabird.read_btl)
    if btl is not None:
        info["btl"] = {"bottles": int(len(btl)), "niskins": sorted(int(b) for b in btl["bottle"])}
    if not oxygen:
        autosal = parse("autosal_xlsx", lambda p: discrete.read_autosal(p)[0])
        if autosal is not None:
            info["autosal"] = {"rows": int(len(autosal)), "sample_rows": int((~autosal["is_standard_check"]).sum())}
        samples = parse("cast_log_xlsm", lambda p: discrete.salinity_samples_for_cast(discrete.read_sample_list(p), cast))
        if samples is not None:
            info["cast_log"] = {"salinity_samples_for_cast": int(len(samples)), "niskins": samples["niskin"].tolist(),
                                "sample_ids": samples["sample_id"].tolist()}
            if autosal is not None and btl is not None:
                have_a = set(autosal["bottle_id"])
                info["matchable"] = {"niskins_with_autosal_result": int(samples["bottle_id"].isin(have_a).sum()),
                                     "niskins_in_btl": int(samples["niskin"].isin(btl["bottle"]).sum()), "of": int(len(samples))}
            if len(samples) == 0:
                next(r for r in rows if r["role"] == "cast_log_xlsm").update(status="INCOMPLETE", detail=f"no salinity samples for cast {cast!r}")
    else:
        for role, reader in (("discrete_summary_csv", ox.read_discrete_oxygen), ("winkler_xls", ox.read_winkler)):
            w = parse(role, lambda p, reader=reader: reader(p, cast))
            if w is not None:
                info.setdefault("winkler", {})[role] = {
                    "rows": int(len(w)), "niskins": sorted({int(n) for n in w["niskin"]}),
                    "flagged_bad": int(w["bottle_flag"].isin(cfg["parameters"]["oxygen"]["winkler_bad_flags"]).sum())}
    info["ready"] = all(r["status"] == "ok" for r in rows)
    return info


def format_report(info: dict) -> str:
    out = [f"{info['year']}  cast {info['cast']}   ready: {'YES' if info['ready'] else 'NO'}"]
    for r in info["inputs"]:
        out.append(f"  [{r['status']:10}] {r['role']:15} {r['detail']}")
    for k in ("cnv", "btl", "autosal", "cast_log", "matchable", "winkler"):
        if k in info:
            out.append(f"  {k}: {info[k]}")
    return "\n".join(out)
