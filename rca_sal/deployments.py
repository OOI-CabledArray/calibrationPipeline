"""Per-instrument, per-deployment summary of the calibrations, with graded status.

Input: outputs/<run>/calibration_record.json and calibration_table.csv; targets, limits and severities from [deployments] in the config.
A configured run without outputs is listed as "not run", one stopped by a missing input as "missing input"; neither has a level.

Each check is graded pass / marginal / fail against a target and a limit (n/a when there is nothing to compare). Its severity maps a
marginal or failed outcome to a level ([deployments.severity_levels]). The deployment level is the maximum level over its checks, from
1 (good) to 4 (not recommended).

Sign conventions:
  salinity  offset_psu = median(profiler - CTD);  S_corrected = S_raw - offset_psu.
            Conductivity: C_corrected = slope * C_raw (AN31), salinity recomputed with PSS-78.
  oxygen    O2_corrected = gain * O2_raw. No flat offset.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .pipeline import run_key, site_year

_DEP = re.compile(r"deployment(\d{4})_")
CONVENTIONS = __doc__.split("Sign conventions:")[1].strip()
OUTCOMES = ("pass", "marginal", "fail", "n/a")


def _num(x):
    return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)


def _r(x, nd=4):
    return None if x is None else round(float(x), nd)


def _deployment(rec: dict, role: str, configured) -> int | None:
    """Deployment number from the OOINet file name the run actually read (deploymentNNNN_...), else the config's."""
    for u in rec["prov:wasGeneratedBy"]["prov:used"]:
        if u.get("rca:role") == role and (m := _DEP.search(u.get("name", ""))):
            return int(m.group(1))
    return int(configured) if configured is not None else None


# ------------------------------------------------------------------ grading
def _higher(spec: dict) -> bool:
    return spec.get("better", "lower") == "higher"


def grade(value, spec: dict) -> str:
    """pass when the value meets the target, marginal when it is within the limit, fail beyond it (or with no value)."""
    if value is None:
        return "fail"
    if _higher(spec):
        return "pass" if value >= spec["target"] else "marginal" if value > spec["limit"] else "fail"
    return "pass" if value <= spec["target"] else "marginal" if value <= spec["limit"] else "fail"


def _check(name: str, spec: dict, value, units: str, summary: str, details: dict, outcome: str | None = None) -> dict:
    hi = _higher(spec)
    room = (lambda b: None if value is None else _r((value - b) if hi else (b - value), 6))   # > 0: inside the bound
    return {"criterion": name, "severity": spec["severity"], "outcome": outcome or grade(value, spec),
            "value": value, "units": units,
            "better": "higher" if hi else "lower", "target_value": spec["target"], "limit_value": spec["limit"],
            "target": f"{'>=' if hi else '<='} {spec['target']}", "limit": f"{'>' if hi else '<='} {spec['limit']}",
            "margin_to_target": room(spec["target"]), "margin_to_limit": room(spec["limit"]),
            "summary": summary, "details": details}


def _flags(c: dict, level: int, worst: int) -> dict:
    """Machine-readable booleans of one check (null when the check could not be assessed): passed = meets the target,
    within_limit = passes or is marginal, sets_status = it reaches the deployment's worst level above 1."""
    ok = c["outcome"] != "n/a"
    return {"assessed": ok, "passed": c["outcome"] == "pass" if ok else None,
            "within_limit": c["outcome"] in ("pass", "marginal") if ok else None,
            "sets_status": worst > 1 and level == worst and c["outcome"] in ("marginal", "fail")}


def _level(c: dict, dcfg: dict) -> int:
    return 1 if c["outcome"] in ("pass", "n/a") else int(dcfg["severity_levels"][c["severity"]][c["outcome"]])


def status(checks: list[dict], dcfg: dict) -> dict:
    """Worst level reached by any check; names the checks that set it and lists the lesser issues."""
    for c in checks:
        c["level"] = _level(c, dcfg)
    level = max([1] + [c["level"] for c in checks])
    issue = lambda c: f"{c['criterion']} {c['outcome']} ({c['severity']}): {c['summary']}"  # noqa: E731
    for c in checks:                           # a minor marginal check stays at level 1 and sets nothing
        c.update(_flags(c, c["level"], level))
    count = lambda o: sum(c["outcome"] == o for c in checks)  # noqa: E731
    return {"level": level, "label": dcfg["level_labels"][str(level)],
            "all_checks_pass": all(c["passed"] for c in checks if c["assessed"]),
            "n_pass": count("pass"), "n_marginal": count("marginal"), "n_fail": count("fail"), "n_not_assessed": count("n/a"),
            "checks_passed": {c["criterion"]: c["passed"] for c in checks},
            "set_by_checks": [c["criterion"] for c in checks if c["sets_status"]],
            "set_by": [issue(c) for c in checks if c["sets_status"]],
            "other_issues": [issue(c) for c in checks if not c["sets_status"] and c["outcome"] in ("marginal", "fail")],
            "not_assessed": [f"{c['criterion']}: {c['summary']}" for c in checks if c["outcome"] == "n/a"]}


# ------------------------------------------------------------------ shared checks
def _time_gap(p: pd.Series, spec: dict) -> dict:
    gap = float(p["mean_profiler_minus_cast_h"])
    up = None if pd.isna(p.get("upcast_id")) else int(p["upcast_id"])
    ids = p.get("upcast_ids") if isinstance(p.get("upcast_ids"), str) else None
    when = "after" if gap >= 0 else "before"
    which = f" (upcast #{up})" if up is not None else f" (mean time of upcasts {ids.replace(';', ', ')})" if ids else ""
    return _check("time_gap_h", spec, _r(abs(gap), 2), "h", f"profiler samples are {abs(gap):.2f} h {when} the CTD cast{which}",
                  {"profiler_minus_cast_h": _r(gap, 2), "cast_time_utc": p["t1_cast_time_utc"],
                   "mean_profiler_time_utc": p["t1_mean_profiler_time_utc"],
                   "samples_from_utc": p["sample_time_start_utc"], "samples_to_utc": p["sample_time_end_utc"], "upcast_id": up,
                   "upcast_ids": ids})


def _rmse(before: float, after: float, spec: dict, units: str, nd: int, extra: dict) -> dict:
    pct = 100 * (before - after) / before if before else None
    verb = "drops" if after < before else "rises" if after > before else "is unchanged"
    shown = {"umolkg": "umol/kg"}.get(units, units)
    return _check("rmse_reduction_pct", spec, _r(pct, 1), "%",
                  f"RMSE {verb} from {before:.{nd}f} to {after:.{nd}f} {shown} ({after - before:+.{nd}f} {shown}, "
                  f"{-pct if pct is not None else float('nan'):+.1f}%)",
                  {f"rmse_before_{units}": _r(before, nd), f"rmse_after_{units}": _r(after, nd),
                   f"change_{units}": _r(after - before, nd), "change_pct": _r(-pct, 1) if pct is not None else None, **extra})


def _per_upcast(tab: pd.DataFrame, p: pd.Series, col: str) -> dict:
    pu = tab[(tab["role"] == "per_upcast")]
    if "reference_depth_col" in tab and "reference_depth_col" in p:
        pu = pu[pu["reference_depth_col"] == p["reference_depth_col"]]
    v = pu[col].dropna()
    if v.empty:
        return {}
    return {"n_upcasts": int(v.size), "per_upcast_min": _r(v.min(), 5), "per_upcast_max": _r(v.max(), 5),
            "per_upcast_range": _r(v.max() - v.min(), 5), "per_upcast_sd": _r(v.std(ddof=1), 5) if v.size > 1 else None}


def _first_present(tab: pd.DataFrame, ids: list[str]) -> str | None:
    return next((v for v in ids if v in tab.index), None)


def _n_upcasts(p: pd.Series) -> int | None:
    n = p.get("n_upcasts")
    return None if n is None or pd.isna(n) else int(n)


def _vs_single(name: str, tab: pd.DataFrame, p: pd.Series, col: str, single_ids: list[str], spec: dict, nd: int, units: str) -> dict:
    single, mine = _first_present(tab, single_ids), float(p[col])
    spread = _per_upcast(tab, p, col)
    if single is None:
        return _check(name, spec, None, units, f"no single-upcast variant ({', '.join(single_ids)}) in this run", spread, outcome="n/a")
    other = float(tab.loc[single, col])
    d, u = abs(mine - other), f" {units}" if units else ""
    sg = "+" if units else ""            # offsets carry a sign, gains do not
    n = _n_upcasts(p)
    text = (f"{f'{n}-upcast mean' if n else 'primary'} {mine:{sg}.{nd}f} vs the single upcast nearest the cast {other:{sg}.{nd}f} "
            f"({single}): differ by {d:.{nd}f}{u}")
    if spread:
        text += (f"; {spread['n_upcasts']} single upcast{'s' if spread['n_upcasts'] != 1 else ''} range "
                 f"{spread['per_upcast_min']:{sg}.{nd}f} to {spread['per_upcast_max']:{sg}.{nd}f}"
                 + (f" (SD {spread['per_upcast_sd']:.{nd}f})" if spread["per_upcast_sd"] else ""))
    return _check(name, spec, _r(d, nd), units, text, {"primary": _r(mine, nd + 1), "single_upcast": _r(other, nd + 1),
                                                       "single_upcast_variant": single, **spread})


def _annotations_block(rec: dict, p: pd.Series) -> dict:
    """OOI HITL annotations of the request, and those that cover the samples the primary variant used."""
    raw = p.get("annotations_on_samples")
    on = json.loads(raw) if isinstance(raw, str) else []
    return {"n_in_request": len(rec["rca:ooinet"].get("annotations") or []), "has_annotations_on_samples": bool(on), "n_on_samples": len(on),
            "has_exclusion_annotation": any(a["exclusion_flag"] for a in on), "n_samples_dropped_by_annotation": int(p["n_annotation_excluded"]),
            "on_samples": on}


# ------------------------------------------------------------------ salinity
def _reference(rec: dict, sensor: str, spec: dict) -> dict:
    bc = rec.get("rca:bottleCheck") or {}
    ref = bc.get(sensor)
    others = {k: _r((v.get("without_outliers") or {}).get("median_diff_psu")) for k, v in bc.items() if k != sensor}
    if not ref:
        return _check("reference_median_abs_psu", spec, None, "PSU", f"no CTD vs Autosal check for {sensor} in the record",
                      {"sensor": sensor}, outcome="fail")
    det = {"sensor": ref.get("sensor", sensor), "n_bottles": ref["n"], "n_flagged": ref["n_outliers_flagged"],
           "median_all_psu": _r(ref["median_diff_psu"]), "rmse_all_psu": _r(ref["rmse_psu"]),
           "other_sensor_median_psu": others, "min_bottles": spec["min_bottles"], "n_excluded_by_flag": ref.get("n_excluded_by_flag", 0)}
    wo = ref.get("without_outliers")
    if not wo:
        return _check("reference_median_abs_psu", spec, None, "PSU",
                      f"CTD {sensor} vs Autosal: every one of {ref['n']} bottles is flagged as an outlier "
                      f"(median of all {ref['median_diff_psu']:+.4f} PSU), so the reference cannot be confirmed", det, outcome="fail")
    v = _r(abs(wo["median_diff_psu"]))
    outcome = grade(v, spec)
    text = (f"CTD {sensor} minus Autosal: median {wo['median_diff_psu']:+.4f} PSU, RMSE {wo['rmse_psu']:.4f} PSU over "
            f"{wo['n']} bottles ({ref['n_outliers_flagged']} of {ref['n']} flagged)"
            + (f"; {ref['n_excluded_by_flag']} more left out by the cruise's own bottle flags" if ref.get("n_excluded_by_flag") else ""))
    if wo["n"] < spec["min_bottles"] and outcome == "pass":
        outcome, text = "marginal", text + f"; fewer than {spec['min_bottles']} usable bottles"
    return _check("reference_median_abs_psu", spec, v, "PSU", text,
                  {**det, "n_used": wo["n"], "median_used_psu": _r(wo["median_diff_psu"]), "rmse_used_psu": _r(wo["rmse_psu"])},
                  outcome=outcome)


def _sensor_agreement(tab: pd.DataFrame, p: pd.Series, spec: dict) -> dict:
    same = (tab["role"] != "per_upcast") & (tab["selection"] == p["selection"]) & \
           (tab["reference_depth_col"] == p["reference_depth_col"]) & (tab["reference_sal_col"] != p["reference_sal_col"])
    if not pd.isna(p["upcast_id"]):
        same &= tab["upcast_id"] == p["upcast_id"]
    alt = tab[same]
    if alt.empty:
        return _check("sensor_agreement_psu", spec, None, "PSU", "no variant against the other CTD sensor in this run", {}, outcome="n/a")
    q = alt.iloc[0]
    d = abs(float(p["offset_psu"]) - float(q["offset_psu"]))
    return _check("sensor_agreement_psu", spec, _r(d), "PSU",
                  f"offset against {p['reference_sal_col']} {p['offset_psu']:+.4f} vs against {q['reference_sal_col']} "
                  f"{q['offset_psu']:+.4f}: differ by {d:.4f} PSU",
                  {"variant": alt.index[0], "offset_primary_sensor_psu": _r(p["offset_psu"], 5),
                   "offset_other_sensor_psu": _r(q["offset_psu"], 5)})


def _salinity_entry(rec: dict, tab: pd.DataFrame, dcfg: dict) -> dict:
    crit = dcfg["salinity"]
    vid = rec["rca:primaryVariant"]
    p = tab.loc[vid]
    off, slope, iqr = float(p["offset_psu"]), _num(p["an31_slope"]), float(p["iqr_psu"])
    sb, so, ss = (_num(p[c]) for c in ("rmse_before_common_bins_psu", "rmse_after_offset_common_bins_psu", "rmse_after_slope_common_bins_psu"))
    rmse = _rmse(float(p["rmse_before_psu"]), float(p["rmse_after_offset_psu"]), crit["rmse_reduction_pct"], "PSU", 4,
                 {"n_bins": int(p["n_bins"]), "depth_range_m": [_r(p["depth_min_m"], 1), _r(p["depth_max_m"], 1)],
                  "an31_rmse_after_slope_psu": _r(ss), "an31_change_vs_offset_psu": _r(ss - so) if None not in (ss, so) else None,
                  "an31_common_bins_rmse_before_psu": _r(sb)})
    checks = [
        rmse,
        _reference(rec, str(p["reference_sal_col"]), crit["reference_median_abs_psu"]),
        _vs_single("primary_minus_single_upcast_psu", tab, p, "offset_psu", dcfg["single_upcast_variants"],
                   crit["primary_minus_single_upcast_psu"], 4, "PSU"),
        _check("iqr_psu", crit["iqr_psu"], _r(iqr), "PSU",
               f"per-bin profiler-CTD differences have an IQR of {iqr:.4f} PSU over {int(p['n_bins'])} bins "
               f"({p['depth_min_m']:.0f}-{p['depth_max_m']:.0f} m), {iqr / abs(off) if off else float('inf'):.1f}x the offset",
               {"offset_psu": _r(off, 5), "iqr_over_abs_offset": _r(iqr / abs(off), 2) if off else None,
                "n_bins": int(p["n_bins"]), "n_profiler_samples": int(p["n_profiler_samples"])}),
        _sensor_agreement(tab, p, crit["sensor_agreement_psu"]),
        _time_gap(p, crit["time_gap_h"]),
    ]
    return {
        "flat_offset": {"value": round(-off, 4), "units": "PSU",
                        "apply": f"S_corrected = S_raw {'+' if off <= 0 else '-'} {abs(off):.4f}",
                        "offset_psu_profiler_minus_ctd": round(off, 4)},
        "function_offset": None if slope is None else
        {"slope": round(slope, 6), "apply": f"C_corrected = {slope:.6f} * C_raw (AN31), S from PSS-78"},
        "basis": {"variant": vid, "n_profiler_samples": int(p["n_profiler_samples"]), "n_upcasts": _n_upcasts(p),
                  "upcast_ids": p.get("upcast_ids") if isinstance(p.get("upcast_ids"), str) else None,
                  "profiler_minus_cast_h": round(float(p["mean_profiler_minus_cast_h"]), 2),
                  "reference_extrapolated": bool(p["reference_extrapolated"]), "n_bins_outside_reference": int(p["n_bins_outside_reference"])},
        "annotations": _annotations_block(rec, p),
        "checks": checks,
    }


# ------------------------------------------------------------------ oxygen
def _oxygen_entry(rec: dict, tab: pd.DataFrame, dcfg: dict) -> dict:
    crit = dcfg["oxygen"]
    vid = rec["rca:primaryVariant"]
    p = tab.loc[vid]
    g, direct = float(p["dofsta_gain"]), _num(p["dofsta_vs_winkler_direct_gain"])
    gi = float(p["dofsta_gain_iqr"])
    chain = (_check("chain_vs_direct_gain", crit["chain_vs_direct_gain"], None, "", "no direct DOFSTA vs Winkler gain in this run",
                    {}, outcome="n/a") if direct is None else
             _check("chain_vs_direct_gain", crit["chain_vs_direct_gain"], _r(abs(g - direct), 5), "",
                    f"gain via the CTD {g:.5f} vs DOFSTA directly against {int(p['n_winkler'])} Winkler bottles {direct:.5f}: "
                    f"differ by {abs(g - direct):.5f}",
                    {"gain_via_ctd": _r(g, 5), "gain_direct": _r(direct, 5), "n_winkler": int(p["n_winkler"]),
                     "ctd_gain": _r(p["ctd_gain"], 5), "ctd_gain_iqr": _r(p["ctd_gain_iqr"], 5)}))
    dep, nw, nwa = abs(g - 1), int(p["n_winkler"]), int(p["n_winkler_available"])
    checks = [
        _rmse(float(p["rmse_before_umolkg"]), float(p["rmse_after_umolkg"]), crit["rmse_reduction_pct"], "umolkg", 2,
              {"n_bins": int(p["n_bins"]), "depth_range_m": [_r(p["depth_min_m"], 1), _r(p["depth_max_m"], 1)]}),
        chain,
        _vs_single("primary_minus_single_upcast_gain", tab, p, "dofsta_gain", dcfg["single_upcast_variants_oxygen"],
                   crit["primary_minus_single_upcast_gain"], 4, ""),
        _check("gain_departure_from_one", crit["gain_departure_from_one"], _r(dep, 5), "",
               f"DOFSTA gain {g:.5f} is {100 * dep:.1f}% from 1 (the OOI BGC guide quotes 5-10% SBE43 drift over a 100-day deployment)",
               {"gain": _r(g, 5), "departure_pct": _r(100 * dep, 2), "ctd_gain": _r(p["ctd_gain"], 5)}),
        _check("winkler_bottles_used", crit["winkler_bottles_used"], nw, "bottles",
               f"{nw} of {nwa} Winkler bottles reached the shipboard CTD profile and set the CTD gain",
               {"n_winkler_used": nw, "n_winkler_available": nwa, "n_winkler_direct_gain": int(p["n_winkler_direct"]),
                "bottle_source": p.get("bottle_source")}),
        _check("gain_iqr", crit["gain_iqr"], _r(gi), "", f"per-bin DOFSTA gain has an IQR of {gi:.4f} over {int(p['n_bins'])} bins "
               f"({p['depth_min_m']:.0f}-{p['depth_max_m']:.0f} m)", {"gain": _r(g, 5), "n_bins": int(p["n_bins"])}),
        _time_gap(p, crit["time_gap_h"]),
    ]
    return {
        "flat_offset": None,
        "function_offset": {"gain": round(g, 5), "apply": f"O2_corrected = {g:.5f} * O2_raw", "ctd_gain": round(float(p["ctd_gain"]), 5)},
        "basis": {"variant": vid, "n_winkler": int(p["n_winkler"]), "n_winkler_available": nwa, "bottle_source": p.get("bottle_source"),
                  "n_upcasts": _n_upcasts(p), "upcast_ids": p.get("upcast_ids") if isinstance(p.get("upcast_ids"), str) else None,
                  "profiler_minus_cast_h": round(float(p["mean_profiler_minus_cast_h"]), 2),
                  "reference_extrapolated": bool(p["reference_extrapolated"]), "n_bins_outside_reference": int(p["n_bins_outside_reference"])},
        "annotations": _annotations_block(rec, p),
        "checks": checks,
    }


# ------------------------------------------------------------------ summary
def _runs(cfg: dict) -> list[tuple[str, str | None, str]]:
    """(year, site, variable) for every configured run: the default site's salinity and oxygen, then the other sites'."""
    out = []
    for year, y in sorted(cfg["years"].items()):
        out.append((year, None, "salinity"))
        if "oxygen" in y:
            out.append((year, None, "oxygen"))
        for s, sc in sorted(y.get("sites", {}).items()):
            out += [(year, s, "salinity")] + ([(year, s, "oxygen")] if "oxygen" in sc else [])
    return out


def _oxygen_refdes(rec: dict) -> str | None:
    return (rec.get("rca:ooinet") or {}).get("reference_designator")


def _not_assessed(reason: str) -> dict:
    return {"level": None, "label": "not assessed", "reason": reason}


def summarize(cfg: dict, outputs: str | Path) -> dict:
    outputs, dcfg = Path(outputs), cfg["deployments"]
    groups: dict[tuple, dict] = {}
    for year, site, var in _runs(cfg):
        ycfg = site_year(cfg, year, site)
        key = run_key(cfg, year, site)
        folder = outputs / key / ("oxygen" if var == "oxygen" else "")
        recp = folder / "calibration_record.json"
        rec = json.loads(recp.read_text(encoding="utf8")) if recp.is_file() else None
        refdes = ycfg.get("profiler_reference_designator")
        if var == "oxygen":
            refdes = ((rec and _oxygen_refdes(rec)) or cfg.get("sites", {}).get(ycfg.get("site"), {}).get("oxygen_reference_designator")
                      or f"{refdes} (oxygen)")
        site_code = ycfg.get("site")
        g = groups.setdefault((var, refdes), {"refdes": refdes, "variable": var, "site": site_code,
                                              "site_name": cfg.get("sites", {}).get(site_code, {}).get("name"), "deployments": []})
        base = {"year": int(year), "run": key, "cruise": ycfg.get("cruise"), "ship_cruise_id": ycfg.get("ship_cruise_id"),
                "cast": (ycfg.get("oxygen", {}).get("cast") if var == "oxygen" else None) or ycfg.get("cast")}
        if (folder / "missing.json").is_file():
            miss = [m["role"] for m in json.loads((folder / "missing.json").read_text(encoding="utf8"))["missing"]]
            g["deployments"].append({**base, "deployment": ycfg.get("deployment"), "calibrated": False, "calibration": "missing input", "missing": miss,
                                     "status": _not_assessed("missing input: " + ", ".join(miss))})
            continue
        if rec is None:
            reason = "profiler data not requested yet" if "profiler_nc" not in ycfg.get("inputs", {}) else "pipeline not run"
            g["deployments"].append({**base, "deployment": ycfg.get("deployment"), "calibrated": False, "calibration": "not run", "reason": reason,
                                     "status": _not_assessed(reason)})
            continue
        tab = pd.read_csv(folder / "calibration_table.csv").set_index("variant_id")
        if var == "salinity":
            body, dep = _salinity_entry(rec, tab, dcfg), _deployment(rec, "profiler_nc", ycfg.get("deployment"))
        else:
            body, dep = _oxygen_entry(rec, tab, dcfg), _deployment(rec, "dofsta_nc", ycfg.get("deployment"))
        g["deployments"].append({**base, "deployment": dep, "calibrated": True, "calibration": "calibrated", "record_id": rec["@id"],
                                 "status": status(body["checks"], dcfg), **body})
    for g in groups.values():
        g["deployments"].sort(key=lambda d: (d["deployment"] is None, d["deployment"] or 0, d["year"]))
    default = cfg.get("sites", {}).get("default")
    return {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "conventions": CONVENTIONS, "criteria": dcfg,
            "instruments": sorted(groups.values(), key=lambda g: (g["variable"] != "salinity", g["site"] != default,
                                                                   g["site"] or "", g["refdes"] or ""))}


# ------------------------------------------------------------------ YAML without an extra dependency
_PLAIN = re.compile(r"^[A-Za-z0-9_./()+*=<>~ -]+$")


_RESERVED = {"na", "true", "false", "null", "~", "yes", "no", "on", "off", "y", "n"}


def _is_number(s: str) -> bool:
    try:
        float(s.replace("_", "x"))      # YAML reads 1_000 as a number too; "x" keeps it a string for float()
        return True
    except ValueError:
        return False


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        s = repr(v)
        return s.replace("e", ".0e") if "e" in s and "." not in s else s     # YAML 1.1 reads 3e-05 as text, 3.0e-05 as a number
    if isinstance(v, int):
        return repr(v)
    s = str(v)
    plain = (_PLAIN.match(s) and s == s.strip() and s[0] not in "-><=*" and s.lower() not in _RESERVED and not _is_number(s))
    return s if plain else json.dumps(s)


def to_yaml(obj, indent: int = 0) -> str:
    """Block-style YAML for dicts, lists and scalars (None is written null)."""
    pad, lines = "  " * indent, []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines += [f"{pad}{k}:", to_yaml(v, indent + 1)]
            elif isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}")
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)) and v:
                inner = to_yaml(v, indent + 1).split("\n")
                lines += [f"{pad}- {inner[0].lstrip()}"] + inner[1:]
            else:
                lines.append(f"{pad}- {_scalar(v)}")
    else:
        lines.append(pad + _scalar(obj))
    return "\n".join(lines)
