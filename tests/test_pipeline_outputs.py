"""End-to-end: run 2025 into a temp dir and check provenance and internal consistency of the outputs."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rca_sal.lineage import sha256_file
from rca_sal.pipeline import run_year
from tests.conftest import ROOT, Y25, need, recorded_file

pytestmark = need("profiler_nc", "reference_cnv", "reference_btl", "autosal_xlsx", "cast_log_xlsm")


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("o")
    run_year(2025, ROOT / "config" / "cruises.toml", d)
    return d / "2025"


def test_every_input_hash_in_record_matches_the_file_on_disk(out):
    rec = json.loads((out / "calibration_record.json").read_text(encoding="utf8"))
    used = rec["prov:wasGeneratedBy"]["prov:used"]
    assert {u["rca:role"] for u in used} >= {"profiler_nc", "reference_cnv", "reference_btl", "autosal_xlsx",
                                              "cast_log_xlsm", "profiler_csv", "ooinet_status"}
    for u in used:
        p = recorded_file(u["contentUrl"])
        if p is None:                                      # a file read from GitHub: named by its raw URL at the commit read
            assert u["contentUrl"].startswith("https://raw.githubusercontent.com/")
            continue
        assert not Path(u["contentUrl"]).is_absolute() and "\\" not in u["contentUrl"]      # a path inside the repository
        assert sha256_file(p) == u["sha256"]
        assert p.stat().st_size == u["contentSize"]
        assert u["dateModified"]
    assert rec["rca:ooinet"]["request_id"] == Path(Y25["ooinet_request_dir"]).name
    assert rec["@context"]["prov"].startswith("http://www.w3.org/ns/prov")
    assert rec["rca:parameters"]["global"]["bin_size_m"] and rec["rca:parameters"]["variants"]


def test_output_hashes_listed_in_record_match(out):
    rec = json.loads((out / "calibration_record.json").read_text(encoding="utf8"))
    names = {o["name"] for o in rec["rca:outputs"]}
    assert {"calibration_table.csv", "calibration_table.parquet", "binned_profiles.csv", "bottle_check.csv"} <= names
    for o in rec["rca:outputs"]:
        assert sha256_file(out / o["name"]) == o["sha256"]


def test_table_csv_equals_parquet_and_carries_provenance_columns(out):
    c, p = pd.read_csv(out / "calibration_table.csv"), pd.read_parquet(out / "calibration_table.parquet")
    assert len(c) == len(p)
    np.testing.assert_allclose(c["offset_psu"], p["offset_psu"], rtol=1e-12)
    for col in ("record_id", "profiler_nc_sha256", "reference_cnv_sha256", "config_sha256", "ooinet_request_id"):
        assert c[col].notna().all()


def test_both_t1_times_and_post_correction_rmse_for_both_methods(out):
    t = pd.read_csv(out / "calibration_table.csv")
    prim = t[t.role == "primary"]
    assert len(prim) == 1
    r = prim.iloc[0]
    assert pd.notna(r.t1_mean_profiler_time_utc) and pd.notna(r.t1_cast_time_utc)
    assert r.rmse_after_offset_common_bins_psu < r.rmse_before_common_bins_psu
    assert r.rmse_after_slope_common_bins_psu < r.rmse_before_common_bins_psu
    assert "notebook_window_ascending_prDM" in set(t.variant_id)
    assert (t.role == "per_upcast").sum() == len(pd.read_csv(out / "upcasts.csv"))
    prim = t[t.role == "primary"].iloc[0]
    assert prim.variant_id == "index_first_upcasts_depSM" and prim.n_upcasts == 6 and len(prim.upcast_ids.split(";")) == 6


def test_two_point_t2_reported_missing(out):
    rec = json.loads((out / "calibration_record.json").read_text(encoding="utf8"))
    assert rec["rca:twoPoint"]["T2"]["status"] == "missing"


def test_nc_and_csv_requests_hold_the_same_values(out):
    rec = json.loads((out / "calibration_record.json").read_text(encoding="utf8"))
    chk = rec["rca:inputChecks"]["nc_vs_csv"]
    assert chk["identical_within_tolerance"] and chk["n_rows_nc"] == chk["n_rows_csv"]


def test_binned_profile_series_are_consistent(out):
    t = pd.read_csv(out / "calibration_table.csv").set_index("variant_id")
    b = pd.read_csv(out / "binned_profiles.csv")
    v = b[b.variant_id == "nearest_upcast_depSM"]
    off = t.loc["nearest_upcast_depSM", "offset_psu"]
    np.testing.assert_allclose((v.raw - v.offset_corrected).dropna(), off, atol=1e-12)
    resid = (v.offset_corrected - v.reference).dropna()
    assert np.sqrt((resid ** 2).mean()) == pytest.approx(t.loc["nearest_upcast_depSM", "rmse_after_offset_psu"], abs=1e-12)
