"""Instrument values at bottle times, offsets and gains of the seafloor CTDs."""
import numpy as np
import pandas as pd
import pytest

from rca_sal import seafloor as sf

T0 = pd.Timestamp("2020-01-01T12:00:00", tz="UTC")


def _instrument() -> pd.DataFrame:
    t = pd.date_range(T0 - pd.Timedelta(hours=1), periods=7200, freq="s", tz="UTC")
    return pd.DataFrame({"time": t, "salinity": 34.60, "oxygen": 100.0, "pressure": 2600.0, "temperature": 1.8})


def test_window_values_use_only_samples_within_the_window():
    inst = _instrument()
    inst.loc[inst["time"] > T0 + pd.Timedelta(minutes=30), "salinity"] = 99.0
    v = sf.window_values(inst, [T0, T0 + pd.Timedelta(hours=3)], pd.Timedelta(minutes=10))
    assert v["n_samples"].tolist() == [1201, 0]
    assert v["inst_salinity"].iloc[0] == pytest.approx(34.60) and np.isnan(v["inst_salinity"].iloc[1])


def test_exclusion_annotation_drops_instrument_samples():
    ann = [{"exclusion_flag": True, "start": (T0 - pd.Timedelta(minutes=5)).isoformat(), "stop": (T0 + pd.Timedelta(minutes=5)).isoformat(),
            "subsite": None, "node": None, "sensor": None}]
    v = sf.window_values(_instrument(), [T0], pd.Timedelta(minutes=10), ann, "RS03AXBS-LJ03A-12-CTDPFB301")
    assert v["n_samples"].iloc[0] == 600 and v["n_annotation_excluded"].iloc[0] == 601


def test_offset_gain_and_deployment_median():
    bottles = pd.DataFrame({"rd": "X-A-1-CTDPFB1", "deployment": 3, "cruise": "C", "time": [T0, T0],
                            "salinity_bottle": [34.61, 34.63], "oxygen_bottle_umolkg": [110.0, 120.0], "ctd_salinity_at_bottle": [34.60, 34.60]})
    b = sf.compare(bottles, sf.window_values(_instrument(), bottles["time"], pd.Timedelta(minutes=10)))
    assert b["salinity_offset"].tolist() == pytest.approx([0.01, 0.03])
    assert b["oxygen_gain"].tolist() == pytest.approx([1.1, 1.2])
    s = sf.deployment_summary(b).iloc[0]
    assert (s["n_salinity_offset"], s["salinity_offset_median"], s["oxygen_gain_median"]) == (2, pytest.approx(0.02), pytest.approx(1.15))
