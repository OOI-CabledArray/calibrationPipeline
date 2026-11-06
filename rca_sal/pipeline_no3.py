"""Nitrate validation of one run: outputs/<run>/nitrate/ (rca_sal.nitrate). Input handling is the same as in rca_sal.pipeline.

Variants:
  nearest_profile_temperature  (primary)   SUNA profile closest in time to the cast, bottles matched by temperature
  nearest_profile_depth        (depth)     the same profile, bottles matched by depth
  profile_<n>_temperature      (per_profile) every SUNA profile in the request, matched by temperature

The cast is `cast` of the run's nitrate table, else the run's reference cast; its time is the midpoint of its first and last bottle
closure in the discrete summary. When the temperature matchup finds no bottle in range, the depth matchup becomes the primary (flagged).

The nearest profile is taken from the run's deployment (`deployment` of the year) when the request also holds the outgoing instrument;
that instrument's profiles stay in the per-profile comparisons with their deployment number (the recovery matchup of guide 3.5.3).
"""
from __future__ import annotations

import json
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import nitrate as no3
from . import ooi_qc
from .lineage import sha256_file
from .pipeline import (CONTEXT, PROJECT_ROOT, MissingInputError, _clean, _code_fingerprint, _prov_file, _resolve, annotate_github,
                       cruise_meta, fingerprint_inputs, github_record, load_config, ooinet_block, require_roles, resolve_github_inputs,
                       run_key, site_year)

NITRATE_REQUIRED = ("nutnr_nc", "discrete_summary_csv")


def _variant(vid: str, role: str, match_by: str, pid: int, d: pd.DataFrame, prof: pd.DataFrame, bottles: pd.DataFrame, par: dict,
             refdes: str, annotations, n_spikes: dict) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    p = d[d["profile_id"] == pid]
    binned = no3.bin_profile(p, par["bin_size_m"])
    col = "temperature" if match_by == "temperature" else "depth"
    bcol = "temperature" if match_by == "temperature" else "depth_m"
    use = bottles[bottles["usable"]]
    info = prof.loc[pid]
    if info["kind"] == "full":
        sensor = no3.sensor_at(binned, col, use[bcol])
    else:                                        # parked: one point, the bottle profile interpolated at the record's median
        x = float(p[col].median())
        use = pd.DataFrame([{"niskin": f"interpolated at {col} {x:.2f}", "depth_m": float(p["depth"].median()),
                             "temperature": float(p["temperature"].median()), "nitrate_bottle": no3.bottle_at(use, bcol, x),
                             "nitrite_bottle": np.nan, "status": "interpolated", "usable": True}])
        sensor = np.array([float(p["nitrate"].median())])
    bad = no3.outliers(use["nitrate_bottle"], sensor, par["bottle_outlier_um"])
    stats = no3.matchup_stats(use["nitrate_bottle"], sensor, par["accuracy_um"], bad)
    on = ooi_qc.annotations_on_samples(annotations, refdes, p["time"])
    row = {"variant_id": vid, "role": role, "match_by": match_by, "profile_id": int(pid), "deployment": int(info["deployment"]),
           "profile_kind": info["kind"], "depth_span_m": float(info["depth_max_m"] - info["depth_min_m"]),
           "profile_mean_time_utc": info["mean_time_utc"].isoformat(), "hours_from_cast": float(info["hours_from_cast"]),
           "n_sensor_samples": int(len(p)), "n_spikes_removed": int(n_spikes.get(pid, 0)), "n_negative": int((p["nitrate"] < 0).sum()),
           "n_bins": int(len(binned)), "depth_min_m": float(info["depth_min_m"]), "depth_max_m": float(info["depth_max_m"]),
           "temperature_min": float(info["temperature_min"]), "temperature_max": float(info["temperature_max"]),
           "n_bottles_usable": int(bottles["usable"].sum()), "annotations_on_samples": json.dumps(on) if on else None, **stats}
    m = use.assign(variant_id=vid, sensor_nitrate=sensor, diff_um=use["nitrate_bottle"] - sensor, outlier=bad)
    m["sensor_corrected"] = m["sensor_nitrate"] + stats.get("offset_um", np.nan)
    return row, m, binned.assign(variant_id=vid, profile_id=int(pid))


def run_year_nitrate(year: int, config_path: str | Path, out_root: str | Path, root: Path = PROJECT_ROOT, site: str | None = None) -> dict:
    started = datetime.now(timezone.utc)
    cfg = load_config(config_path)
    ycfg = site_year(cfg, year, site)
    if "nitrate" not in ycfg:
        raise KeyError(f"no nitrate section for {run_key(cfg, year, site)} in {config_path}")
    ncfg, par = ycfg["nitrate"], cfg["nitrate"]
    out = Path(out_root) / run_key(cfg, year, site) / "nitrate"
    out.mkdir(parents=True, exist_ok=True)
    if (stale := out / "missing.json").exists():
        stale.unlink()
    shared = ("discrete_summary_csv",)
    base = site_year(cfg, year, None)["inputs"]
    inputs = {k: ycfg["inputs"].get(k, base.get(k)) for k in shared if ycfg["inputs"].get(k, base.get(k))} | ncfg["inputs"]
    require_roles(inputs, NITRATE_REQUIRED)
    inputs, gh_infos = resolve_github_inputs(cfg, root, inputs)
    files = annotate_github(fingerprint_inputs({"inputs": inputs}, root), gh_infos)
    P = lambda role: _resolve(inputs[role], root)  # noqa: E731
    flags: list[dict] = []

    cast = ncfg.get("cast") or ycfg["cast"]
    bottles = no3.read_discrete_nitrate(P("discrete_summary_csv"), cast)
    cast_start, cast_end = bottles["time_utc"].min(), bottles["time_utc"].max()
    cast_time = cast_start + (cast_end - cast_start) / 2
    if ncfg.get("cast"):
        flags.append({"severity": "info", "code": "nitrate_cast",
                      "text": f"Discrete nitrate from cast {cast}, not the salinity reference cast {ycfg['cast']}: {ncfg.get('cast_reason', '')}".rstrip(": ")})
    if not bottles["usable"].any():
        raise MissingInputError([{"role": "discrete nitrate", "path": f"no usable discrete nitrate on cast {cast} in the discrete summary"}])
    if (bottles["status"] == "unflagged").any():
        flags.append({"severity": "warning", "code": "nitrate_flag_missing",
                      "text": f"{int((bottles['status'] == 'unflagged').sum())} discrete nitrate value(s) of cast {cast} have no nutrient flag in the "
                              "discrete summary; they are used as unflagged."})

    st = cfg["sites"][ycfg.get("site") or cfg["sites"]["default"]]
    refdes = ncfg.get("reference_designator") or st["nitrate_reference_designator"]
    nc = inputs["nutnr_nc"]
    d = no3.read_nutnr_nc([_resolve(x, root) for x in nc] if isinstance(nc, list) else P("nutnr_nc"))
    if d.attrs.get("nitrate_units") != "umol L-1":
        raise ValueError(f"unexpected nitrate units {d.attrs.get('nitrate_units')!r}")
    annotations = json.loads(P("ooinet_annotations").read_text(encoding="utf8")).get("annotations", []) if "ooinet_annotations" in inputs else []
    d, n_excl = ooi_qc.drop_excluded(ooi_qc.mark_exclusions(d, annotations, refdes))
    d = no3.label_profiles(d, par["profile_gap_min"], par["min_profile_samples"])
    spikes = no3.spike_mask(d["nitrate"], d["profile_id"], par["spike_window"], par["spike_threshold_um"]) & (d["profile_id"] > 0).to_numpy()
    n_spikes = d.loc[spikes, "profile_id"].value_counts().to_dict()
    d = d[~spikes]
    prof = no3.profile_summary(d, cast_time)
    if prof.empty:
        raise ValueError("no SUNA profile in the nitrate file")
    prof["kind"] = np.where(prof["depth_max_m"] - prof["depth_min_m"] >= par["min_profile_span_m"], "full", "parked")
    own = prof[prof["deployment"] == int(ycfg["deployment"])] if ycfg.get("deployment") is not None else prof
    if own.empty:
        own = prof
        flags.append({"severity": "warning", "code": "nitrate_deployment",
                      "text": f"No SUNA profile of deployment {ycfg.get('deployment')} in the request; the nearest profile of any deployment is used."})
    full = own[own["kind"] == "full"]
    nearest = int((full if len(full) else own)["hours_from_cast"].abs().idxmin())
    if not len(full):
        z = prof.loc[nearest]
        flags.append({"severity": "warning", "code": "nitrate_parked",
                      "text": f"No full SUNA profile (span >= {par['min_profile_span_m']} m) in the request: the profiler was parked. The primary "
                              f"comparison is one point, the parked record at {z['depth_min_m']:.0f}-{z['depth_max_m']:.0f} m against the bottle "
                              "profile interpolated at its temperature (or depth)."})
    if prof["deployment"].nunique() > 1:
        flags.append({"severity": "info", "code": "nitrate_two_deployments",
                      "text": "The request spans an instrument exchange (deployments " + ", ".join(str(int(x)) for x in sorted(prof["deployment"].unique()))
                              + f"). The primary comparison uses deployment {int(prof.loc[nearest, 'deployment'])}; the other deployment's profiles are "
                              "single-profile comparisons."})
    if n_excl:
        flags.append({"severity": "caveat", "code": "ooinet_annotation", "text": f"{n_excl} SUNA sample(s) inside OOI exclusion annotations were dropped."})
    if (d["nitrate"] < 0).any():
        flags.append({"severity": "caveat", "code": "nitrate_negative",
                      "text": f"{int((d['nitrate'] < 0).sum())} SUNA sample(s) are negative; kept (guide 3.5.2: evaluate, do not assume)."})
    flags.append({"severity": "caveat", "code": "nitrate_l2",
                  "text": "Sensor values are OOI L2 salinity_corrected_nitrate (TCSS), computed by OOI with the profiler's own, uncalibrated CTD; "
                          "temperature matchups use that CTD temperature and the cast's CTD temperature 1."})

    specs = [("nearest_profile_temperature", "primary", "temperature", nearest), ("nearest_profile_depth", "depth_matchup", "depth", nearest)]
    specs += [(f"profile_{pid}_temperature", "per_profile", "temperature", pid) for pid in prof.index]
    rows, mats, bins = [], [], []
    for vid, role, by, pid in specs:
        r, m, b = _variant(vid, role, by, pid, d, prof, bottles, par, refdes, annotations, n_spikes)
        m = m.assign(deployment=r["deployment"])
        r["mean_profiler_minus_cast_h"] = r["hours_from_cast"]
        r["n_annotation_excluded"] = n_excl
        rows.append(r)
        mats.append(m)
        bins.append(b)
    if rows[0].get("n_matchups", 0) == 0 and rows[1].get("n_matchups", 0) > 0:
        rows[0]["role"], rows[1]["role"] = "temperature_matchup", "primary"
        rows[0], rows[1] = rows[1], rows[0]
        mats[0], mats[1], bins[0], bins[1] = mats[1], mats[0], bins[1], bins[0]
        flags.append({"severity": "warning", "code": "nitrate_depth_primary",
                      "text": "No bottle temperature falls inside the SUNA record's temperature range, so the primary comparison is by depth."})
        z = d[d["profile_id"] == rows[0]["profile_id"]]["temperature"]
        if z.nunique() == 1:
            flags.append({"severity": "warning", "code": "nitrate_ctd_constant",
                          "text": f"The CTD temperature merged into the SUNA record is constant ({z.iloc[0]:.4f} C) over {len(z)} samples."})
    table = pd.DataFrame(rows)
    primary = rows[0]
    pm = mats[0]
    if pm["outlier"].any():
        flags.append({"severity": "warning", "code": "nitrate_bottle_outlier",
                      "text": "Discrete nitrate left out as outlier(s) (|difference - median difference| > "
                              f"{par['bottle_outlier_um']} umol/L): " + ", ".join(f"Niskin {r.niskin} at {r.depth_m:.0f} m, bottle {r.nitrate_bottle:.2f}, "
                              f"sensor {r.sensor_nitrate:.2f}" for r in pm[pm["outlier"]].itertuples()) + "."})
    if primary.get("n_matchups", 0) == 0:
        flags.append({"severity": "warning", "code": "nitrate_no_matchups",
                      "text": "No bottle temperature falls inside the temperature range of the nearest SUNA profile."})

    all_sf = [sf for lst in files.values() for sf in lst]
    code = _code_fingerprint(config_path)
    record_id = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps({"year": year, "site": ycfg.get("site"), "var": "nitrate",
                                                           "inputs": sorted(sf.sha256 for sf in all_sf), "code": sorted(c["sha256"] for c in code)}))
    ooinet = ooinet_block({**ncfg, "deployment": ycfg.get("deployment"), "inputs": inputs,
                           "profiler_reference_designator": refdes, "profiler_stream": ncfg.get("stream") or st["nitrate_stream"]}, P)
    table = table.assign(record_id=str(record_id), year=year, nutnr_nc_sha256=";".join(f.sha256 for f in files["nutnr_nc"]),
                         discrete_summary_csv_sha256=files["discrete_summary_csv"][0].sha256)
    table.to_csv(out / "calibration_table.csv", index=False)
    pd.concat(mats, ignore_index=True).assign(record_id=str(record_id)).to_csv(out / "bottle_matchups.csv", index=False)
    pd.concat(bins, ignore_index=True).assign(record_id=str(record_id)).to_csv(out / "sensor_profiles.csv", index=False)
    d.drop(columns=[c for c in (ooi_qc.EXCLUDED,) if c in d]).assign(record_id=str(record_id)).to_csv(out / "sensor_samples.csv", index=False)
    prof.reset_index().to_csv(out / "profiles.csv", index=False)
    bottles.to_csv(out / "bottles.csv", index=False)
    outputs = [{"name": f.name, "sha256": sha256_file(f), "contentSize": f.stat().st_size}
               for f in sorted(out.glob("*")) if f.is_file() and f.name != "calibration_record.json"]

    record = {
        "@context": CONTEXT, "@id": f"urn:uuid:{record_id}", "@type": ["Dataset", "prov:Entity"],
        "name": f"RCA SUNA nitrate validation, {ycfg.get('cruise')} {ycfg.get('ship_cruise_id') or ''} ({year}), {st.get('name')}".replace("  ", " "),
        "dateCreated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "variableMeasured": "salinity_corrected_nitrate", "rca:correctionKind": "additive", "rca:site": ycfg.get("site"),
        "rca:method": par["method"],
        "prov:wasGeneratedBy": {
            "@type": "prov:Activity", "prov:startedAtTime": started.isoformat(timespec="seconds"),
            "prov:endedAtTime": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "prov:wasAssociatedWith": {"@type": ["SoftwareApplication", "prov:SoftwareAgent"], "name": "rca_sal",
                                       "rca:codeFiles": code, "rca:codeVersion": ooi_qc.code_version(root),
                                       "rca:runtime": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__}},
            "prov:used": [_prov_file(sf, gh_infos) for sf in all_sf]},
        "rca:ooinet": ooinet,
        **({"rca:githubSource": github_record(gh_infos, cfg.get("github", {}).get("repo"))} if gh_infos else {}),
        "rca:cruise": cruise_meta({**ycfg, "cast": cast}) | {"first_bottle_closure_utc": cast_start, "last_bottle_closure_utc": cast_end,
                                                          "cast_time_utc": cast_time,
                                                          "definition": "cast_time = midpoint of the cast's first and last bottle closure"},
        "rca:parameters": {"nitrate": par},
        "rca:inputChecks": {"sensor_samples_after_cleaning": int(len(d)), "spikes_removed": int(spikes.sum()), "annotation_excluded": int(n_excl),
                            "n_profiles": int(len(prof)), "bottles": int(len(bottles)), "bottles_usable": int(bottles["usable"].sum()),
                            "nitrate_units": "umol L-1"},
        "rca:profiles": prof.reset_index().to_dict("records"),
        "rca:variants": rows, "rca:primaryVariant": primary["variant_id"],
        "rca:twoPoint": {"T1": {"variant": primary["variant_id"], "offset_um": primary.get("offset_um"), "time_cast_utc": cast_time},
                         "T2": {"status": "missing", "reason": "drift needs a matchup at recovery of the same deployment (guide 3.5.3); not computed"}},
        "rca:flags": flags, "rca:outputs": outputs}
    (out / "calibration_record.json").write_text(json.dumps(_clean(record), indent=2, ensure_ascii=False, default=str), encoding="utf8")
    return record
