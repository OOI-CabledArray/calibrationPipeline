"""Run the salinity and oxygen pipelines on uploaded files.

Uploaded bytes are saved in a per-session workspace, a JSON config naming them is built from the parameters and variants of the template
year, and rca_sal.pipeline.run_year and rca_sal.pipeline_o2.run_year_oxygen are called.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .pipeline import PROJECT_ROOT, load_config, run_year
from .pipeline_o2 import run_year_oxygen

# role -> (label, allowed suffixes)
SALINITY_ROLES = {
    "profiler_nc": ("Profiler CTDPFA NetCDF (OOINet)", (".nc",)),
    "reference_cnv": ("Shipboard CTD .cnv (with sal00, downcast)", (".cnv",)),
    "reference_btl": ("Shipboard CTD .btl (bottle averages)", (".btl",)),
    "autosal_xlsx": ("Autosal salinity workbook", (".xlsx", ".xlsm")),
    "cast_log_xlsm": ("Discrete cast log (SampleList tab)", (".xlsm", ".xlsx")),
}
OXYGEN_ROLES = {
    "winkler_xls": ("Winkler calculation workbook", (".xls", ".xlsx")),
    "dofsta_nc": ("Profiler DOFSTA NetCDF (OOINet do_fast_sample)", (".nc",)),
}
OXYGEN_NEEDS_FROM_SALINITY = ("reference_cnv", "reference_btl")


class UploadError(ValueError):
    """A problem with what the user supplied (named files, missing inputs, bad values)."""


@dataclass
class RunResult:
    label: str
    outputs_root: Path
    records: dict = field(default_factory=dict)      # "salinity" / "oxygen" -> record
    errors: dict = field(default_factory=dict)       # "salinity" / "oxygen" -> message
    skipped: dict = field(default_factory=dict)      # "salinity" / "oxygen" -> why


def upload_settings(config_path: str | Path) -> dict:
    return load_config(config_path)["upload"]


def purge_old_workspaces(max_age_h: float, base: Path | None = None) -> int:
    """Delete stale upload workspaces left by earlier sessions; returns how many were removed."""
    base = base or Path(tempfile.gettempdir())
    n = 0
    for d in base.glob("rca_upload_*"):
        try:
            if d.is_dir() and time.time() - d.stat().st_mtime > max_age_h * 3600:
                shutil.rmtree(d)
                n += 1
        except OSError:
            pass
    return n


def new_workspace(max_age_h: float, base: Path | None = None) -> Path:
    purge_old_workspaces(max_age_h, base)
    return Path(tempfile.mkdtemp(prefix="rca_upload_", dir=base))


def _safe_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._ +()=-]", "_", Path(name.replace("\\", "/")).name).strip(". ")
    if not base:
        raise UploadError(f"unusable file name {name!r}")
    return base


def save_uploads(ws: Path, files: dict[str, tuple[str, bytes]], max_file_mb: float) -> dict[str, Path]:
    """files: role -> (original file name, bytes). Each role gets its own folder, so equal names cannot collide."""
    roles = {**SALINITY_ROLES, **OXYGEN_ROLES}
    out = {}
    for role, (name, data) in files.items():
        if role not in roles:
            raise UploadError(f"unknown input role {role!r}")
        if not data:
            raise UploadError(f"{roles[role][0]}: the file {name!r} is empty")
        if len(data) > max_file_mb * 1e6:
            raise UploadError(f"{roles[role][0]}: {name!r} is larger than the {max_file_mb:g} MB limit")
        safe = _safe_name(name)
        if not safe.lower().endswith(roles[role][1]):
            raise UploadError(f"{roles[role][0]}: expected a {' / '.join(roles[role][1])} file, got {name!r}")
        d = ws / "inputs" / role
        d.mkdir(parents=True, exist_ok=True)
        (d / safe).write_bytes(data)
        out[role] = d / safe
    return out


def _window(w: tuple[str, str] | None) -> dict | None:
    if not w:
        return None
    return {"start": w[0], "end": w[1]}


def build_config(template: dict, meta: dict, paths: dict[str, Path], windows: dict[str, tuple[str, str] | None]) -> dict:
    """JSON-able config for one uploaded run. Parameters and variants are the template year's."""
    t = template["years"][template["upload"]["template_year"]]
    year = str(int(meta["year"]))
    sal_ok = all(r in paths for r in SALINITY_ROLES)
    ox_ok = all(r in paths for r in (*OXYGEN_ROLES, *OXYGEN_NEEDS_FROM_SALINITY))
    inputs = {r: str(p) for r, p in paths.items() if r in SALINITY_ROLES}
    ycfg = {"cruise": meta.get("cruise") or None, "ship_cruise_id": meta.get("ship_cruise_id") or None,
            "vessel": meta.get("vessel") or None, "cast": meta["cast"], "deployment": meta.get("deployment") or None,
            "inputs": inputs,
            "variants": [v for v in t["variants"] if not v.get("window") or windows.get("salinity")]}
    if windows.get("salinity"):
        ycfg["notebook_window_utc"] = _window(windows["salinity"])
    if ox_ok:
        ycfg["inputs"] = inputs | {k: str(paths[k]) for k in OXYGEN_NEEDS_FROM_SALINITY}
        ycfg["oxygen"] = {"cast": meta["cast"],
                          "inputs": {r: str(paths[r]) for r in OXYGEN_ROLES},
                          "variants": [v for v in t["oxygen"]["variants"] if not v.get("window") or windows.get("oxygen")]}
        if windows.get("oxygen"):
            ycfg["oxygen"]["notebook_window_utc"] = _window(windows["oxygen"])
    return {"parameters": template["parameters"], "years": {year: ycfg}, "_uploaded": {"salinity": sal_ok, "oxygen": ox_ok}}


def label_for(year) -> str:
    return f"{int(year)} (uploaded)"


def run_uploaded(ws: Path, files: dict[str, tuple[str, bytes]], meta: dict, windows: dict | None = None,
                 config_path: str | Path = PROJECT_ROOT / "config" / "cruises.toml") -> RunResult:
    """Save, validate and run. Raises UploadError for unusable input; per-pipeline failures go in RunResult.errors."""
    windows = windows or {}
    settings = upload_settings(config_path)
    for k in ("year", "cast"):
        if not str(meta.get(k, "")).strip():
            raise UploadError(f"{k} is required")
    try:
        year = int(meta["year"])
    except (TypeError, ValueError):
        raise UploadError(f"year must be a whole number, got {meta['year']!r}") from None
    meta = {**meta, "year": year, "cast": str(meta["cast"]).strip()}
    paths = save_uploads(ws, files, settings["max_file_mb"])
    cfg = build_config(load_config(config_path), meta, paths, windows)
    sal_ok, ox_ok = cfg["_uploaded"]["salinity"], cfg["_uploaded"]["oxygen"]
    if not (sal_ok or ox_ok):
        need_s = [SALINITY_ROLES[r][0] for r in SALINITY_ROLES if r not in paths]
        need_o = [(OXYGEN_ROLES | {k: (k, ()) for k in OXYGEN_NEEDS_FROM_SALINITY})[r][0]
                  for r in (*OXYGEN_ROLES, *OXYGEN_NEEDS_FROM_SALINITY) if r not in paths]
        raise UploadError("Nothing to run. Missing for salinity: " + ", ".join(need_s)
                          + ". Missing for oxygen: " + ", ".join(need_o) + ".")
    cfg_path = ws / "config.json"
    cfg_path.write_text(json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, indent=2), encoding="utf8")
    res = RunResult(label_for(year), ws / "outputs")
    for name, ok, fn in (("salinity", sal_ok, run_year), ("oxygen", ox_ok, run_year_oxygen)):
        if not ok:
            res.skipped[name] = "not all of its input files were uploaded"
            continue
        try:
            res.records[name] = fn(year, cfg_path, ws / "outputs", ws)
        except Exception as e:                       # noqa: BLE001 - reported to the user
            res.errors[name] = f"{type(e).__name__}: {e}"
    return res


def zip_outputs(outputs_root: Path, year: int) -> bytes:
    buf = io.BytesIO()
    root = outputs_root / str(year)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(root.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(root).as_posix())
    return buf.getvalue()
