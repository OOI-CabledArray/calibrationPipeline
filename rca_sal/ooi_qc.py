"""OOI human-in-the-loop (HITL) annotations on the profiler samples, and the code version of a run.

Samples inside the interval of an applicable annotation with exclusion_flag set are dropped. Every other annotation that covers the
samples used is reported in the record.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

EXCLUDED = "annotation_excluded"


def _utc(v):
    if v in (None, ""):
        return None
    t = pd.Timestamp(v)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def applies(a: dict, refdes: str | None) -> bool:
    """Does annotation `a` concern the instrument `refdes` (SUBSITE-NODE-PORT-INSTRUMENT)? Without a designator it is assumed to."""
    if not refdes:
        return True
    site, node, sensor = refdes.split("-", 2)
    return all(a.get(k) in (None, v) for k, v in (("subsite", site), ("node", node), ("sensor", sensor)))


def _inside(times, a: dict) -> np.ndarray:
    t = pd.DatetimeIndex(times)
    m = np.ones(len(t), dtype=bool)
    if (s := _utc(a.get("start"))) is not None:
        m &= np.asarray(t >= s)
    if (e := _utc(a.get("stop"))) is not None:
        m &= np.asarray(t <= e)
    return m


def mark_exclusions(df: pd.DataFrame, annotations: list[dict], refdes: str | None) -> pd.DataFrame:
    """Add boolean column `annotation_excluded`: the sample lies inside an applicable annotation that carries exclusion_flag."""
    ex = np.zeros(len(df), dtype=bool)
    for a in annotations or []:
        if a.get("exclusion_flag") and applies(a, refdes):
            ex |= _inside(df["time"], a)
    return df.assign(**{EXCLUDED: ex})


def drop_excluded(sub: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """The samples outside every exclusion annotation, and how many were dropped."""
    ex = sub[EXCLUDED].to_numpy(bool) if EXCLUDED in sub else np.zeros(len(sub), dtype=bool)
    return sub[~ex], int(ex.sum())


def annotations_on_samples(annotations: list[dict], refdes: str | None, times) -> list[dict]:
    """The applicable annotations whose interval contains at least one of the samples used, with how many."""
    out = []
    for a in annotations or []:
        if applies(a, refdes) and (n := int(_inside(times, a).sum())):
            out.append({"id": a.get("id"), "qc_flag": a.get("qc_flag"), "exclusion_flag": bool(a.get("exclusion_flag")),
                        "sensor": a.get("sensor"), "start": a.get("start"), "stop": a.get("stop"), "n_samples": n,
                        "annotation": a.get("annotation")})
    return out


def code_version(root: str | Path) -> dict:
    """Git commit of the code and whether code or config had uncommitted changes when the run was made (None if git is unavailable)."""
    def git(*args):
        try:
            r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None
    status = git("status", "--porcelain", "--", "rca_sal", "scripts", "config")
    return {"git_commit": git("rev-parse", "HEAD"), "uncommitted_changes_in_code_or_config": None if status is None else bool(status)}
