import numpy as np
import pytest

from rca_sal import bottle_check as bc, discrete, seabird
from tests.conftest import Y25, need, path25

ROLES = ("reference_btl", "autosal_xlsx", "cast_log_xlsm")


@pytest.fixture(scope="module")
def matched():
    btl = seabird.read_btl(path25("reference_btl"))
    autosal, _ = discrete.read_autosal(path25("autosal_xlsx"))
    samples = discrete.salinity_samples_for_cast(discrete.read_sample_list(path25("cast_log_xlsm")), Y25["cast"])
    return bc.match_bottles(btl, samples, autosal), autosal, btl


@need(*ROLES)
def test_ctd007_niskins_map_to_sa415_419(matched):
    m, autosal, btl = matched
    assert m["niskin"].tolist() == [3, 11, 15, 19, 23]
    assert m["sample_id"].tolist() == [f"SA-{i}" for i in range(415, 420)]
    assert m["bottle_id"].tolist() == [str(i) for i in range(415, 420)]
    assert m["bottle_salinity"].notna().all()
    # each bottle's Autosal salinity is the workbook value for that numeric ID, nothing else
    for _, r in m.iterrows():
        assert r["bottle_salinity"] == autosal.loc[autosal.bottle_id == r["bottle_id"], "salinity"].iloc[0]
        assert r["bottle"] == r["niskin"]
    # trip depths agree with the cast-log target depths to within a few metres
    assert (np.abs(m["DepSM"] - m["target_depth_m"]) < 5).all()


@need(*ROLES)
def test_ctd_minus_bottle_matches_reference_values(matched):
    m, _, _ = matched
    p = bc.check(m, "Sal00", "C0S/m", "T090C", "PrDM", "primary")
    s = bc.check(m, "Sal11", "C1S/m", "T190C", "PrDM", "secondary")
    assert p.n == s.n == 5
    assert p.median_diff_psu == pytest.approx(0.0076, abs=5e-4)
    assert s.median_diff_psu == pytest.approx(0.0011, abs=5e-4)
    assert p.rmse_psu == pytest.approx(0.0107, abs=5e-4)
    assert p.an31_slope == pytest.approx(0.99992, abs=5e-5)
    np.testing.assert_allclose(p.table["diff_psu"], p.table["Sal00"] - p.table["bottle_salinity"])


@need(*ROLES)
def test_standards_are_flagged_not_treated_as_samples(matched):
    _, autosal, _ = matched
    assert autosal.loc[autosal.bottle_id == "Chk Sw", "is_standard_check"].all()
