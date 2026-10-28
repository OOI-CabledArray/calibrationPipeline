"""Deployment summary: built from the committed outputs, decisions follow the configured thresholds, page renders."""
import json

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from rca_sal import deployments as D
from rca_sal.pipeline import load_config
from tests.conftest import ROOT

CFG = load_config(ROOT / "config" / "cruises.toml")


@pytest.fixture(scope="module")
def summary():
    return D.summarize(CFG, ROOT / "outputs")


def _all(summary):
    return [(g, d) for g in summary["instruments"] for d in g["deployments"]]


def test_every_configured_run_is_listed_once(summary):
    runs = {(d["run"], g["variable"]) for g, d in _all(summary)}
    assert len(runs) == len(_all(summary))
    expected = {(D.run_key(CFG, y, s), v) for y, s, v in D._runs(CFG)}
    assert runs == expected


def test_calibrated_entries_match_the_records(summary):
    sev = CFG["deployments"]["severity_levels"]
    for g, d in _all(summary):
        if d["calibration"] != "calibrated":
            assert d["status"]["level"] is None
            continue
        folder = ROOT / "outputs" / d["run"] / ("oxygen" if g["variable"] == "oxygen" else "")
        rec = json.loads((folder / "calibration_record.json").read_text(encoding="utf8"))
        tab = pd.read_csv(folder / "calibration_table.csv").set_index("variant_id")
        p = tab.loc[rec["rca:primaryVariant"]]
        assert d["record_id"] == rec["@id"] and d["basis"]["variant"] == rec["rca:primaryVariant"]
        assert g["refdes"] == rec["rca:ooinet"]["reference_designator"]
        assert f"deployment{d['deployment']:04d}_" in json.dumps(rec["prov:wasGeneratedBy"]["prov:used"])
        if g["variable"] == "salinity":
            assert d["flat_offset"]["value"] == pytest.approx(-p["offset_psu"], abs=5e-5)      # S_corrected = S_raw + value
            before, after = p["rmse_before_psu"], p["rmse_after_offset_psu"]
        else:
            before, after = p["rmse_before_umolkg"], p["rmse_after_umolkg"]
        rmse = next(c for c in d["checks"] if c["criterion"] == "rmse_reduction_pct")
        assert rmse["value"] == pytest.approx(100 * (before - after) / before, abs=0.05)
        expected = max([1] + [sev[c["severity"]][c["outcome"]] for c in d["checks"] if c["outcome"] in ("marginal", "fail")])
        assert d["status"]["level"] == expected
        assert d["status"]["label"] == CFG["deployments"]["level_labels"][str(expected)]
        assert (expected == 1) == (not d["status"]["set_by"])


def test_every_check_reports_its_numbers(summary):
    for _, d in _all(summary):
        for c in d.get("checks", []):
            assert c["outcome"] in D.OUTCOMES and c["severity"] in CFG["deployments"]["severity_levels"]
            assert c["summary"]
            if c["value"] is not None:
                assert c["margin_to_target"] is not None and c["margin_to_limit"] is not None
                assert any(ch.isdigit() for ch in c["summary"])
            if c["outcome"] != "n/a":
                assert c["details"]


@pytest.mark.parametrize("value,spec,out", [
    (0.004, {"target": 0.005, "limit": 0.01}, "pass"), (0.007, {"target": 0.005, "limit": 0.01}, "marginal"),
    (0.02, {"target": 0.005, "limit": 0.01}, "fail"), (None, {"target": 0.005, "limit": 0.01}, "fail"),
    (12.0, {"target": 5, "limit": 0, "better": "higher"}, "pass"), (3.0, {"target": 5, "limit": 0, "better": "higher"}, "marginal"),
    (0.0, {"target": 5, "limit": 0, "better": "higher"}, "fail"), (-2.0, {"target": 5, "limit": 0, "better": "higher"}, "fail")])
def test_grade(value, spec, out):
    assert D.grade(value, spec) == out


def test_severity_caps_the_level():
    dcfg = CFG["deployments"]
    mk = lambda sev, out: {"criterion": "x", "severity": sev, "outcome": out, "summary": "s"}  # noqa: E731
    assert D.status([mk("minor", "fail"), mk("minor", "fail")], dcfg)["level"] == dcfg["severity_levels"]["minor"]["fail"]
    assert D.status([mk("minor", "fail"), mk("critical", "fail")], dcfg)["level"] == 4
    assert D.status([mk("major", "n/a"), mk("critical", "pass")], dcfg)["level"] == 1
    s = D.status([mk("minor", "fail"), mk("critical", "marginal")], dcfg)
    assert s["level"] == 3 and len(s["set_by"]) == 1 and len(s["other_issues"]) == 1


def test_every_check_has_machine_readable_booleans(summary):
    for _, d in _all(summary):
        st = d["status"]
        if d["calibrated"] is False:
            assert d["calibration"] in ("not run", "missing input") and st["level"] is None
            continue
        assert st["checks_passed"] == {c["criterion"]: c["passed"] for c in d["checks"]}
        assert st["all_checks_pass"] == all(c["passed"] for c in d["checks"] if c["assessed"])
        assert st["set_by_checks"] == [c["criterion"] for c in d["checks"] if c["sets_status"]]
        assert (st["n_pass"], st["n_marginal"], st["n_fail"], st["n_not_assessed"]) == tuple(
            sum(c["outcome"] == o for c in d["checks"]) for o in ("pass", "marginal", "fail", "n/a"))
        for c in d["checks"]:
            if c["outcome"] == "n/a":
                assert (c["assessed"], c["passed"], c["within_limit"], c["sets_status"]) == (False, None, None, False)
            else:
                assert c["assessed"] and c["passed"] == (c["outcome"] == "pass") and c["within_limit"] == (c["outcome"] != "fail")
                assert not c["passed"] or c["within_limit"]
            assert isinstance(c["target_value"], (int, float)) and isinstance(c["limit_value"], (int, float)) and c["better"] in ("higher", "lower")


def test_thresholds_drive_the_status(summary):
    loose = json.loads(json.dumps(CFG))
    for spec in loose["deployments"]["salinity"].values():
        hi = spec.get("better") == "higher"
        spec.update(target=-1e9 if hi else 1e9, limit=-1e10 if hi else 1e10)
    loose["deployments"]["salinity"]["reference_median_abs_psu"]["min_bottles"] = 0
    s = D.summarize(loose, ROOT / "outputs")
    sal = [d for g in s["instruments"] if g["variable"] == "salinity" for d in g["deployments"] if d["calibration"] == "calibrated"]
    assert sal
    for d in sal:          # with every target relaxed, only a reference with no usable bottle (all flagged) can still fail
        bad = [c for c in d["checks"] if c["outcome"] not in ("pass", "n/a")]
        assert all(c["criterion"] == "reference_median_abs_psu" and c["value"] is None for c in bad)
        assert d["status"]["level"] == (1 if not bad else CFG["deployments"]["severity_levels"]["major"]["fail"])
    strict = json.loads(json.dumps(CFG))
    strict["deployments"]["salinity"]["rmse_reduction_pct"].update(target=1e9, limit=1e9)
    s = D.summarize(strict, ROOT / "outputs")
    assert all(d["status"]["level"] == 4 for g in s["instruments"] if g["variable"] == "salinity"
               for d in g["deployments"] if d["calibration"] == "calibrated")


def test_not_run_years_say_why_and_are_not_graded(summary):
    nr = [d for _, d in _all(summary) if d["calibration"] == "not run"]
    assert all(d["reason"] and d["status"]["level"] is None and d["status"]["reason"] == d["reason"] for d in nr)


@pytest.mark.parametrize("v,out", [(None, "null"), (True, "true"), (0.37, "0.37"), (-3e-05, "-3.0e-05"), (1.5e-07, "1.5e-07"), (1e+16, "1.0e+16"), ("NA", '"NA"'), ("a: b", '"a: b"'),
                                   ("S_corrected = S_raw + 0.0326", "S_corrected = S_raw + 0.0326"), ("-x", '"-x"'), (">= 5.0", '">= 5.0"'), ("5.0", '"5.0"'), ("Yes", '"Yes"')])
def test_yaml_scalars(v, out):
    assert D._scalar(v) == out


def test_yaml_nesting():
    y = D.to_yaml({"refdes": "X", "deployments": {1: {"flat_offset": 0.37, "correctable": True}, 2: {"flat_offset": None}}, "l": [1, {"a": 2}]})
    assert y.splitlines() == ["refdes: X", "deployments:", "  1:", "    flat_offset: 0.37", "    correctable: true", "  2:",
                              "    flat_offset: null", "l:", "  - 1", "  - a: 2"]


def test_yaml_file_parses_back_to_the_summary(summary):
    yaml = pytest.importorskip("yaml")
    assert json.loads(json.dumps(yaml.safe_load(D.to_yaml(summary)))) == json.loads(json.dumps(summary))   # JSON stringifies the int keys


def _tables(g, d):
    folder = ROOT / "outputs" / d["run"] / ("oxygen" if g["variable"] == "oxygen" else "")
    return pd.read_csv(folder / "calibration_table.csv").set_index("variant_id")


def test_the_reference_is_not_extrapolated_except_in_notebook_reproductions(summary):
    for g, d in _all(summary):
        if d["calibration"] != "calibrated":
            continue
        tab = _tables(g, d)
        assert d["basis"]["reference_extrapolated"] is False
        assert not tab[tab.role != "regression"]["reference_extrapolated"].any()
        assert tab[tab.role == "regression"]["reference_extrapolated"].all() if (tab.role == "regression").any() else True


def test_annotations_are_listed_with_the_samples_they_cover(summary):
    for g, d in _all(summary):
        if d["calibration"] != "calibrated":
            continue
        a = d["annotations"]
        assert a["n_on_samples"] == len(a["on_samples"]) and a["has_annotations_on_samples"] == bool(a["on_samples"])
        assert a["n_in_request"] >= a["n_on_samples"] if g["variable"] == "oxygen" else True
        for x in a["on_samples"]:
            assert x["n_samples"] > 0 and x["id"] is not None


def test_oxygen_status_grades_the_size_of_the_gain_and_the_bottles(summary):
    for g, d in _all(summary):
        if d["calibration"] != "calibrated" or g["variable"] != "oxygen":
            continue
        c = {x["criterion"]: x for x in d["checks"]}
        gain = d["function_offset"]["gain"]
        assert c["gain_departure_from_one"]["value"] == pytest.approx(abs(gain - 1), abs=1e-5)
        assert (c["gain_departure_from_one"]["outcome"] == "pass") == (abs(gain - 1) <= CFG["deployments"]["oxygen"]["gain_departure_from_one"]["target"])
        assert c["winkler_bottles_used"]["value"] == d["basis"]["n_winkler"] <= d["basis"]["n_winkler_available"]


def test_page_renders_and_lists_every_instrument(summary):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at.switch_page("app_pages/deployments.py").run()
    assert not at.exception
    sal = [g["refdes"] for g in summary["instruments"] if g["variable"] == "salinity"]
    assert all(any(r in s.value for s in at.subheader) for r in sal)


def test_run_selector_names_the_site_and_the_instrument_of_every_run():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    sel = at.sidebar.selectbox[0]
    runs = [r for r in (ROOT / "outputs").iterdir() if r.is_dir() and r.name[:4].isdigit()]
    assert len(sel.options) == len(runs) == 18
    by_name = {t["short_name"]: t for t in CFG["sites"].values() if isinstance(t, dict)}
    for label in sel.options:
        year, name, refdes = label.split(" · ")
        assert year.isdigit() and by_name[name]["profiler_reference_designator"] == refdes, label
    first_2025 = [o for o in sel.options if o.startswith("2025 ·")]
    assert first_2025[0].endswith(CFG["sites"][CFG["sites"]["default"]]["profiler_reference_designator"])      # the default site comes first
    assert any(CFG["sites"]["RS01SBPS"]["short_name"] in o for o in first_2025) and any(CFG["sites"]["CE04OSPS"]["short_name"] in o for o in first_2025)
    assert not any(o.isdigit() for o in sel.options)                                                          # no bare year any more
    assert any("DOFSTA" in c.value for c in at.sidebar.caption) and any("CTDPFA" in c.value for c in at.sidebar.caption)


def test_deployments_page_shows_the_booleans_in_its_yaml_and_checks_table():
    yaml = pytest.importorskip("yaml")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at.switch_page("app_pages/deployments.py").run()
    assert not at.exception
    docs = [yaml.safe_load(c.value) for c in at.code if c.language == "yaml"]
    assert len(docs) == 3                                           # one compact summary per salinity instrument
    n_checks = 0
    for doc in docs:
        for dep in doc["deployments"].values():
            assert isinstance(dep["calibrated"], bool) and isinstance(dep["status"]["level"], int)
            assert isinstance(dep["status"]["all_checks_pass"], bool)
            assert isinstance(dep["annotations"]["has_annotations_on_samples"], bool) and isinstance(dep["reference_extrapolated"], bool)
            for name, c in dep["checks"].items():
                assert isinstance(c["assessed"], bool) and isinstance(c["sets_status"], bool), name
                if c["assessed"]:
                    assert isinstance(c["passed"], bool) and isinstance(c["within_limit"], bool) and (c["outcome"] == "pass") == c["passed"]
                else:
                    assert c["passed"] is None and c["within_limit"] is None and c["outcome"] == "n/a"
                n_checks += 1
            assert dep["status"]["set_by_checks"] == [n for n, c in dep["checks"].items() if c["sets_status"]]
    assert n_checks >= 18 * 6
    assert any({"passed", "within limit", "sets status"} <= set(df.value.columns) for df in at.dataframe)
    assert any("all checks pass" in df.value.columns for df in at.dataframe)


def test_page_titles_name_the_site_and_the_instrument():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.session_state["year"] = "2022-RS01SBPS"
    at.run()
    for page, needle in (("bottles", "Slope Base"), ("profiler", "RS01SBPS-SF01A-2A-CTDPFA102"), ("oxygen_winkler", "Slope Base"),
                         ("oxygen_dofsta", "RS01SBPS-SF01A-2A-DOFSTA102"), ("provenance", "Slope Base")):
        at.switch_page(f"app_pages/{page}.py").run()
        assert not at.exception and needle in at.title[0].value and "2022" in at.title[0].value, (page, at.title[0].value)
