"""Practical Salinity Scale 1978 (UNESCO 1983), numpy only.

Salinity from conductivity, and the inverse (conductivity of a given salinity at given temperature and pressure), used for the AN31
slope. Tested against the UNESCO check values in tests/test_pss78.py.
"""
from __future__ import annotations

import numpy as np

C3515 = 4.2914  # S/m, conductivity of standard seawater S=35, T=15 C (IPTS-68), P=0

_A = (0.0080, -0.1692, 25.3851, 14.0941, -7.0261, 2.7081)
_B = (0.0005, -0.0056, -0.0066, -0.0375, 0.0636, -0.0144)
_K = 0.0162
_C = (0.6766097, 2.00564e-2, 1.104259e-4, -6.9698e-7, 1.0031e-9)
_D = (3.426e-2, 4.464e-4, 4.215e-1, -3.107e-3)
_E = (2.070e-5, -6.370e-10, 3.989e-15)


def _t68(t90):
    return np.asarray(t90, dtype=float) * 1.00024


def _rt(t68):
    c = _C
    return c[0] + t68 * (c[1] + t68 * (c[2] + t68 * (c[3] + t68 * c[4])))


def _rp(R, t68, p):
    d, e = _D, _E
    num = p * (e[0] + p * (e[1] + p * e[2]))
    den = 1 + d[0] * t68 + d[1] * t68 ** 2 + (d[2] + d[3] * t68) * R
    return 1 + num / den


def _sal(Rt, t68):
    a, b = _A, _B
    rt = np.sqrt(np.abs(Rt))
    ds = (t68 - 15) / (1 + _K * (t68 - 15))
    s = sum(a[i] * rt ** i for i in range(6))
    s += ds * sum(b[i] * rt ** i for i in range(6))
    return s


def _dsal_drt(Rt, t68):
    a, b = _A, _B
    rt = np.sqrt(np.abs(Rt))
    ds = (t68 - 15) / (1 + _K * (t68 - 15))
    dsdrt = sum(i * a[i] * rt ** (i - 1) for i in range(1, 6))
    dsdrt += ds * sum(i * b[i] * rt ** (i - 1) for i in range(1, 6))
    return dsdrt / (2 * rt)


def salinity_from_ratio(R, t90, p):
    """Practical salinity from conductivity ratio R = C/C(35,15,0)."""
    t68 = _t68(t90)
    R = np.asarray(R, dtype=float)
    p = np.asarray(p, dtype=float)
    Rt = R / (_rp(R, t68, p) * _rt(t68))
    return _sal(Rt, t68)


def salinity_from_conductivity(c_sm, t90, p):
    """Practical salinity from conductivity [S/m], ITS-90 temperature [C], pressure [dbar]."""
    return salinity_from_ratio(np.asarray(c_sm, dtype=float) / C3515, t90, p)


def conductivity_from_salinity(s, t90, p, n_iter: int = 20):
    """Invert PSS-78: conductivity [S/m] for salinity s at in-situ t90 [C] and p [dbar]."""
    s = np.asarray(s, dtype=float)
    t68 = _t68(t90)
    p = np.asarray(p, dtype=float)
    # Newton iteration on Rt at atmospheric pressure, then solve the pressure term for R.
    Rt = np.full(np.broadcast(s, t68).shape, 1.0) * np.sqrt(np.clip(s, 0.1, None) / 35.0)
    for _ in range(n_iter):
        Rt = Rt - (_sal(Rt, t68) - s) / _dsal_drt(Rt, t68)
    rt = _rt(t68)
    # R = Rt * rt * Rp(R) -> solve the quadratic in R implied by Rp's form
    d, e = _D, _E
    A = d[2] + d[3] * t68
    B = 1 + d[0] * t68 + d[1] * t68 ** 2
    C = p * (e[0] + p * (e[1] + p * e[2]))
    k = Rt * rt
    # R*(B + A R) = k*(B + A R + C)  ->  A R^2 + (B - k A) R - k (B + C) = 0
    aa, bb, cc = A, B - k * A, -k * (B + C)
    R = (-bb + np.sqrt(bb ** 2 - 4 * aa * cc)) / (2 * aa)
    return R * C3515
