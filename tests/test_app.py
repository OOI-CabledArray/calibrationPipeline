"""Headless AppTest of every page, plus the 'missing' behaviour. Needs outputs/2025 (run scripts/run_year.py)."""
import json
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import ROOT

PAGES = ["app_pages/bottles.py", "app_pages/profiler.py", "app_pages/oxygen_winkler.py", "app_pages/oxygen_dofsta.py", "app_pages/upload.py", "app_pages/provenance.py"]
pytestmark = pytest.mark.skipif(
    not all((ROOT / "outputs" / "2025" / n).is_file() for n in ("calibration_record.json", "oxygen/calibration_record.json")),
    reason="outputs/2025 (salinity and oxygen) not generated")


def _run(page):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception
    if page != PAGES[0]:
        at.switch_page(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page):
    at = _run(page)
    assert not at.exception
    assert not at.error


def test_profiler_page_scorecards_come_from_outputs():
    at = _run(PAGES[1])
    rec = json.loads((ROOT / "outputs/2025/calibration_record.json").read_text(encoding="utf8"))
    prim = next(v for v in rec["rca:variants"] if v["variant_id"] == rec["rca:primaryVariant"])
    vals = {m.label: m.value for m in at.metric}
    assert vals["Offset (PSU)"] == f"{prim['offset_psu']:+.4f}"
    assert vals["AN31 slope"] == f"{prim['an31_slope']:.6f}"


def test_oxygen_page_scorecard_comes_from_outputs():
    at = _run("app_pages/oxygen_dofsta.py")
    rec = json.loads((ROOT / "outputs/2025/oxygen/calibration_record.json").read_text(encoding="utf8"))
    prim = next(v for v in rec["rca:variants"] if v["variant_id"] == rec["rca:primaryVariant"])
    vals = {m.label: m.value for m in at.metric}
    assert vals["DOFSTA gain"] == f"{prim['dofsta_gain']:.5f}"
    assert vals["CTD gain (step 1)"] == f"{prim['ctd_gain']:.5f}"


def test_provenance_page_can_show_oxygen():
    at = _run("app_pages/provenance.py")
    at.sidebar.get("button_group")[0].set_value("Oxygen").run() if at.sidebar.get("button_group") else None
    assert not at.exception


def test_year_with_missing_inputs_says_missing_and_stops(tmp_path, monkeypatch):
    d = ROOT / "outputs" / "1999"
    d.mkdir()
    try:
        (d / "missing.json").write_text(json.dumps({"year": 1999, "status": "missing", "checked_utc": "x",
                                                    "missing": [{"role": "reference_cnv", "path": "G:/nowhere.cnv"}]}))
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
        at.sidebar.selectbox[0].select("1999").run()
        assert not at.exception
        assert any("missing input" in e.value for e in at.error)
        assert not at.metric
    finally:
        shutil.rmtree(d)


def test_icon_set_is_square_and_complete():
    from PIL import Image
    d = ROOT / "assets" / "icons"
    sizes = {"icon-16.png": 16, "icon-32.png": 32, "icon-48.png": 48, "icon-64.png": 64, "icon-192.png": 192,
             "apple-touch-icon.png": 180, "icon-512.png": 512}
    for name, px in sizes.items():
        assert Image.open(d / name).size == (px, px), name
    assert {(16, 16), (32, 32), (48, 48), (64, 64)} <= set(Image.open(d / "favicon.ico").info["sizes"])
