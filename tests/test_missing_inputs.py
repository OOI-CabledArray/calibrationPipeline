import importlib.util
import json

import pytest

from rca_sal import lineage
from rca_sal.pipeline import MissingInputError, run_year
from tests.conftest import ROOT


def test_source_file_missing_is_explicit(tmp_path):
    with pytest.raises(FileNotFoundError, match="reference_ctd_cnv"):
        lineage.source_file(tmp_path / "nope.cnv", "reference_ctd_cnv")


def _cfg_with_broken_path(tmp_path):
    txt = (ROOT / "config" / "cruises.toml").read_text(encoding="utf8")
    key = 'reference_cnv = "'
    i = txt.index(key) + len(key)
    j = txt.index('"', i)
    bad = txt[:i] + str(tmp_path / "AT50-41_CTD-007_nowhere.cnv").replace("\\", "/") + txt[j:]
    p = tmp_path / "cruises.toml"
    p.write_text(bad, encoding="utf8")
    return p


def test_run_year_stops_and_names_the_missing_file(tmp_path):
    cfg = _cfg_with_broken_path(tmp_path)
    with pytest.raises(MissingInputError) as e:
        run_year(2025, cfg, tmp_path / "out")
    assert e.value.missing[0]["role"] == "reference_cnv"
    assert "nowhere" in e.value.missing[0]["path"]
    assert not (tmp_path / "out" / "2025" / "calibration_table.csv").exists()


def test_script_writes_missing_json_and_exits_2(tmp_path):
    spec = importlib.util.spec_from_file_location("run_year_script", ROOT / "scripts" / "run_year.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = _cfg_with_broken_path(tmp_path)
    assert mod.main(["--year", "2025", "--config", str(cfg), "--out", str(tmp_path / "out")]) == 2
    m = json.loads((tmp_path / "out" / "2025" / "missing.json").read_text())
    assert m["status"] == "missing" and m["missing"][0]["role"] == "reference_cnv"


def test_year_without_config_is_an_error(tmp_path):
    with pytest.raises(KeyError):
        run_year(1999, ROOT / "config" / "cruises.toml", tmp_path)
