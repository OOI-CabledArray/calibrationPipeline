"""Site-keyed configuration: another site's year inherits the cruise facts and shared workbooks, nothing else (no network)."""
import json
import sys

import pandas as pd
import pytest

from rca_sal.pipeline import MissingInputError, load_config, run_key, run_year, site_year
from tests.conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
from site_gaps import gap_row  # noqa: E402

CFG = ROOT / "config" / "cruises.toml"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CFG)


def test_default_site_is_the_year_entry_itself(cfg):
    y = site_year(cfg, 2025)
    assert y["site"] == cfg["sites"]["default"] and y["inputs"] == cfg["years"]["2025"]["inputs"]
    assert site_year(cfg, 2025, cfg["sites"]["default"])["cast"] == y["cast"]


def test_other_site_takes_shared_workbooks_but_its_own_cast_and_files(cfg):
    base, y = cfg["years"]["2025"], site_year(cfg, 2025, "RS01SBPS")
    assert "profiler_nc" not in base["sites"]["RS01SBPS"].get("inputs", {}) or base["inputs"]["profiler_nc"] != y["inputs"]["profiler_nc"]
    assert y["site"] == "RS01SBPS" and y["cast"] != base["cast"] and y["cruise"] == base["cruise"]
    assert y["inputs"]["cast_log_xlsm"] == base["inputs"]["cast_log_xlsm"] and y["inputs"]["autosal_xlsx"] == base["inputs"]["autosal_xlsx"]
    assert y["inputs"]["reference_cnv"].endswith(f"{y['cast']}.cnv") and y["inputs"]["reference_cnv"] != base["inputs"]["reference_cnv"]
    assert y["inputs"].get("profiler_nc") != base["inputs"]["profiler_nc"]   # the default site's profiler file is never inherited
    assert y["profiler_reference_designator"] == cfg["sites"]["RS01SBPS"]["profiler_reference_designator"]


def test_unknown_site_is_an_error_not_a_fallback(cfg):
    with pytest.raises(KeyError, match=r"no \[years.2025.sites.NOPE\]"):
        site_year(cfg, 2025, "NOPE")


def test_run_keys(cfg):
    assert run_key(cfg, 2025) == "2025" and run_key(cfg, 2025, cfg["sites"]["default"]) == "2025"
    assert run_key(cfg, 2025, "CE04OSPS") == "2025-CE04OSPS"


def test_every_configured_site_year_names_a_cast_and_an_index(cfg):
    n = 0
    for year, y in cfg["years"].items():
        for site, sc in y.get("sites", {}).items():
            n += 1
            assert site in cfg["sites"] and sc["cast"] in sc["inputs"]["reference_cnv"] and sc["cast"] in sc["inputs"]["reference_btl"]
            assert sc["inputs"]["profile_index_csv"] == f"github[profile_indices]:{cfg['profile_index']['file'].format(site=site, year=year)}"
    assert n > 0


def test_site_without_profiler_file_is_reported_missing_and_writes_nothing(tmp_path, cfg):
    c = load_config(CFG)
    del c["years"]["2025"]["sites"]["RS01SBPS"]["inputs"]["profiler_nc"]
    (tmp_path / "c.json").write_text(json.dumps(c), encoding="utf8")
    with pytest.raises(MissingInputError) as e:
        run_year(2025, tmp_path / "c.json", tmp_path, site="RS01SBPS")
    assert [m["role"] for m in e.value.missing] == ["profiler_nc"]


def test_gap_is_zero_inside_an_upcast_and_counts_neighbours():
    t = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    ix = pd.DataFrame({"profile": [1, 2], "start": [t("2025-01-01 00:00"), t("2025-01-01 03:00")],
                       "peak": [t("2025-01-01 00:30"), t("2025-01-01 03:30")], "end": [t("2025-01-01 01:00"), t("2025-01-01 04:00")]})
    g = gap_row(t("2025-01-01 00:10"), ix)
    assert g["nearest_profile_gap_h"] == 0 and g["profiles_within_6h"] == 2 and g["first_profile_after_h"] == 2.83
    assert gap_row(t("2025-01-01 02:00"), ix)["nearest_profile_gap_h"] == 1.0   # 1.5 h after profile 1, 1 h before profile 2
