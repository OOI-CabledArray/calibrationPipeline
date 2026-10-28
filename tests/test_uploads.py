"""Uploaded files go through the same pipelines: module-level and through the Streamlit page."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from rca_sal import uploads as U
from tests.conftest import ROOT, Y25, resolve_spec

CFG = ROOT / "config" / "cruises.toml"
O = Y25["oxygen"]


_p = resolve_spec


ALL_PATHS = {**{r: _p(Y25["inputs"][r]) for r in U.SALINITY_ROLES}, **{r: _p(O["inputs"][r]) for r in U.OXYGEN_ROLES}}
have_files = all(p.is_file() for p in ALL_PATHS.values())
need_files = pytest.mark.skipif(not have_files, reason="cruise input files not reachable")
META = {"year": 2025, "cast": Y25["cast"], "cruise": Y25["cruise"], "ship_cruise_id": Y25["ship_cruise_id"]}


def _read(roles):
    return {r: (ALL_PATHS[r].name, ALL_PATHS[r].read_bytes()) for r in roles}


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "ws"


@need_files
def test_uploaded_run_reproduces_repository_numbers(ws):
    res = U.run_uploaded(ws, _read(ALL_PATHS), META, config_path=CFG)
    assert not res.errors and not res.skipped and set(res.records) == {"salinity", "oxygen"}
    repo_s = json.loads((ROOT / "outputs/2025/calibration_record.json").read_text(encoding="utf8"))
    repo_o = json.loads((ROOT / "outputs/2025/oxygen/calibration_record.json").read_text(encoding="utf8"))
    pick = lambda rec, v: next(x for x in rec["rca:variants"] if x["variant_id"] == v)  # noqa: E731
    assert pick(res.records["salinity"], "nearest_upcast_depSM")["offset_psu"] == pytest.approx(pick(repo_s, "nearest_upcast_depSM")["offset_psu"], abs=1e-12)
    # an upload has only the Winkler workbook, so its default bottles are the workbook's: same as the repository's workbook variant
    assert pick(res.records["oxygen"], "nearest_upcast_depSM")["dofsta_gain"] == pytest.approx(pick(repo_o, "nearest_upcast_depSM_workbook")["dofsta_gain"], abs=1e-12)
    prim = {k: pick(res.records[k], res.records[k]["rca:primaryVariant"]) for k in res.records}
    assert all(p["selection"] == "first_upcasts" and p["n_upcasts"] == 6 for p in prim.values())   # same rule without a profile index
    # no notebook window given: the variants that reproduce the notebook are left out
    assert "notebook_window_ascending_prDM" not in {v["variant_id"] for v in res.records["salinity"]["rca:variants"]}


@need_files
def test_uploaded_record_has_provenance_of_the_uploaded_bytes(ws):
    from rca_sal.lineage import sha256_file
    res = U.run_uploaded(ws, _read(U.SALINITY_ROLES), META, config_path=CFG)
    assert set(res.records) == {"salinity"} and "oxygen" in res.skipped
    rec = res.records["salinity"]
    used = {u["rca:role"]: u for u in rec["prov:wasGeneratedBy"]["prov:used"]}
    for role in U.SALINITY_ROLES:
        assert used[role]["sha256"] == sha256_file(ALL_PATHS[role])        # same bytes as the original file
        assert used[role]["contentUrl"].startswith(f"uploaded/{role}/")     # named as an upload
    assert rec["rca:ooinet"]["request_id"] == "uploaded"
    cfg_entry = next(c for c in rec["prov:wasGeneratedBy"]["prov:wasAssociatedWith"]["rca:codeFiles"] if c["role"] == "config")
    assert cfg_entry["path"].endswith("config.json") and cfg_entry["sha256"] == sha256_file(ws / "config.json")


@need_files
def test_notebook_window_enables_exact_variant(ws):
    w = Y25["notebook_window_utc"]
    res = U.run_uploaded(ws, _read(U.SALINITY_ROLES), META, {"salinity": (w["start"], w["end"])}, CFG)
    v = {x["variant_id"]: x for x in res.records["salinity"]["rca:variants"]}["notebook_window_ascending_prDM"]
    assert round(v["offset_psu"], 3) == -0.022


def test_missing_files_are_named_and_nothing_runs(ws):
    with pytest.raises(U.UploadError, match="Missing for salinity"):
        U.run_uploaded(ws, {"reference_cnv": ("a.cnv", b"x")}, META, config_path=CFG)
    assert not (ws / "outputs").exists()


@pytest.mark.parametrize("files,msg", [
    ({"reference_cnv": ("a.txt", b"x")}, "expected a .cnv"),
    ({"reference_cnv": ("a.cnv", b"")}, "is empty"),
    ({"nonsense": ("a.cnv", b"x")}, "unknown input role"),
])
def test_bad_uploads_are_rejected(ws, files, msg):
    with pytest.raises(U.UploadError, match=msg):
        U.run_uploaded(ws, files, META, config_path=CFG)


def test_oversized_upload_is_rejected(ws, monkeypatch):
    big = b"x" * 3_000_000
    monkeypatch.setattr(U, "upload_settings", lambda _: {"max_file_mb": 1, "workspace_max_age_h": 6})
    with pytest.raises(U.UploadError, match="larger than"):
        U.run_uploaded(ws, {"reference_cnv": ("a.cnv", big)}, META, config_path=CFG)


def test_file_names_cannot_escape_the_workspace(ws):
    paths = U.save_uploads(ws, {"reference_cnv": ("../../evil/..\\x.cnv", b"data")}, 10)
    assert paths["reference_cnv"].resolve().is_relative_to(ws.resolve())
    assert paths["reference_cnv"].name == "x.cnv"


def test_garbage_file_fails_in_the_pipeline_with_a_message_not_a_crash(ws):
    files = {r: (f"f{U.SALINITY_ROLES[r][1][0]}", b"not a real file") for r in U.SALINITY_ROLES}
    res = U.run_uploaded(ws, files, META, config_path=CFG)
    assert "salinity" in res.errors and not res.records


def test_year_and_cast_are_required(ws):
    with pytest.raises(U.UploadError, match="year"):
        U.run_uploaded(ws, {}, {"year": "", "cast": "CTD-1"}, config_path=CFG)
    with pytest.raises(U.UploadError, match="whole number"):
        U.run_uploaded(ws, {}, {"year": "abc", "cast": "CTD-1"}, config_path=CFG)


def test_stale_workspaces_are_purged(tmp_path):
    old = tmp_path / "rca_upload_old"
    old.mkdir()
    import os
    os.utime(old, (1, 1))
    new = U.new_workspace(6, tmp_path)
    assert not old.exists() and new.exists()


# ------------------------------------------------------------------ the Streamlit page
def _page():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180).run()
    return at.switch_page("app_pages/upload.py").run()


def test_upload_page_renders():
    at = _page()
    assert not at.exception and [b.label for b in at.button] == ["Run pipelines"]


def test_submitting_with_nothing_uploaded_says_what_is_missing():
    at = _page()
    at.number_input[0].set_value(2025)
    at.text_input[0].set_value("CTD-007")
    at.button[0].click().run()
    assert not at.exception
    assert any("Nothing to run" in e.value for e in at.error)


@need_files
def test_full_upload_through_the_page_selects_and_shows_the_new_run():
    at = _page()
    at.number_input[0].set_value(2025)
    at.text_input[0].set_value(Y25["cast"])
    for fu in at.file_uploader:
        role = fu.key.removeprefix("up_")
        fu.upload(ALL_PATHS[role].name, ALL_PATHS[role].read_bytes())
    at.button[0].click().run()
    assert not at.exception and not at.error
    assert at.sidebar.selectbox[0].value == "2025 (uploaded)"
    assert "2025 (uploaded)" in at.sidebar.selectbox[0].options
    # the viewer pages now show the uploaded run
    at.switch_page("app_pages/profiler.py").run()
    assert not at.exception
    vals = {m.label: m.value for m in at.metric}
    repo = json.loads((ROOT / "outputs/2025/calibration_record.json").read_text(encoding="utf8"))
    # an upload has no profile index, so its primary is the same rule on the upcasts found in the data: the repository's first_upcasts_depSM
    prim = next(v for v in repo["rca:variants"] if v["variant_id"] == "first_upcasts_depSM")
    assert vals["Offset (PSU)"] == f"{prim['offset_psu']:+.4f}"
