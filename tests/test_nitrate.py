"""SUNA nitrate against discrete nitrate (OOI BGC guide chapter 3): profiles, spike test, matchups, offset and fit."""
import numpy as np
import pandas as pd
import pytest

from rca_sal import nitrate as no3

T0 = pd.Timestamp("2025-08-18T12:00:00", tz="UTC")
OK, MISSING = "*0000000000000100", -9999999


def _sensor(offset=-2.0) -> pd.DataFrame:
    """Two profiles 12 h apart; nitrate = 0.15 * depth + offset, temperature = 18 - 0.06 * depth."""
    rows = []
    for k, t0 in enumerate((T0, T0 + pd.Timedelta(hours=12))):
        z = np.linspace(200, 5, 100)
        rows.append(pd.DataFrame({"time": t0 + pd.to_timedelta(np.arange(100) * 2, unit="s"), "depth": z, "temperature": 18 - 0.06 * z,
                                  "nitrate": 0.15 * z + offset}))
    return pd.concat(rows, ignore_index=True)


def test_profiles_are_split_on_time_gaps_and_short_ones_dropped():
    d = no3.label_profiles(pd.concat([_sensor(), _sensor().iloc[:3].assign(time=T0 + pd.Timedelta(hours=30))]), 30, 10)
    assert sorted(set(d["profile_id"])) == [0, 1, 2]
    assert (d["profile_id"] == 0).sum() == 3


def test_spike_test_removes_a_single_spike_only():
    d = no3.label_profiles(_sensor(), 30, 10)
    d.loc[50, "nitrate"] += 8.0
    mask = no3.spike_mask(d["nitrate"], d["profile_id"], 5, 2.0)
    assert np.flatnonzero(mask).tolist() == [50]


def test_sensor_value_by_temperature_and_depth_inside_the_range_only():
    p = no3.bin_profile(_sensor().iloc[:100], 2.0)
    at_t = no3.sensor_at(p, "temperature", [18 - 0.06 * 100, 30.0])
    at_z = no3.sensor_at(p, "depth", [100.0, 500.0])
    assert at_t[0] == pytest.approx(13.0, abs=0.2) and np.isnan(at_t[1])
    assert at_z[0] == pytest.approx(13.0, abs=0.2) and np.isnan(at_z[1])


def test_offset_fit_and_outlier():
    bottle = np.array([1.0, 5.0, 10.0, 20.0, 30.0, 0.3])
    sensor = np.array([-1.0, 3.0, 8.0, 18.0, 28.0, 25.0])        # 2 low everywhere, last bottle is a bad sample
    bad = no3.outliers(bottle, sensor, 10.0)
    assert bad.tolist() == [False] * 5 + [True]
    s = no3.matchup_stats(bottle, sensor, 2.0, bad)
    assert (s["n_matchups"], s["n_outliers"]) == (5, 1)
    assert s["offset_um"] == pytest.approx(2.0) and s["rmse_after_um"] == pytest.approx(0.0, abs=1e-12)
    assert s["fit_slope"] == pytest.approx(1.0) and s["fit_intercept_um"] == pytest.approx(-2.0) and s["fit_r2"] == pytest.approx(1.0)
    assert s["within_accuracy"]


def test_no_matchups_gives_no_offset():
    assert no3.matchup_stats([1.0], [np.nan], 2.0) == {"n_matchups": 0, "n_outliers": 0}


def test_discrete_nitrate_averages_usable_replicates_and_keeps_status(tmp_path):
    def row(niskin, n, flag=OK, nisk=OK, cast="CTD-001"):
        return {"Cast": cast, "Niskin/Bottle Position": niskin, "Niskin Flag": nisk, "CTD Bottle Closure Time [UTC]": "2025-08-18T12:00:00Z",
                "CTD Pressure [db]": 10.0 * niskin, "CTD Depth [m]": 10.0 * niskin, "CTD Temperature 1 [deg C]": 10.0,
                "Discrete Nitrate [uM]": n, "Discrete Nitrite [uM]": 0.01, "Discrete Nutrients Flag": flag}
    rows = [row(1, 10.0), row(1, 12.0), row(2, 5.0, flag=MISSING), row(3, 7.0, flag="*0000000000010000"), row(4, MISSING, flag=MISSING),
            row(5, 3.0, nisk="*0000000000010100"), row(6, 1.0, cast="CTD-002")]
    f = tmp_path / "s.csv"
    pd.DataFrame(rows).to_csv(f, index=False)
    b = no3.read_discrete_nitrate(f, "CTD-001").set_index("niskin")
    assert b.loc["1", "nitrate_bottle"] == pytest.approx(11.0) and b.loc["1", "n_replicates"] == 2
    assert b["status"].to_dict() == {"1": "accepted", "2": "unflagged", "3": "flagged", "4": "none", "5": "accepted"}
    assert b["usable"].to_dict() == {"1": True, "2": True, "3": False, "4": False, "5": False}     # 5: leaking Niskin
    with pytest.raises(ValueError):
        no3.read_discrete_nitrate(f, "CTD-009")


def test_bottle_profile_interpolated_for_a_parked_record():
    b = pd.DataFrame({"temperature": [12.0, 9.0, 7.0], "depth_m": [10.0, 100.0, 200.0], "nitrate_bottle": [1.0, 20.0, 30.0]})
    assert no3.bottle_at(b, "depth_m", 150.0) == pytest.approx(25.0)
    assert no3.bottle_at(b, "temperature", 8.0) == pytest.approx(25.0)
    assert np.isnan(no3.bottle_at(b, "temperature", 6.9))
