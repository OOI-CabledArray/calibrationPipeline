"""Salinity calibration of one run: configured inputs -> outputs/<run>/ with provenance.

All quantities are computed from the files named in config/cruises.toml. A missing configured input raises MissingInputError before
any computation.
"""
from __future__ import annotations

import json
import platform
import re
import sys
import tomllib
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import bottle_check as bc
from . import calibrate as cal
from . import discrete, ooi_qc, profile_index, profiler, seabird
from dataclasses import replace as _replace

from . import github_data
from .lineage import SourceFile, source_file, sha256_file

PKG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_DIR.parent
CONTEXT = {
    "@vocab": "https://schema.org/",
    "prov": "http://www.w3.org/ns/prov#",
    "rca": "urn:rca-salinity:term/",
}
SENSORS = {  # reference-CTD sensor -> .btl / .cnv column names
    "sal00": dict(sal="Sal00", cond="C0S/m", temp="T090C", label="primary (sal00)"),
    "sal11": dict(sal="Sal11", cond="C1S/m", temp="T190C", label="secondary (sal11)"),
}


class MissingInputError(FileNotFoundError):
    def __init__(self, missing: list[dict]):
        self.missing = missing
        super().__init__("missing input file(s): " + "; ".join(f"{m['role']}: {m['path']}" for m in missing))


# ----------------------------------------------------------------------------------------------
def load_config(path: str | Path) -> dict:
    """Config as TOML (the repo's cruises.toml) or JSON (generated for uploaded files)."""
    with open(path, "rb") as f:
        return json.load(f) if str(path).lower().endswith(".json") else tomllib.load(f)


META_KEYS = ("cruise", "ship_cruise_id", "vessel", "cast")


def cruise_meta(ycfg: dict) -> dict:
    return {k: ycfg.get(k) for k in META_KEYS}


def ooinet_block(ycfg: dict, P, extra: dict | None = None) -> dict:
    """OOINet request metadata; files that only exist for OOINet downloads are optional (uploads have none)."""
    folder = ycfg.get("ooinet_request_dir")
    has = ycfg["inputs"]
    block = {"request_folder": folder, "request_id": Path(folder).name if folder else "uploaded",
             "reference_designator": ycfg.get("profiler_reference_designator"), "stream": ycfg.get("profiler_stream"),
             "deployment": ycfg.get("deployment"),
             "annotations": json.loads(P("ooinet_annotations").read_text()).get("annotations", []) if "ooinet_annotations" in has else [],
             "status": json.loads(P("ooinet_status").read_text()) if "ooinet_status" in has else None}
    return block | (extra or {})


def _resolve(p: str | Path, root: Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (root / p)


SALINITY_REQUIRED = ("profiler_nc", "reference_cnv", "reference_btl", "autosal_xlsx", "cast_log_xlsm")
OXYGEN_REQUIRED = ("dofsta_nc", "reference_cnv", "reference_btl")
OXYGEN_BOTTLE_ROLES = ("discrete_summary_csv", "winkler_xls")    # at least one: the cruise's discrete summary or the lab's workbook


def year_variants(cfg: dict, own: list | None, window, oxygen: bool = False, index: bool = False, workbook: bool = True) -> list[dict]:
    """Variants of a run: its own, else those of the template year. Variants that need a notebook window, the Winkler workbook or the
    profile index are dropped when that input is absent; a variant with `without_index` then selects on the upcasts found in the profiler data.
    """
    if own is not None:
        base = own
    else:
        tpl = cfg["years"][cfg["upload"]["template_year"]]
        base = (tpl["oxygen"] if oxygen else tpl)["variants"]
    out = []
    for v in base:
        if (v.get("window") and not window) or (v.get("needs_workbook") and not workbook):
            continue
        if v.get("needs_profile_index") and not index:
            if not v.get("without_index"):
                continue
            v = {k: x for k, x in v.items() if k != "needs_profile_index"} | {
                "select": v["without_index"], "description": v["description"] + " (no profile index: upcasts found in the profiler data)"}
        out.append(v)
    return out


SHARED_INPUT_ROLES = ("autosal_xlsx", "cast_log_xlsm", "discrete_summary_csv")    # one file per cruise, shared by every site's cast
SHARED_YEAR_KEYS = ("cruise", "ship_cruise_id", "vessel")


def default_site(cfg: dict) -> str | None:
    return cfg.get("sites", {}).get("default")


def run_key(cfg: dict, year, site: str | None = None) -> str:
    """Folder/record key of a run: '2025' for the default site, '2025-RS01SBPS' for any other."""
    return str(year) if site in (None, default_site(cfg)) else f"{year}-{site}"


def site_year(cfg: dict, year, site: str | None = None) -> dict:
    """The year's configuration for one site. The default site is the year's own entry; another site takes the cruise
    facts and the shared cruise workbooks from the year and everything else (cast, CTD files, profiler files, profile
    index) from [years.<year>.sites.<site>], and its profiler designator/stream from [sites.<site>]."""
    base = cfg["years"][str(year)]
    if site in (None, default_site(cfg)):
        return {**base, "site": default_site(cfg)}
    sc = base.get("sites", {}).get(site)
    if sc is None:
        raise KeyError(f"year {year} has no [years.{year}.sites.{site}] entry in the config")
    st = cfg.get("sites", {}).get(site, {})
    inputs = {r: base["inputs"][r] for r in SHARED_INPUT_ROLES if r in base.get("inputs", {})} | sc.get("inputs", {})
    return {**{k: base[k] for k in SHARED_YEAR_KEYS if k in base},
            "profiler_reference_designator": st.get("profiler_reference_designator"), "profiler_stream": st.get("profiler_stream"),
            **{k: v for k, v in sc.items() if k != "inputs"}, "inputs": inputs, "site": site}


def require_roles(inputs: dict, roles) -> None:
    """An input role not named in the config is reported as missing."""
    absent = [{"role": r, "path": "(not configured for this year)"} for r in roles if r not in inputs]
    if absent:
        raise MissingInputError(absent)


def resolve_github_inputs(cfg: dict, root: Path, inputs: dict) -> tuple[dict, dict]:
    """Fetch every `github:` input (see rca_sal.github_data); unavailable ones are reported as missing."""
    try:
        return github_data.resolve_inputs(github_data.sources_from_config(cfg, root), inputs)
    except github_data.GitHubMissing as e:
        raise MissingInputError(e.missing) from None


def annotate_github(files: dict[str, list[SourceFile]], infos: dict) -> dict[str, list[SourceFile]]:
    """Put the GitHub commit and git blob of each fetched file into its SourceFile note."""
    by_path = {str(Path(k).resolve()): v for k, v in infos.items()}
    return {role: [_replace(sf, note=by_path[sf.path].note()) if sf.path in by_path else sf for sf in lst] for role, lst in files.items()}


def github_record(infos: dict, primary_repo: str | None = None) -> dict:
    """Commit and files read, per repository: the first repository at the top level, any others under 'additionalSources'."""
    groups: dict[tuple, list] = {}
    for i in infos.values():
        groups.setdefault((i.repo, i.ref, i.commit), []).append(i)
    recs = [{"repo": r, "ref": ref, "commit": c,
             "files": [{"path": i.path, "blob_sha": i.blob_sha, "bytes": i.size, "url": i.url} for i in g]}
            for (r, ref, c), g in groups.items()]
    recs.sort(key=lambda r: r["repo"] != primary_repo)      # the [github] repository first (stable for the others)
    return {**recs[0], **({"additionalSources": recs[1:]} if len(recs) > 1 else {})}


def fingerprint_inputs(year_cfg: dict, root: Path) -> dict[str, list[SourceFile]]:
    """Fingerprint every configured input; raise MissingInputError listing ALL absent files."""
    missing, found = [], {}
    for role, val in year_cfg["inputs"].items():
        for i, p in enumerate(val if isinstance(val, list) else [val]):
            rp = _resolve(p, root)
            if not rp.is_file():
                missing.append({"role": role, "path": str(p).replace("\\", "/")})          # path as written in the config
                continue
            found.setdefault(role, []).append(source_file(rp, role))
    if missing:
        raise MissingInputError(missing)
    return found


# ----------------------------------------------------------------------------------------------
def compare_nc_csv(nc: pd.DataFrame, csv: pd.DataFrame) -> dict:
    """Same values in the .nc and the CSV export over the CSV's time span? (The .nc may come from a later, longer request.)"""
    pad = pd.Timedelta(seconds=1)                     # CSV times are written to the second or coarser
    nc = nc[(nc["time"] >= csv["time"].min() - pad) & (nc["time"] <= csv["time"].max() + pad)].reset_index(drop=True)
    out = {"n_rows_nc": int(len(nc)), "n_rows_csv": int(len(csv))}
    if len(nc) != len(csv):
        out["identical_within_tolerance"] = False
        return out
    out["max_abs_time_diff_s"] = float((nc["time"] - csv["time"]).abs().dt.total_seconds().max())
    cols = ["depth_m", "pressure_dbar", "temperature_c", "conductivity_sm", "salinity_psu"]
    out["max_abs_diff"] = {c: float(np.nanmax(np.abs(nc[c].to_numpy(float) - csv[c].to_numpy(float)))) for c in cols}
    out["n_nan_mismatch"] = int(sum((nc[c].isna() != csv[c].isna()).sum() for c in cols))
    scale = max(float(np.nanmax(np.abs(nc[c].to_numpy(float)))) for c in cols)
    out["identical_within_tolerance"] = bool(
        out["n_nan_mismatch"] == 0 and out["max_abs_time_diff_s"] < 1e-3
        and max(out["max_abs_diff"].values()) <= 1e-9 * max(scale, 1.0))
    return out


def scan_workbook_years(path: Path, cruise_year: int) -> list[dict]:
    """Cells in a workbook that mention a year other than the cruise year (template leftovers)."""
    import openpyxl
    hits = []
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    for ws in wb:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str):
                    yrs = {int(y) for y in re.findall(r"\b(20\d\d)\b", c.value)}
                    if yrs and cruise_year not in yrs:
                        hits.append({"sheet": ws.title, "cell": c.coordinate, "text": c.value})
    return hits


def _gradient_at(ref: pd.DataFrame, depth_col: str, sal_col: str, z: float, half: float = 2.5) -> float:
    zz, ss = ref[depth_col].to_numpy(float), ref[sal_col].to_numpy(float)
    ok = np.isfinite(zz) & np.isfinite(ss)
    o = np.argsort(zz[ok])
    a, b = np.interp([z - half, z + half], zz[ok][o], ss[ok][o])
    return float((b - a) / (2 * half))


def flagged_bottles(matched: pd.DataFrame, ok_niskin, ok_salinity_prefix: str) -> np.ndarray:
    """Bottles marked unusable by the cruise: Niskin flag not in ok_niskin, or discrete salinity flag not starting with
    ok_salinity_prefix. A missing flag does not exclude a bottle.
    """
    n, s = matched["niskin_flag"].astype("string"), matched["salinity_flag"].astype("string")
    bad = (n.notna() & ~n.isin(list(ok_niskin))) | (s.notna() & ~s.str.startswith(ok_salinity_prefix, na=False))
    return bad.fillna(False).to_numpy(bool)


def bottle_checks(btl, samples, autosal, ref_down, outlier_psu, ok_niskin=("No problems noted",), ok_salinity_prefix="Acceptable") -> tuple[pd.DataFrame, dict]:
    matched = bc.match_bottles(btl, samples, autosal)
    matched = matched.assign(excluded_by_flag=flagged_bottles(matched, ok_niskin, ok_salinity_prefix))
    rows, summ = [], {}
    for key, s in SENSORS.items():
        r = bc.check(matched[~matched["excluded_by_flag"]], s["sal"], s["cond"], s["temp"], "PrDM", s["label"])
        t = bc.check(matched, s["sal"], s["cond"], s["temp"], "PrDM", s["label"]).table.copy()   # all bottles, for display
        used = ~t["excluded_by_flag"]
        med = float(np.median(t.loc[used, "diff_psu"])) if used.any() else np.nan
        t["sensor"] = key
        t["ctd_sal_gradient_psu_per_m"] = [
            _gradient_at(ref_down, "depSM", key, z) for z in t["DepSM"]]
        t["outlier"] = ((t["diff_psu"] - med).abs() > outlier_psu) & used
        rows.append(t)
        d = r.summary()
        d["n_excluded_by_flag"] = int((~used).sum())
        d["n_outliers_flagged"] = int(t["outlier"].sum())
        # summary without the flagged bottles, reported with the summary of all bottles
        keep = t[~t["outlier"] & used]
        d["without_outliers"] = ({"n": int(len(keep)), "median_diff_psu": float(keep["diff_psu"].median()),
                                  "rmse_psu": float(np.sqrt(np.mean(keep["diff_psu"] ** 2)))} if len(keep) else None)
        summ[key] = d
    tab = pd.concat(rows, ignore_index=True)
    keep_cols = ["sensor", "cast", "niskin", "sample_id", "bottle_id", "niskin_flag", "salinity_flag", "replicate_flag",
                 "target_depth_m", "time_utc", "PrDM", "DepSM", "T090C", "T190C", "Sal00", "Sal11",
                 "bottle_salinity", "ratio_2rt", "diff_psu", "bottle_cond_sm", "ctd_sal_gradient_psu_per_m", "excluded_by_flag", "outlier"]
    tab = tab[[c for c in keep_cols if c in tab.columns]]
    return tab, summ


# ----------------------------------------------------------------------------------------------
def first_upcasts(summ: pd.DataFrame, cast_start, n: int, min_span_m: float) -> list[int]:
    """First n upcasts that start at or after cast_start and span at least min_span_m of depth.
    Raises ValueError if the data hold fewer.
    """
    after = summ[summ["start"] >= pd.Timestamp(cast_start)].sort_values("start")
    full = after[(after["depth_max"] - after["depth_min"]) >= min_span_m]
    if len(full) < n:
        short = {int(k): round(float(r["depth_max"] - r["depth_min"]), 1) for k, r in after.iterrows() if k not in full.index}
        raise ValueError(f"only {len(full)} complete upcast(s) (span >= {min_span_m:g} m) start after the cast "
                         f"({pd.Timestamp(cast_start):%Y-%m-%d %H:%M} UTC) in the profiler data; {n} are needed"
                         + (f"; incomplete: {short} (span, m)" if short else "")
                         + ". Request more profiles (scripts/fetch_profiler.py --from-index).")
    return [int(k) for k in full.index[:n]]


def _select(var: dict, prof: pd.DataFrame, labelled: pd.DataFrame, summ: pd.DataFrame, cast_time, window, cast_start=None):
    sel = var["select"]
    if sel == "notebook_window_ascending":
        d = prof
        if var.get("window"):
            if window is None:
                raise ValueError(f"variant {var['id']} wants the notebook window (notebook_window_utc) but none is configured")
            d = prof[(prof["time"] >= window[0]) & (prof["time"] <= window[1])].reset_index(drop=True)
        return d[profiler.ascending_mask(d)], None
    if sel == "upcasts":
        return labelled[labelled["upcast_id"] >= 0], None
    if sel == "nearest_upcast":
        zero = pd.Timedelta(0)
        gap = ((summ["start"] - cast_time).clip(lower=zero) + (cast_time - summ["end"]).clip(lower=zero))
        k = int(gap.sort_values(kind="stable").index[0])
        return labelled[labelled["upcast_id"] == k], k
    if sel == "first_upcasts":
        if cast_start is None:
            raise ValueError(f"variant {var['id']} needs the cast start time")
        ks = first_upcasts(summ, cast_start, int(var["n_upcasts"]), float(var["min_span_m"]))
        return labelled[labelled["upcast_id"].isin(ks)], None
    raise ValueError(f"unknown selection {sel!r} in variant {var['id']}")


def _expand_variants(variants: list[dict], summ: pd.DataFrame) -> list[dict]:
    out = []
    for v in variants:
        if v["select"] != "each_upcast":
            out.append(v)
            continue
        for k in summ.index:
            w = dict(v)
            w["id"] = v["id"].replace("{k}", str(int(k)))
            w["description"] = v["description"].replace("{k}", str(int(k)))
            w["select"], w["upcast_id"] = "upcasts_one", int(k)
            out.append(w)
    return out


def upcasts_used(sub: pd.DataFrame) -> tuple[int | None, str | None]:
    """(number of upcasts, their ids joined with ';') in a selection; None for selections that are not upcast-labelled."""
    if "upcast_id" not in sub:
        return None, None
    ks = sorted(int(k) for k in sub["upcast_id"].unique() if k >= 0)
    return len(ks), ";".join(map(str, ks))


def run_variant(var, prof, labelled, summ, refs, cast_time, window, par, kind, cast_start=None, refdes=None, annotations=()):
    if var["select"] == "upcasts_one":
        sub, nearest = labelled[labelled["upcast_id"] == var["upcast_id"]], var["upcast_id"]
    else:
        sub, nearest = _select(var, prof, labelled, summ, cast_time, window, cast_start)
    if len(sub) == 0:
        raise ValueError(f"variant {var['id']} selected no profiler samples")
    sub, n_excluded = ooi_qc.drop_excluded(sub)
    if len(sub) == 0:
        raise ValueError(f"variant {var['id']} has no profiler samples left after dropping those inside exclusion annotations")
    on_samples = ooi_qc.annotations_on_samples(annotations, refdes, sub["time"])
    ext = bool(var.get("extrapolate_reference", par["extrapolate_reference"]))
    ref = refs[var["ref"]]
    zc, sc = var["ref_depth_col"], var["ref_sal_col"]
    binned = cal.bin_and_smooth(
        sub["depth_m"], {c: sub[c] for c in ("salinity_psu", "conductivity_sm", "temperature_c", "pressure_dbar")},
        bin_size=par["bin_size_m"], smooth_window=par["smooth_window_bins"])
    res = cal.estimate_correction(binned, ref[zc], ref[sc], kind=kind, extrapolate=ext)
    an = cal.an31_slope(binned, ref[zc], ref[sc], smooth_window=par["smooth_window_bins"],
                        offset=res.value if kind == "additive" else None, extrapolate=ext)
    t1 = profiler.mean_time(sub["time"])
    z = binned.index.to_numpy(float)
    ref_at = cal.reference_at(z, ref[zc], ref[sc], ext)
    outside = int((np.isfinite(binned["salinity_psu"].to_numpy()) & ~np.isfinite(cal.reference_at(z, ref[zc], ref[sc], False))).sum())
    series = pd.DataFrame({
        "depth_bin_m": z, "n_samples": binned["n_samples"].to_numpy(),
        "raw": binned["salinity_psu"].to_numpy(),
        "offset_corrected": binned["salinity_psu"].to_numpy() - res.value if kind == "additive"
        else binned["salinity_psu"].to_numpy() * res.value,
        "slope_corrected": an["salinity_slope_corrected"].to_numpy(),
        "reference": np.where(np.isfinite(binned["salinity_psu"].to_numpy()), ref_at, np.nan),
    })
    series.insert(0, "variant_id", var["id"])
    row = {
        "variant_id": var["id"], "role": var["role"], "description": var["description"],
        "selection": var["select"], "upcast_id": nearest, **dict(zip(("n_upcasts", "upcast_ids"), upcasts_used(sub))),
        "reference_filter": var["ref"], "reference_depth_col": zc, "reference_sal_col": sc,
        "kind": kind, "n_annotation_excluded": n_excluded, "annotations_on_samples": json.dumps(on_samples) if on_samples else None,
        "reference_extrapolated": ext, "n_bins_outside_reference": outside,
        "n_profiler_samples": int(len(sub)), "n_bins": res.n_bins,
        "depth_min_m": res.depth_min_m, "depth_max_m": res.depth_max_m,
        "offset_psu": res.value, "iqr_psu": res.iqr, "rmse_before_psu": res.rmse,
        "rmse_after_offset_psu": res.rmse_after,
        "an31_slope": an["slope"], "an31_n_bins": an["n_bins"],
        "rmse_before_common_bins_psu": an["rmse_before"], "rmse_after_offset_common_bins_psu": an["rmse_after_offset"],
        "rmse_after_slope_common_bins_psu": an["rmse_after"],
        "median_residual_after_slope_psu": an["residual_median_after"],
        "t1_mean_profiler_time_utc": t1.isoformat(),
        "sample_time_start_utc": sub["time"].min().isoformat(), "sample_time_end_utc": sub["time"].max().isoformat(),
        "t1_cast_time_utc": pd.Timestamp(cast_time).isoformat(),
        "mean_profiler_minus_cast_h": float((t1 - cast_time).total_seconds() / 3600),
    }
    return row, series


# ----------------------------------------------------------------------------------------------
def annotation_flags(primary: dict) -> list[dict]:
    """Record flags for the OOI annotations that cover the samples the primary variant used (see rca_sal.ooi_qc)."""
    raw = primary.get("annotations_on_samples")
    out = []
    for a in json.loads(raw) if isinstance(raw, str) else []:
        bad = a["qc_flag"] in ("fail", "suspect", "not_operational") or a["exclusion_flag"]
        out.append({"severity": "warning" if bad else "info", "code": "ooinet_annotation",
                    "text": f"OOI annotation {a['id']} ({a['qc_flag']}{', exclusion flag set' if a['exclusion_flag'] else ''}; "
                            f"{a['start']} to {a['stop'] or 'open'}) covers {a['n_samples']} of the samples used: {a['annotation']}"})
    return out


def _clean(o):
    """JSON-safe: numpy -> python, NaN/inf -> None, Timestamp -> iso."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return pd.Timestamp(o).isoformat()
    if o is pd.NaT or o is pd.NA:
        return None
    return o


def repo_relative(p: str | Path) -> str | None:
    """Path of a file inside this repository with forward slashes (the same path on GitHub); None if it lies outside it."""
    try:
        return Path(p).resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return None


def _code_fingerprint(config_path: str | Path) -> list[dict]:
    """SHA-256 of the package, the run script and the config file actually used for this run."""
    files = sorted(PKG_DIR.glob("*.py")) + [Path(config_path)]
    run = PROJECT_ROOT / "scripts" / "run_year.py"
    if run.is_file():
        files.append(run)
    return [{"path": repo_relative(f) or f"uploaded/{f.name}", "sha256": sha256_file(f), "role": "config" if f == Path(config_path) else "code"}
            for f in files]


def source_location(sf: SourceFile, infos: dict) -> str:
    """Location of an input without machine paths: the raw GitHub URL at the commit read, the path inside the repository, or
    uploaded/<role>/<name> for an uploaded file.
    """
    by_path = {str(Path(k).resolve()): v for k, v in infos.items()}
    if sf.path in by_path:
        return by_path[sf.path].url
    return repo_relative(sf.path) or f"uploaded/{sf.role}/{sf.filename}"


def _prov_file(sf: SourceFile, infos: dict | None = None) -> dict:
    return {"@type": ["MediaObject", "prov:Entity"], "name": sf.filename, "contentUrl": source_location(sf, infos or {}),
            "sha256": sf.sha256, "contentSize": sf.size_bytes, "dateModified": sf.modified_utc,
            "rca:role": sf.role, "description": sf.note or None}


def run_year(year: int, config_path: str | Path, out_root: str | Path, root: Path = PROJECT_ROOT, site: str | None = None) -> dict:
    started = datetime.now(timezone.utc)
    cfg = load_config(config_path)
    if str(year) not in cfg.get("years", {}):
        raise KeyError(f"year {year} is not in {config_path}")
    ycfg, par = site_year(cfg, year, site), cfg["parameters"]
    kind = par["correction_kind"]
    out = Path(out_root) / run_key(cfg, year, site)
    out.mkdir(parents=True, exist_ok=True)
    stale = out / "missing.json"
    if stale.exists():
        stale.unlink()
    require_roles(ycfg["inputs"], SALINITY_REQUIRED)
    resolved, gh_infos = resolve_github_inputs(cfg, root, ycfg["inputs"])
    ycfg = {**ycfg, "inputs": resolved}
    files = annotate_github(fingerprint_inputs(ycfg, root), gh_infos)   # raises MissingInputError before any computation
    P = lambda role: _resolve(ycfg["inputs"][role], root)  # noqa: E731
    flags: list[dict] = []

    # --- profiler (NetCDF; CSV only for cross-check) ---
    nc_in = ycfg["inputs"]["profiler_nc"]     # one file, or several when OOINet split a long request into chunks
    nc_paths = [_resolve(p, root) for p in (nc_in if isinstance(nc_in, list) else [nc_in])]
    prof = profiler.read_ooinet_nc(nc_paths[0]) if len(nc_paths) == 1 else profiler.read_profiler(nc_paths)
    refdes = ycfg.get("profiler_reference_designator")
    annotations = json.loads(P("ooinet_annotations").read_text(encoding="utf8")).get("annotations", []) if "ooinet_annotations" in ycfg["inputs"] else []
    prof = ooi_qc.mark_exclusions(prof, annotations, refdes)
    if "profiler_csv" in ycfg["inputs"]:
        csv_paths = [_resolve(p, root) for p in ycfg["inputs"]["profiler_csv"]]
        csv_check = compare_nc_csv(prof, profiler.read_ooinet_csv(csv_paths))
    else:
        csv_check = {"status": "not provided", "identical_within_tolerance": None}
    if csv_check["identical_within_tolerance"] is False:
        flags.append({"severity": "warning", "code": "nc_csv_differ", "text": "The .nc and the CSV export differ.", "detail": csv_check})
    upp = profiler.UpcastParams(**par["upcast"])
    labelled = profiler.label_upcasts(prof, upp)
    summ = profiler.upcast_summary(labelled)
    pindex = None
    if "profile_index_csv" in ycfg["inputs"]:
        ix = profile_index.read_index(P("profile_index_csv"))
        lab_ix = profile_index.label_by_index(prof, ix)
        pindex = {"labelled": lab_ix, "summ": profiler.upcast_summary(lab_ix), "crosscheck": profile_index.crosscheck(summ, ix) if len(summ) else None}
        cc = pindex["crosscheck"]
        if cc and cc["n_matched"] < cc["n_upcasts_found"]:
            flags.append({"severity": "warning", "code": "upcasts_not_in_profile_index",
                          "text": f"{cc['n_upcasts_found'] - cc['n_matched']} of {cc['n_upcasts_found']} upcasts found in the profiler data do not match a profile in the official index.",
                          "detail": cc})
    if summ.empty:
        raise ValueError(f"no upcasts found in the profiler file: {len(prof)} samples, {prof['time'].min():%Y-%m-%d %H:%M} to "
                         f"{prof['time'].max():%Y-%m-%d %H:%M} UTC, depth only {prof['depth_m'].min():.1f}-{prof['depth_m'].max():.1f} m (the profiler was not profiling in this window)")

    # --- reference CTD --------------------------------------------------------------------------
    ctd, hdr = seabird.read_cnv(P("reference_cnv"))
    dc = par["ctd_downcast"]
    down = seabird.downcast(ctd, "prDM", dc["min_pressure_dbar"], dc["heave_tolerance_dbar"])
    refs = {"raw": ctd, "downcast": down}
    proc = hdr.get("processing", [])
    flags.append({"severity": "caveat", "code": "ctd_processing",
                  "text": f"Reference .cnv was processed with {', '.join(proc) or 'unknown steps'} only; "
                          "alignctd / celltm / loopedit are not applied."})
    flags.append({"severity": "caveat", "code": "ctd_downcast_only",
                  "text": f"The .cnv holds the downcast only ({len(ctd)} scans, {len(down)} kept after removing "
                          "ship-heave reversals). Bottle (.btl) averages are taken at upcast bottle closures, so "
                          "CTD-vs-Autosal uses upcast data while profiler-vs-CTD uses the downcast."})

    # --- discrete samples + bottle check ---------------------------------------------------------
    btl = seabird.read_btl(P("reference_btl"))
    autosal, ahdr = discrete.read_autosal(P("autosal_xlsx"))
    samples = discrete.salinity_samples_for_cast(discrete.read_sample_list(P("cast_log_xlsm")), ycfg["cast"])
    btab, bsum = bottle_checks(btl, samples, autosal, down, par["bottle_outlier_psu"],
                               par["bottle_ok_niskin_flags"], par["bottle_ok_salinity_flag_prefix"])
    for _, r in btab[btab["excluded_by_flag"] & (btab["sensor"] == "sal00")].iterrows():
        flags.append({"severity": "warning", "code": "bottle_excluded_by_flag",
                      "text": f"Niskin {int(r['niskin'])} ({r['sample_id']}) is flagged by the cruise ({r['niskin_flag']}; {r['salinity_flag']}) "
                              "and is left out of the CTD-vs-Autosal check."})
    for _, r in btab[btab["outlier"]].iterrows():
        flags.append({"severity": "warning", "code": "bottle_outlier",
                      "text": f"Niskin {int(r['niskin'])} ({r['sample_id']}, ~{r['DepSM']:.0f} m depSM, {r['sensor']}): "
                              f"CTD-bottle {r['diff_psu']:+.4f} PSU is more than {par['bottle_outlier_psu']} PSU from the "
                              f"median; CTD gradient there {r['ctd_sal_gradient_psu_per_m']:+.3f} PSU/m."})
    adate = ahdr.get("date")
    cast_start = hdr["nmea_utc"]
    if adate is not None and pd.Timestamp(adate).date() != cast_start.date():
        flags.append({"severity": "warning", "code": "autosal_header_date",
                      "text": f"Autosal workbook header date is {pd.Timestamp(adate).date()} but the cast is on "
                              f"{cast_start.date()}; looks like a template leftover (not changed)."})
    for h in scan_workbook_years(P("autosal_xlsx"), cast_start.year):
        flags.append({"severity": "warning", "code": "autosal_other_year",
                      "text": f"Autosal workbook {h['sheet']}!{h['cell']} says {h['text']!r}; cruise year is {cast_start.year} (not changed)."})

    # --- cast time -----------------------------------------------------------------------------
    cast_end = btl["time_utc"].max()
    cast_time = cast_start + (cast_end - cast_start) / 2
    cast_times = {"cast_start_utc": cast_start, "last_bottle_closure_utc": cast_end,
                  "cast_time_utc": cast_time,
                  "definition": "cast_time = midpoint of NMEA cast start (.cnv header) and the last bottle closure (.btl)"}

    # --- variants -----------------------------------------------------------------------------
    w = ycfg.get("notebook_window_utc")
    window = (pd.Timestamp(w["start"]), pd.Timestamp(w["end"])) if w else None
    rows, series = [], []
    variants = year_variants(cfg, ycfg.get("variants"), w, index=pindex is not None)
    for v in _expand_variants(variants, summ):
        if v["select"].startswith("index_"):                 # same selection rules, applied to the official profile index
            if pindex["summ"].empty:
                raise ValueError(f"variant {v['id']}: no profile of the official index overlaps the profiler data")
            r, s = run_variant({**v, "select": v["select"][len("index_"):]}, prof, pindex["labelled"], pindex["summ"], refs, cast_time,
                               window, par, kind, cast_start, refdes, annotations)
            r["selection"] = v["select"]
        else:
            r, s = run_variant(v, prof, labelled, summ, refs, cast_time, window, par, kind, cast_start, refdes, annotations)
        rows.append(r)
        series.append(s)
    table = pd.DataFrame(rows)
    profiles = pd.concat(series, ignore_index=True)

    # --- provenance ---------------------------------------------------------------------------
    all_sf = [sf for lst in files.values() for sf in lst]
    code = _code_fingerprint(config_path)
    record_id = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
        {"year": year, "inputs": sorted(sf.sha256 for sf in all_sf), "code": sorted(c["sha256"] for c in code)}))
    ooinet = ooinet_block(ycfg, P, {"crosscheck_request_folder": ycfg.get("ooinet_crosscheck_request_dir")})
    status = ooinet["status"] or {}
    for k, v in status.items():
        if isinstance(v, dict):
            flags.append({"severity": "info", "code": "ooinet_empty_window",
                          "text": f"OOINet sub-request {k} returned {v.get('code')}: {v.get('message')}"})
    prov_cols = {"record_id": str(record_id), "year": year, "ooinet_request_id": ooinet["request_id"],
                 "profiler_nc_sha256": ";".join(f.sha256 for f in files["profiler_nc"]),
                 "reference_cnv_sha256": files["reference_cnv"][0].sha256,
                 "config_sha256": next(c["sha256"] for c in code if c["role"] == "config")}
    for k, v in prov_cols.items():
        table[k] = v
        profiles[k] = v
    btab = btab.assign(record_id=str(record_id), year=year)
    ref_out = ctd[["prDM", "depSM", "t090C", "t190C", "sal00", "sal11"]].copy()
    ref_out["in_downcast_reference"] = ctd.index.isin(down.index)
    ref_out["record_id"], ref_out["year"] = str(record_id), year

    # --- write outputs --------------------------------------------------------------------------
    table.to_csv(out / "calibration_table.csv", index=False)
    table.to_parquet(out / "calibration_table.parquet", index=False)
    profiles.to_csv(out / "binned_profiles.csv", index=False)
    btab.to_csv(out / "bottle_check.csv", index=False)
    ref_out.to_csv(out / "reference_ctd_profile.csv", index=False)
    us = summ.reset_index()
    us["record_id"] = str(record_id)
    us.to_csv(out / "upcasts.csv", index=False)
    outputs = [{"name": f.name, "sha256": sha256_file(f), "contentSize": f.stat().st_size}
               for f in sorted(out.glob("*")) if f.is_file() and f.name != "calibration_record.json"]

    primary = next(r for r in rows if r["role"] == "primary")
    reg = next((r for r in rows if r["role"] == "regression"), None)
    flags += annotation_flags(primary)
    record = {
        "@context": CONTEXT, "@id": f"urn:uuid:{record_id}", "@type": ["Dataset", "prov:Entity"],
        "name": f"RCA CTDPFA salinity calibration, {ycfg.get('cruise') or 'uploaded files'} {ycfg.get('ship_cruise_id') or ''} ({year})".replace("  ", " "),
        "dateCreated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "variableMeasured": "sea_water_practical_salinity",
        "rca:correctionKind": kind,
        "prov:wasGeneratedBy": {
            "@type": "prov:Activity", "prov:startedAtTime": started.isoformat(timespec="seconds"),
            "prov:endedAtTime": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "prov:wasAssociatedWith": {"@type": ["SoftwareApplication", "prov:SoftwareAgent"], "name": "rca_sal",
                                       "rca:codeFiles": code, "rca:codeVersion": ooi_qc.code_version(root),
                                       "rca:runtime": {"python": platform.python_version(),
                                                       "pandas": pd.__version__, "numpy": np.__version__,
                                                       "platform": platform.platform()}},
            "prov:used": [_prov_file(sf, gh_infos) for sf in all_sf],
        },
        "rca:ooinet": ooinet,
        **({"rca:profileIndex": {"n_profiles_in_data": int(len(pindex["summ"])), "crosscheck_with_own_upcasts": pindex["crosscheck"]}} if pindex else {}),
        **({"rca:githubSource": github_record(gh_infos, cfg.get("github", {}).get("repo"))} if gh_infos else {}),
        "rca:site": ycfg.get("site"),
        "rca:cruise": cruise_meta(ycfg) | cast_times,
        "rca:parameters": {"global": par, "variants": variants, "notebook_window_utc": w},
        "rca:inputChecks": {"nc_vs_csv": csv_check,
                            "ctd_header": {k: v for k, v in hdr.items()},
                            "ctd_scans_total": int(len(ctd)), "ctd_scans_downcast_reference": int(len(down)),
                            "autosal_header": ahdr,
                            "profiler_samples": int(len(prof)), "n_upcasts": int(len(summ)),
                            "profiler_time_start": prof["time"].min(), "profiler_time_end": prof["time"].max()},
        "rca:bottleCheck": bsum,
        "rca:upcasts": summ.reset_index().to_dict("records"),
        "rca:variants": rows,
        "rca:primaryVariant": primary["variant_id"],
        "rca:twoPoint": {
            "T1": {"variant": primary["variant_id"], "value": primary["offset_psu"],
                   "time_mean_profiler_utc": primary["t1_mean_profiler_time_utc"],
                   "time_cast_utc": cast_time,
                   "notebook_convention_variant": reg["variant_id"] if reg else None,
                   "notebook_convention_value": reg["offset_psu"] if reg else None,
                   "notebook_convention_time_mean_profiler_utc": reg["t1_mean_profiler_time_utc"] if reg else None},
            "T2": {"status": "missing", "reason": ycfg.get("t2_missing_reason",
                   "no second cruise configured for this year; two-point interpolation not computed")},
        },
        "rca:flags": flags,
        "rca:outputs": outputs,
    }
    (out / "calibration_record.json").write_text(json.dumps(_clean(record), indent=2, ensure_ascii=False), encoding="utf8")
    return record
