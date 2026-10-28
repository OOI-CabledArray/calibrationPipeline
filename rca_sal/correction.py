"""Correction kinds and linear interpolation in time.

additive:        value = median(sensor - reference),  corrected = raw - value
multiplicative:  value = median(reference / sensor),  corrected = raw * value
For values v1, v2 at times t1, t2 the value at t is interpolated linearly and held constant outside [t1, t2].
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KINDS = ("additive", "multiplicative")


def _check(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown correction kind {kind!r}; expected one of {KINDS}")


def pointwise(kind: str, sensor, reference) -> np.ndarray:
    """Per-bin discrepancy whose median is the correction value."""
    _check(kind)
    s, r = np.asarray(sensor, dtype=float), np.asarray(reference, dtype=float)
    return s - r if kind == "additive" else r / s


def apply(kind: str, raw, value):
    _check(kind)
    return raw - value if kind == "additive" else raw * value


def interpolate_value(t1, t2, v1: float, v2: float, times) -> np.ndarray:
    """Linear interpolation between (t1, v1) and (t2, v2); constant outside, as numpy.interp."""
    # pandas >= 3 infers the datetime resolution from the input; convert to ns first.
    t = pd.DatetimeIndex(pd.to_datetime(times, utc=True)).as_unit("ns").asi8
    edge = [pd.Timestamp(x).as_unit("ns").value for x in (t1, t2)]
    return np.interp(t, edge, [v1, v2])
