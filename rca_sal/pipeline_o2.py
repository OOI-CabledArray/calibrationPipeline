"""Oxygen calibration of one run: outputs/<run>/oxygen/. Input handling is the same as in rca_sal.pipeline.

Winkler data come from the cruise discrete summary (discrete_summary_csv) and/or the Winkler workbook (winkler_xls). A variant selects
its source with `bottles` ("summary" or "workbook"); without it the summary is used when configured.
"""
from __future__ import annotations

import json
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import calibrate as cal
from . import oxygen as ox
from . import ooi_qc, profile_index, profiler, seabird
from .lineage import sha256_file
from .pipeline import (CONTEXT, PROJECT_ROOT, _clean, _code_fingerprint, _expand_variants, _prov_file, _resolve, _select,
                       OXYGEN_BOTTLE_ROLES, OXYGEN_REQUIRED, MissingInputError, annotate_github, annotation_flags, cruise_meta, fingerprint_inputs,
                       github_record, load_config, ooinet_block, require_roles, resolve_github_inputs, run_key, site_year,
                       upcasts_used, year_variants)


def _o2_variant(var, d, labelled, summ, ctd, refs, winks, cast_time, window, par, kind, cast_start=None, refdes=None, annotations=()):
    if var["select"] == "upcasts_one":
        sub, nearest = labelled[labelled["upcast_id"] == var["upcast_id"]], var["upcast_id"]
    else:
        sub, nearest = _select(var, d, labelled, summ, cast_time, window, cast_start)
    if len(sub) == 0:
        raise ValueError(f"variant {var['id']} selected no DOFSTA samples")
    sub, n_excluded = ooi_qc.drop_excluded(sub)
    if len(sub) == 0:
        raise ValueError(f"variant {var['id']} has no DOFSTA samples left after dropping those inside exclusion annotations")
    on_samples = ooi_qc.annotations_on_samples(annotations, refdes, sub["time"])
    ext = bool(var.get("extrapolate_reference", par["extrapolate_reference"]))
    ref = refs[var["ref"]]
    dc, wc = var["ctd_depth_col"], var["winkler_depth_col"]
    source = var.get("bottles") or ("summary" if "summary" in winks else "workbook")
    if source not in winks:
        raise ValueError(f"variant {var['id']} wants Winkler bottles from the {source} but none is configured")
    w = winks[source]
    if var.get("use_winkler_flags"):
        w = w[~w["bottle_flag"].isin(par["oxygen"]["winkler_bad_flags"])]
    g = ox.winkler_gain(w[wc], w["o2_winkler_umolkg"], ref[dc], ref["o2_ctd_umolkg"], ext)
    ref_cal = ref.assign(o2_ctd_calibrated=ref["o2_ctd_umolkg"] * g.gain)
    smooth = ("o2_umolkg",) if var["smooth"] else ()
    binned = cal.bin_and_smooth(sub["depth_m"], {"o2_umolkg": sub["o2_umolkg"]}, bin_size=par["bin_size_m"],
                                smooth_window=par["smooth_window_bins"], smooth=smooth)
    # The DOFSTA gain is measured against the Winkler-calibrated CTD, so it already includes the CTD gain:
    # it is the total gain from raw DOFSTA to Winkler scale and is not multiplied by ctd_gain again.
    res = cal.estimate_correction(binned, ref_cal[dc], ref_cal["o2_ctd_calibrated"], col="o2_umolkg", kind=kind, extrapolate=ext)
    # DOFSTA compared with the Winkler samples directly, as a check on the two-step gain.
    dof_at_w = cal.reference_at(w[wc], binned.index, binned["o2_umolkg"], ext)
    ok = np.isfinite(dof_at_w)
    direct = float(np.median(w["o2_winkler_umolkg"].to_numpy(float)[ok] / dof_at_w[ok])) if ok.any() else float("nan")
    t1 = profiler.mean_time(sub["time"])
    z = binned.index.to_numpy(float)
    ref_at = cal.reference_at(z, ref_cal[dc], ref_cal["o2_ctd_calibrated"], ext)
    outside = int((np.isfinite(binned["o2_umolkg"].to_numpy())
                   & ~np.isfinite(cal.reference_at(z, ref_cal[dc], ref_cal["o2_ctd_calibrated"], False))).sum())
    have = np.isfinite(binned["o2_umolkg"].to_numpy())
    series = pd.DataFrame({
        "variant_id": var["id"], "depth_bin_m": z, "n_samples": binned["n_samples"].to_numpy(),
        "raw": binned["o2_umolkg"].to_numpy(), "corrected": binned["o2_umolkg"].to_numpy() * res.value,
        "reference": np.where(have, ref_at, np.nan)})
    wtab = pd.DataFrame({
        "variant_id": var["id"], "bottle_source": source, "niskin": w["niskin"].to_numpy(), "bottle_flag": w["bottle_flag"].to_numpy(),
        "winkler_depth_m": w["depth_m"].to_numpy(), "winkler_pressure_dbar": w["pressure_dbar"].to_numpy(),
        "o2_winkler_umolkg": w["o2_winkler_umolkg"].to_numpy(), "o2_ctd_at_winkler_umolkg": g.ctd_at_winkler,
        "ratio_winkler_over_ctd": g.ratios})
    ctd_prof = None
    if var["role"] != "per_upcast":
        ctd_prof = pd.DataFrame({"variant_id": var["id"], "depth_m": ref_cal[dc].to_numpy(),
                                 "o2_ctd_umolkg": ref_cal["o2_ctd_umolkg"].to_numpy(),
                                 "o2_ctd_calibrated_umolkg": ref_cal["o2_ctd_calibrated"].to_numpy()})
    row = {
        "variant_id": var["id"], "role": var["role"], "description": var["description"], "selection": var["select"],
        "bottle_source": source, "upcast_id": nearest, **dict(zip(("n_upcasts", "upcast_ids"), upcasts_used(sub))),
        "smoothed": bool(var["smooth"]), "reference_filter": var["ref"],
        "winkler_depth_col": wc, "ctd_depth_col": dc, "winkler_flags_applied": bool(var.get("use_winkler_flags")),
        "kind": kind, "n_annotation_excluded": n_excluded, "annotations_on_samples": json.dumps(on_samples) if on_samples else None,
        "reference_extrapolated": ext, "n_bins_outside_reference": outside,
        "n_dofsta_samples": int(len(sub)), "n_bins": res.n_bins,
        "depth_min_m": res.depth_min_m, "depth_max_m": res.depth_max_m,
        "n_winkler": g.n, "n_winkler_available": int(len(w)), "n_winkler_direct": int(ok.sum()), "ctd_gain": g.gain, "ctd_gain_iqr": g.iqr,
        "dofsta_gain": res.value, "dofsta_gain_iqr": res.iqr,
        "rmse_before_umolkg": res.rmse, "rmse_after_umolkg": res.rmse_after,
        "dofsta_vs_winkler_direct_gain": direct,
        "t1_mean_profiler_time_utc": t1.isoformat(),
        "sample_time_start_utc": sub["time"].min().isoformat(), "sample_time_end_utc": sub["time"].max().isoformat(),
        "t1_cast_time_utc": pd.Timestamp(cast_time).isoformat(),
        "mean_profiler_minus_cast_h": float((t1 - cast_time).total_seconds() / 3600),
    }
    return row, series, wtab, ctd_prof


def _summary_flags(wink: pd.DataFrame) -> list[dict]:
    """Caveats from the discrete summary's own flags: rows with notes, an alternative method, or no oxygen flag at all."""
    out = []
    bits = wink["o2_flag"].map(ox.summary_flag_bits)
    nf = wink[bits.isna()]
    if len(nf):
        out.append({"severity": "warning", "code": "winkler_flag_missing",
                    "text": f"{len(nf)} discrete-summary oxygen row(s) (Niskin {sorted({int(n) for n in nf['niskin']})}) have no oxygen "
                            "flag; they are kept as unflagged."})
    for bit, what in ((7, "processed with an alternative method (see the cruise's notes)"), (0, "carry a note (see the cruise's notes)")):
        hit = wink[bits.map(lambda b, bit=bit: bool(b) and bit in b)]
        if len(hit):
            out.append({"severity": "caveat", "code": f"winkler_flag_bit{bit}",
                        "text": f"{len(hit)} discrete-summary oxygen row(s) (Niskin {sorted({int(n) for n in hit['niskin']})}) are flagged "
                                f"as {what}; they are kept."})
    return out


def run_year_oxygen(year: int, config_path: str | Path, out_root: str | Path, root: Path = PROJECT_ROOT, site: str | None = None) -> dict:
    started = datetime.now(timezone.utc)
    cfg = load_config(config_path)
    if str(year) not in cfg.get("years", {}):
        raise KeyError(f"year {year} is not in {config_path}")
    ycfg = site_year(cfg, year, site)
    if "oxygen" not in ycfg:
        raise KeyError(f"no oxygen section for {run_key(cfg, year, site)} in {config_path}")
    ocfg, par = ycfg["oxygen"], cfg["parameters"]
    kind = par["oxygen"]["correction_kind"]
    out = Path(out_root) / run_key(cfg, year, site) / "oxygen"
    out.mkdir(parents=True, exist_ok=True)
    stale = out / "missing.json"
    if stale.exists():
        stale.unlink()
    shared = ("reference_cnv", "reference_btl", "discrete_summary_csv", "profile_index_csv")
    inputs = {k: ycfg["inputs"][k] for k in shared if k in ycfg["inputs"]} | ocfg["inputs"]
    require_roles(inputs, OXYGEN_REQUIRED)
    if not any(r in inputs for r in OXYGEN_BOTTLE_ROLES):
        raise MissingInputError([{"role": " or ".join(OXYGEN_BOTTLE_ROLES), "path": "(not configured for this year)"}])
    inputs, gh_infos = resolve_github_inputs(cfg, root, inputs)
    files = annotate_github(fingerprint_inputs({"inputs": inputs}, root), gh_infos)
    P = lambda role: _resolve(inputs[role], root)  # noqa: E731
    flags: list[dict] = []

    ctd, hdr = seabird.read_cnv(P("reference_cnv"))
    ctd["o2_ctd_umolkg"] = ox.ctd_o2_umolkg(ctd)
    dc = par["ctd_downcast"]
    down = seabird.downcast(ctd, "prDM", dc["min_pressure_dbar"], dc["heave_tolerance_dbar"])
    refs = {"raw": ctd, "downcast": down}
    if "sbox0Mm/Kg" not in ctd.columns:
        flags.append({"severity": "caveat", "code": "ctd_o2_converted",
                      "text": "The .cnv has no sbox0Mm/Kg column, so shipboard SBE43 oxygen was converted from ml/l to umol/kg "
                              "with the Sea-Bird formula (ml/l x 44660 / (EOS-80 sigma-theta + 1000)), using sal00, t090C and prDM."})
    flags.append({"severity": "caveat", "code": "ctd_processing",
                  "text": f"Reference .cnv was processed with {', '.join(hdr.get('processing', [])) or 'unknown steps'} only; "
                          "alignctd / celltm / loopedit are not applied, which matters for the SBE43 oxygen sensor."})

    cast = ocfg.get("cast") or ycfg["cast"]
    winks = {}
    if "discrete_summary_csv" in inputs:
        winks["summary"] = ox.read_discrete_oxygen(P("discrete_summary_csv"), cast)
        flags.append({"severity": "caveat", "code": "winkler_from_summary",
                      "text": "Winkler oxygen is the discrete summary's mL/L value converted to umol/kg with the Sea-Bird formula, "
                              "using the CTD salinity 1, temperature 1 and pressure at each bottle closure."})
        flags += _summary_flags(winks["summary"])
    if "winkler_xls" in inputs:
        winks["workbook"] = ox.read_winkler(P("winkler_xls"), cast)
    wink_all = winks.get("summary", winks.get("workbook"))
    bad = wink_all["bottle_flag"].isin(par["oxygen"]["winkler_bad_flags"])
    if bad.any():
        flags.append({"severity": "warning", "code": "winkler_flagged",
                      "text": f"{int(bad.sum())} Winkler row(s) carry a bad bottle flag; excluded from every variant except the exact reproduction."})

    d = ox.read_dofsta_nc(P("dofsta_nc"))
    if d.attrs.get("o2_units") not in ("umol kg-1",):
        raise ValueError(f"unexpected DOFSTA O2 units {d.attrs.get('o2_units')!r}")
    site_cfg = cfg.get("sites", {}).get(ycfg.get("site"), {})
    refdes = ocfg.get("profiler_reference_designator") or site_cfg.get("oxygen_reference_designator")
    annotations = json.loads(P("ooinet_annotations").read_text(encoding="utf8")).get("annotations", []) if "ooinet_annotations" in inputs else []
    units = d.attrs.get("o2_units")
    d = ooi_qc.mark_exclusions(d, annotations, refdes)
    d.attrs["o2_units"] = units
    labelled = profiler.label_upcasts(d, profiler.UpcastParams(**par["upcast"]))
    summ = profiler.upcast_summary(labelled)
    if summ.empty:
        raise ValueError("no DOFSTA upcasts found")
    pindex = None
    if "profile_index_csv" in inputs:
        lab_ix = profile_index.label_by_index(d, profile_index.read_index(P("profile_index_csv")))
        pindex = {"labelled": lab_ix, "summ": profiler.upcast_summary(lab_ix)}

    btl = seabird.read_btl(P("reference_btl"))
    cast_start, cast_end = hdr["nmea_utc"], btl["time_utc"].max()
    cast_time = cast_start + (cast_end - cast_start) / 2
    cast_times = {"cast_start_utc": cast_start, "last_bottle_closure_utc": cast_end, "cast_time_utc": cast_time,
                  "definition": "cast_time = midpoint of NMEA cast start (.cnv header) and the last bottle closure (.btl)"}

    w = ocfg.get("notebook_window_utc")
    window = (pd.Timestamp(w["start"]), pd.Timestamp(w["end"])) if w else None
    rows, series, wtabs, ctdp = [], [], [], []
    variants = year_variants(cfg, ocfg.get("variants"), w, oxygen=True, index=pindex is not None, workbook="workbook" in winks)
    for v in _expand_variants(variants, summ):
        if v["select"].startswith("index_"):                 # same selection rules, applied to the official profile index
            if pindex["summ"].empty:
                raise ValueError(f"variant {v['id']}: no profile of the official index overlaps the DOFSTA data")
            r, s, wt, cp = _o2_variant({**v, "select": v["select"][len("index_"):]}, d, pindex["labelled"], pindex["summ"], ctd, refs,
                                       winks, cast_time, window, par, kind, cast_start, refdes, annotations)
            r["selection"] = v["select"]
        else:
            r, s, wt, cp = _o2_variant(v, d, labelled, summ, ctd, refs, winks, cast_time, window, par, kind, cast_start, refdes, annotations)
        rows.append(r)
        series.append(s)
        wtabs.append(wt)
        if cp is not None:
            ctdp.append(cp)
    table = pd.DataFrame(rows)
    profiles = pd.concat(series, ignore_index=True)
    wtab = pd.concat(wtabs, ignore_index=True)
    ctd_profiles = pd.concat(ctdp, ignore_index=True)

    all_sf = [sf for lst in files.values() for sf in lst]
    code = _code_fingerprint(config_path)
    record_id = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
        {"year": year, "site": ycfg.get("site"), "var": "oxygen", "inputs": sorted(sf.sha256 for sf in all_sf),
         "code": sorted(c["sha256"] for c in code)}))
    st = cfg.get("sites", {}).get(ycfg.get("site"), {})
    ooinet = ooinet_block({**ocfg, "deployment": ycfg.get("deployment"), "inputs": inputs,
                           "profiler_reference_designator": ocfg.get("profiler_reference_designator") or st.get("oxygen_reference_designator"),
                           "profiler_stream": ocfg.get("profiler_stream") or st.get("oxygen_stream")}, P)
    prov_cols = {"record_id": str(record_id), "year": year, "ooinet_request_id": ooinet["request_id"],
                 "dofsta_nc_sha256": files["dofsta_nc"][0].sha256,
                 **{f"{r}_sha256": files[r][0].sha256 for r in OXYGEN_BOTTLE_ROLES if r in files},
                 "reference_cnv_sha256": files["reference_cnv"][0].sha256,
                 "config_sha256": next(c["sha256"] for c in code if c["role"] == "config")}
    for k, v in prov_cols.items():
        table[k] = v
        profiles[k] = v
    wtab = wtab.assign(record_id=str(record_id), year=year)
    ctd_profiles = ctd_profiles.assign(record_id=str(record_id), year=year)

    table.to_csv(out / "calibration_table.csv", index=False)
    table.to_parquet(out / "calibration_table.parquet", index=False)
    profiles.to_csv(out / "binned_profiles.csv", index=False)
    wtab.to_csv(out / "winkler_check.csv", index=False)
    ctd_profiles.to_csv(out / "ctd_o2_profiles.csv", index=False)
    us = summ.reset_index()
    us["record_id"] = str(record_id)
    us.to_csv(out / "upcasts.csv", index=False)
    outputs = [{"name": f.name, "sha256": sha256_file(f), "contentSize": f.stat().st_size}
               for f in sorted(out.glob("*")) if f.is_file() and f.name != "calibration_record.json"]

    primary = next(r for r in rows if r["role"] == "primary")
    reg = next((r for r in rows if r["role"] == "regression"), None)
    flags += annotation_flags(primary)
    site_name = st.get("name")
    record = {
        "@context": CONTEXT, "@id": f"urn:uuid:{record_id}", "@type": ["Dataset", "prov:Entity"],
        "name": f"RCA DOFSTA oxygen gain, {ycfg.get('cruise') or 'uploaded files'} {ycfg.get('ship_cruise_id') or ''} ({year})".replace("  ", " ")
                + (f", {site_name}" if site_name else ""),
        "dateCreated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "variableMeasured": "corrected_dissolved_oxygen", "rca:correctionKind": kind, "rca:site": ycfg.get("site"),
        "prov:wasGeneratedBy": {
            "@type": "prov:Activity", "prov:startedAtTime": started.isoformat(timespec="seconds"),
            "prov:endedAtTime": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "prov:wasAssociatedWith": {"@type": ["SoftwareApplication", "prov:SoftwareAgent"], "name": "rca_sal",
                                       "rca:codeFiles": code, "rca:codeVersion": ooi_qc.code_version(root),
                                       "rca:runtime": {"python": platform.python_version(), "pandas": pd.__version__,
                                                       "numpy": np.__version__, "platform": platform.platform()}},
            "prov:used": [_prov_file(sf, gh_infos) for sf in all_sf]},
        "rca:ooinet": ooinet,
        **({"rca:githubSource": github_record(gh_infos, cfg.get("github", {}).get("repo"))} if gh_infos else {}),
        "rca:cruise": cruise_meta({**ycfg, "cast": cast}) | cast_times,
        "rca:parameters": {"global": {k: v for k, v in par.items()}, "variants": variants, "notebook_window_utc": w},
        "rca:inputChecks": {"ctd_header": hdr, "ctd_scans_total": int(len(ctd)), "ctd_scans_downcast_reference": int(len(down)),
                            "winkler_rows": {k: int(len(v)) for k, v in winks.items()}, "winkler_rows_flagged_bad": int(bad.sum()),
                            "dofsta_samples": int(len(d)), "dofsta_o2_units": d.attrs.get("o2_units"),
                            "n_upcasts": int(len(summ)), "dofsta_time_start": d["time"].min(), "dofsta_time_end": d["time"].max()},
        "rca:upcasts": summ.reset_index().to_dict("records"),
        **({"rca:profileIndex": {"n_profiles_in_data": int(len(pindex["summ"]))}} if pindex else {}),
        "rca:variants": rows, "rca:primaryVariant": primary["variant_id"],
        "rca:twoPoint": {
            "T1": {"variant": primary["variant_id"], "ctd_gain": primary["ctd_gain"], "dofsta_gain": primary["dofsta_gain"],
                   "time_mean_profiler_utc": primary["t1_mean_profiler_time_utc"], "time_cast_utc": cast_time,
                   "notebook_convention_variant": reg["variant_id"] if reg else None,
                   "notebook_convention_ctd_gain": reg["ctd_gain"] if reg else None,
                   "notebook_convention_dofsta_gain": reg["dofsta_gain"] if reg else None,
                   "notebook_convention_time_mean_profiler_utc": reg["t1_mean_profiler_time_utc"] if reg else None},
            "T2": {"status": "missing", "reason": ycfg.get("t2_missing_reason",
                   "no second cruise configured for this year; two-point interpolation not computed")}},
        "rca:flags": flags, "rca:outputs": outputs}
    (out / "calibration_record.json").write_text(json.dumps(_clean(record), indent=2, ensure_ascii=False), encoding="utf8")
    return record
