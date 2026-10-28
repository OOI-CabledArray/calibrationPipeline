"""Records and exports name inputs by their GitHub URL or their path inside the repository; nothing identifies the machine that made them."""
import json
import re
from pathlib import Path

import pytest

from tests.conftest import ROOT

LOCAL = re.compile(r"[A-Za-z]:\\|[A-Za-z]:/(?!/)|/Users/|/home/")      # a drive path, or a home folder ('https://' has two slashes)
RAW = "https://raw.githubusercontent.com/"


def _strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


def _records():
    return sorted(ROOT.glob("outputs/*/calibration_record.json")) + sorted(ROOT.glob("outputs/*/oxygen/calibration_record.json"))


def test_the_detector_finds_local_paths_and_accepts_urls():
    for bad in ("C:" + chr(92) + "Users" + chr(92) + "x" + chr(92) + "f.nc", "D:/data/f.nc", "/Users/a/f.nc", "/home/a/f.nc"):
        assert LOCAL.search(bad), bad
    for ok in ("https://raw.githubusercontent.com/OOI-CabledArray/discreteSummaries/abc/f.csv", "data/raw/ooinet/2026/f.nc", "rca_sal/pipeline.py"):
        assert not LOCAL.search(ok), ok


def test_there_are_records_to_check():
    assert len(_records()) == 36


@pytest.mark.parametrize("f", _records(), ids=lambda f: "/".join(f.parts[-3:-1] if f.parent.name == "oxygen" else f.parts[-2:-1]))
def test_record_has_no_local_path(f):
    rec = json.loads(f.read_text(encoding="utf8"))
    bad = [s[:90] for s in _strings(rec) if LOCAL.search(s)]
    assert not bad, bad
    run = rec["prov:wasGeneratedBy"]
    for u in run["prov:used"]:
        url = u["contentUrl"]
        assert url.startswith(RAW) or (not Path(url).is_absolute() and ".." not in url and "\\" not in url), url
    for c in run["prov:wasAssociatedWith"]["rca:codeFiles"]:
        assert c["path"].startswith(("rca_sal/", "scripts/", "config/")) and "\\" not in c["path"], c["path"]


def test_deployment_summary_has_no_local_path():
    for name in ("deployments.yaml", "deployments.json"):
        text = (ROOT / "outputs" / name).read_text(encoding="utf8")
        assert not LOCAL.search(text.replace("\\\\", "\\")), name


def test_github_inputs_are_named_by_url_and_repository_files_by_path():
    rec = json.loads((ROOT / "outputs" / "2025" / "calibration_record.json").read_text(encoding="utf8"))
    used = {u["rca:role"]: u["contentUrl"] for u in rec["prov:wasGeneratedBy"]["prov:used"]}
    assert used["autosal_xlsx"].startswith(RAW) and used["profile_index_csv"].startswith(RAW)
    assert used["reference_cnv"].startswith(RAW) and used["discrete_summary_csv"].startswith(RAW)
    assert used["profiler_nc"].startswith("data/raw/ooinet/") and used["ooinet_status"].startswith("data/raw/ooinet/")
