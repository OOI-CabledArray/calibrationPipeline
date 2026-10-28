"""EOS-80 seawater density and potential temperature (UNESCO 1983), numpy only.

Used to convert oxygen from ml/l to umol/kg:
    umol/kg = (ml/l) * 44660 / (sigma_theta + 1000),   sigma_theta = rho(S, theta, 0 dbar) - 1000  [kg/m3].
Temperatures are ITS-90 and converted to IPTS-68 with T68 = 1.00024 * T90. Checked against the UNESCO TR44 values in tests/test_oxygen.py.
"""
from __future__ import annotations

import numpy as np

T68_PER_T90 = 1.00024
UMOL_PER_ML_O2 = 44660.0     # umol of O2 per (ml/l)


def _rho_smow(t):
    return (999.842594 + 6.793952e-2 * t - 9.095290e-3 * t ** 2 + 1.001685e-4 * t ** 3
            - 1.120083e-6 * t ** 4 + 6.536332e-9 * t ** 5)


def dens0(s, t68):
    """Density at the surface (p = 0) [kg/m3]; t68 in IPTS-68."""
    s, t = np.asarray(s, float), np.asarray(t68, float)
    a = 8.24493e-1 - 4.0899e-3 * t + 7.6438e-5 * t ** 2 - 8.2467e-7 * t ** 3 + 5.3875e-9 * t ** 4
    b = -5.72466e-3 + 1.0227e-4 * t - 1.6546e-6 * t ** 2
    return _rho_smow(t) + a * s + b * s ** 1.5 + 4.8314e-4 * s ** 2


def dens(s, t68, p):
    """In-situ density [kg/m3] for p in dbar; t68 in IPTS-68."""
    s, t, p = np.asarray(s, float), np.asarray(t68, float), np.asarray(p, float) / 10.0   # p in bars
    kw = 19652.21 + 148.4206 * t - 2.327105 * t ** 2 + 1.360477e-2 * t ** 3 - 5.155288e-5 * t ** 4
    k0 = (kw + s * (54.6746 - 0.603459 * t + 1.09987e-2 * t ** 2 - 6.1670e-5 * t ** 3)
          + s ** 1.5 * (7.944e-2 + 1.6483e-2 * t - 5.3009e-4 * t ** 2))
    aw = 3.239908 + 1.43713e-3 * t + 1.16092e-4 * t ** 2 - 5.77905e-7 * t ** 3
    a = aw + (2.2838e-3 - 1.0981e-5 * t - 1.6078e-6 * t ** 2) * s + 1.91075e-4 * s ** 1.5
    bw = 8.50935e-5 - 6.12293e-6 * t + 5.2787e-8 * t ** 2
    b = bw + (-9.9348e-7 + 2.0816e-8 * t + 9.1697e-10 * t ** 2) * s
    k = k0 + a * p + b * p ** 2
    return dens0(s, t) / (1.0 - p / k)


def adiabatic_gradient(s, t68, p):
    """Adiabatic temperature gradient [K/dbar]."""
    s, t, p = np.asarray(s, float), np.asarray(t68, float), np.asarray(p, float)
    ds = s - 35.0
    return (3.5803e-5 + 8.5258e-6 * t - 6.8360e-8 * t ** 2 + 6.6228e-10 * t ** 3
            + (1.8932e-6 - 4.2393e-8 * t) * ds
            + (1.8741e-8 - 6.7795e-10 * t + 8.7330e-12 * t ** 2 - 5.4481e-14 * t ** 3) * p
            + (-1.1351e-10 + 2.7759e-12 * t) * p * ds
            + (-4.6206e-13 + 1.8676e-14 * t - 2.1687e-16 * t ** 2) * p ** 2)


def ptmp(s, t68, p, pref=0.0):
    """Potential temperature [IPTS-68] referenced to `pref` dbar (4th-order Runge-Kutta, Bryden 1973)."""
    s, t, p = np.asarray(s, float), np.asarray(t68, float), np.asarray(p, float)
    dp = pref - p
    r2 = np.sqrt(2.0)
    d = dp * adiabatic_gradient(s, t, p)
    th, q = t + 0.5 * d, d
    d = dp * adiabatic_gradient(s, th, p + 0.5 * dp)
    th = th + (1 - 1 / r2) * (d - q)
    q = (2 - r2) * d + (-2 + 3 / r2) * q
    d = dp * adiabatic_gradient(s, th, p + 0.5 * dp)
    th = th + (1 + 1 / r2) * (d - q)
    q = (2 + r2) * d + (-2 - 3 / r2) * q
    d = dp * adiabatic_gradient(s, th, p + dp)
    return th + (d - 2 * q) / 6.0


def sigma_theta(s, t90, p):
    """EOS-80 potential density anomaly [kg/m3] (rho(S, theta, 0) - 1000) from ITS-90 T and p in dbar."""
    t68 = np.asarray(t90, float) * T68_PER_T90
    return dens0(s, ptmp(s, t68, p, 0.0)) - 1000.0


def oxygen_mll_to_umolkg(o2_ml_per_l, s, t90, p):
    """Sea-Bird oxygen conversion: umol/kg = ml/l * 44660 / (sigma-theta + 1000)."""
    return np.asarray(o2_ml_per_l, float) * UMOL_PER_ML_O2 / (sigma_theta(s, t90, p) + 1000.0)
