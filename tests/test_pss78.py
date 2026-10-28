"""UNESCO 1983 (Fofonoff & Millard, Tech. Paper 44) PSS-78 check values.

The UNESCO tables use IPTS-68 temperature; our functions take ITS-90, so T68 is divided by 1.00024.
"""
import numpy as np
import pytest

from rca_sal import pss78

K = 1.00024


@pytest.mark.parametrize("R,t68,p,S", [
    (1.0, 15.0, 0.0, 35.0),
    (1.2, 20.0, 2000.0, 37.245628),
    (1.888091, 40.0, 10000.0, 40.0),
])
def test_unesco_salinity(R, t68, p, S):
    assert pss78.salinity_from_ratio(R, t68 / K, p) == pytest.approx(S, abs=2e-5)


def test_conductivity_roundtrip():
    s = np.array([32.0, 33.7, 34.9])
    t = np.array([4.0, 7.0, 17.0])
    p = np.array([180.0, 90.0, 5.0])
    c = pss78.conductivity_from_salinity(s, t, p)
    assert pss78.salinity_from_conductivity(c, t, p) == pytest.approx(s, abs=1e-8)




def test_ooinet_salinity_is_pss78(prof25):
    s = pss78.salinity_from_conductivity(prof25.conductivity_sm, prof25.temperature_c, prof25.pressure_dbar)
    assert np.nanmax(np.abs(s - prof25.salinity_psu)) < 1e-9
