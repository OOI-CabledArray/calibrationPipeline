import numpy as np
import pandas as pd
import pytest

from rca_sal import calibrate as cal, correction


def test_unknown_kind_is_an_error():
    with pytest.raises(ValueError):
        correction.apply("quadratic", 1.0, 1.0)


def _profile(n=120, bias=0.0, gain=1.0, seed=3):
    rng = np.random.default_rng(seed)
    z = np.linspace(0.2, 119.8, 3000)
    ref = 32 + 0.01 * z
    sens = (ref + bias) / gain + rng.normal(0, 0.002, z.size)
    return z, ref, sens


def test_additive_recovers_synthetic_offset():
    z, ref, sens = _profile(bias=-0.03)
    b = cal.bin_and_smooth(z, {"salinity_psu": sens})
    r = cal.estimate_correction(b, z, ref, kind="additive")
    assert r.value == pytest.approx(-0.03, abs=2e-3)
    assert r.rmse_after < r.rmse


def test_multiplicative_gain_matches_dofsta_convention():
    z, ref, sens = _profile(gain=0.97)            # sensor reads 3 % high -> gain = ref/sensor ~ 0.97
    b = cal.bin_and_smooth(z, {"oxygen": sens * 10}, smooth=())
    r = cal.estimate_correction(b, z, ref * 10, col="oxygen", kind="multiplicative")
    assert r.value == pytest.approx(0.97, rel=2e-3)
    assert correction.apply("multiplicative", 10.0, r.value) == pytest.approx(10.0 * r.value)
    assert r.rmse_after < r.rmse


def test_two_point_interpolation_linear_and_clamped():
    t1, t2 = pd.Timestamp("2025-08-18", tz="UTC"), pd.Timestamp("2026-08-18", tz="UTC")
    mid = t1 + (t2 - t1) / 2
    v = correction.interpolate_value(t1, t2, -0.02, -0.24, [t1 - pd.Timedelta(days=5), mid, t2 + pd.Timedelta(days=5)])
    assert v == pytest.approx([-0.02, -0.13, -0.24])


def test_an31_slope_recovers_conductivity_scale_and_reports_both_rmse():
    from rca_sal.pss78 import conductivity_from_salinity, salinity_from_conductivity
    z = np.arange(0.5, 150, 1.0)
    ref = 32 + 0.012 * z
    t, p = 12 - 0.04 * z, z * 1.01
    c_true = conductivity_from_salinity(ref, t, p)
    scale = 0.999
    c = c_true * scale
    b = pd.DataFrame({"conductivity_sm": c, "temperature_c": t, "pressure_dbar": p,
                      "salinity_psu": salinity_from_conductivity(c, t, p)}, index=z)
    a = cal.an31_slope(b, z, ref, offset=0.0)
    assert a["slope"] == pytest.approx(1 / scale, rel=1e-6)
    assert a["rmse_after"] < 0.1 * a["rmse_before"]
    assert a["rmse_after_offset"] == pytest.approx(a["rmse_before"])   # offset=0 changes nothing
