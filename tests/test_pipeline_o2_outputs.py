"""End-to-end oxygen run into a temp dir: provenance and internal consistency."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rca_sal.lineage import sha256_file
from rca_sal.pipeline_o2 import run_year_oxygen
from tests.conftest import ROOT, Y25, recorded_file, resolve_spec

O = Y25["oxygen"]
_need = [resolve_spec(v) for v in O["inputs"].values()]
_need += [resolve_spec(Y25["inputs"][k]) for k in ("reference_cnv", "reference_btl", "discrete_summary_csv", "profile_index_csv")]
pytestmark = pytest.mark.skipif(not all(f.is_file() for f in _need), reason="oxygen inputs not reachable")


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("o2")
    run_year_oxygen(2025, ROOT / "config" / "cruises.toml", d)
    return d / "2025" / "oxygen"


def test_inputs_and_outputs_hash_match_disk(out):
    rec = json.loads((out / "calibration_record.json").read_text(encoding="utf8"))
    roles = {u["rca:role"] for u in rec["prov:wasGeneratedBy"]["prov:used"]}
    assert roles >= {"winkler_xls", "discrete_summary_csv", "profile_index_csv", "dofsta_nc", "reference_cnv", "reference_btl", "ooinet_status"}
    for u in rec["prov:wasGeneratedBy"]["prov:used"]:
        p = recorded_file(u["contentUrl"])
        if p is None:
            assert u["contentUrl"].startswith("https://raw.githubusercontent.com/")
        else:
            assert sha256_file(p) == u["sha256"]
    for o in rec["rca:outputs"]:
        assert sha256_file(out / o["name"]) == o["sha256"]
    assert rec["rca:ooinet"]["request_id"] == Path(O["ooinet_request_dir"]).name
    assert rec["rca:correctionKind"] == "multiplicative"
    assert rec["rca:twoPoint"]["T2"]["status"] == "missing"


def test_table_has_provenance_and_both_t1_times(out):
    t = pd.read_csv(out / "calibration_table.csv")
    pq = pd.read_parquet(out / "calibration_table.parquet")
    np.testing.assert_allclose(t["dofsta_gain"], pq["dofsta_gain"], rtol=1e-12)
    for c in ("record_id", "dofsta_nc_sha256", "winkler_xls_sha256", "discrete_summary_csv_sha256", "reference_cnv_sha256", "config_sha256",
              "ooinet_request_id"):
        assert t[c].notna().all()
    prim = t[t.role == "primary"].iloc[0]
    assert pd.notna(prim.t1_mean_profiler_time_utc) and pd.notna(prim.t1_cast_time_utc)
    assert (t.role == "per_upcast").sum() == len(pd.read_csv(out / "upcasts.csv")) and "notebook_window_ascending_prDM" in set(t.variant_id)
    assert prim.variant_id == "index_first_upcasts_depSM" and prim.n_upcasts == 6 and prim.bottle_source == "summary"
    assert set(t.loc[t.selection.str.startswith("notebook"), "bottle_source"]) == {"workbook"}
    assert "combined_gain_ctd_x_dofsta" not in t.columns          # DOFSTA gain already includes the CTD gain


def test_gain_improves_rmse_and_profiles_are_raw_times_gain(out):
    t = pd.read_csv(out / "calibration_table.csv").set_index("variant_id")
    b = pd.read_csv(out / "binned_profiles.csv")
    v = b[b.variant_id == "nearest_upcast_depSM"]
    np.testing.assert_allclose((v.corrected / v.raw).dropna(), t.loc["nearest_upcast_depSM", "dofsta_gain"], rtol=1e-12)
    assert t.loc["nearest_upcast_depSM", "rmse_after_umolkg"] < t.loc["nearest_upcast_depSM", "rmse_before_umolkg"]
    resid = (v.corrected - v.reference).dropna()
    assert np.sqrt((resid ** 2).mean()) == pytest.approx(t.loc["nearest_upcast_depSM", "rmse_after_umolkg"], abs=1e-9)


def test_winkler_table_ratios(out):
    w = pd.read_csv(out / "winkler_check.csv")
    p = w[w.variant_id == "nearest_upcast_depSM"]
    np.testing.assert_allclose(p.ratio_winkler_over_ctd, p.o2_winkler_umolkg / p.o2_ctd_at_winkler_umolkg, rtol=1e-12)
    t = pd.read_csv(out / "calibration_table.csv").set_index("variant_id")
    assert np.median(p.ratio_winkler_over_ctd) == pytest.approx(t.loc["nearest_upcast_depSM", "ctd_gain"], abs=1e-12)


def test_summary_and_workbook_bottles_agree_in_ml_per_l_and_differ_only_where_the_workbook_used_no_salinity():
    """The discrete summary repeats the workbook's mL/L; the workbook's umol/kg divides by a density computed with S = 0 for bottles
    without a salinity sample, so only those rows differ (by the density ratio)."""
    from rca_sal import oxygen as ox
    summ = ox.read_discrete_oxygen(resolve_spec(Y25["inputs"]["discrete_summary_csv"]), O["cast"])
    wb = ox.read_winkler(resolve_spec(O["inputs"]["winkler_xls"]), O["cast"])
    assert len(summ) == len(wb)
    np.testing.assert_allclose(summ["niskin"], wb["niskin"])
    ratio = (wb["o2_winkler_umolkg"] / summ["o2_winkler_umolkg"]).to_numpy()
    ok = np.abs(ratio - 1) < 0.002
    assert ok.sum() >= 4 and (ratio[~ok] > 1.02).all()
