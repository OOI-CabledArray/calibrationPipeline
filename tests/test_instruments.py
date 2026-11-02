"""Pairing of discrete-summary bottles with deployed CTD and oxygen instruments."""
import numpy as np
import pandas as pd
import pytest

from rca_sal import instruments as inst

SEAFLOOR = "RS03AXBS-LJ03A-12-CTDPFB301"
PROFILER = "RS03AXPS-SF03A-2A-CTDPFA302"
ROV, SHIP = "*0010000000000000", "*0000000000000000"
OK = "*0000000000000100"
MISSING = -9999999


def _deployments() -> pd.DataFrame:
    rows = [
        (SEAFLOOR, 1, "2020-01-01T00:00:00", "2020-08-01T00:00:00", 45.8168, -129.7540, 2607.0),
        (SEAFLOOR, 2, "2020-08-05T00:00:00", None, 45.8168, -129.7540, 2607.0),
        (PROFILER, 1, "2020-01-01T00:00:00", None, 45.8300, -129.7500, np.nan),
        ("RS03AXBS-LJ03A-12-ADCPSK101", 1, "2020-01-01T00:00:00", None, 45.8168, -129.7540, 2607.0),
    ]
    return inst.read_deployments([pd.DataFrame(rows, columns=["Reference Designator", "deploymentNumber", "startDateTime", "stopDateTime",
                                                              "lat", "lon", "deployment_depth"])])


def _bottle(cast, time, lat, lon, depth, rov=False, sal=34.6, sal_flag=OK, o2=2.0, o2_flag=OK, niskin=OK, label="CTDPFB301", position="A"):
    return {"Cruise": "X1", "Cast": cast, "Niskin/Bottle Position": position, "Niskin Flag": niskin, "Cast Flag": ROV if rov else SHIP,
            "CTD Bottle Closure Time [UTC]": time, "Start Latitude [degrees]": lat, "Start Longitude [degrees]": lon,
            "CTD Pressure [db]": MISSING, "CTD Depth [m]": depth, "Discrete Salinity [psu]": sal, "Discrete Salinity Flag": sal_flag,
            "Discrete Oxygen [mL/L]": o2, "Discrete Oxygen Flag": o2_flag, "Target Asset": label, "Station": "s"}


def _match(rows, **kw):
    par = {"max_distance_km": 5.0, "max_distance_rov_km": 0.25, "max_depth_difference_m": 30.0, "max_gap_hours": 72.0, **kw}
    return inst.match_samples(pd.DataFrame(rows), _deployments(), **par)


# ------------------------------------------------------------------ formulas and codes
def test_distance_of_one_degree_of_latitude():
    assert inst.distance_km(10.0, 20.0, 11.0, 20.0) == pytest.approx(111.195, abs=1e-2)


def test_instrument_codes_placements_and_classes():
    assert inst.instrument_code(SEAFLOOR) == "CTDPFB301"
    assert [inst.placement(r) for r in (SEAFLOOR, PROFILER, "RS03AXPS-PC03A-4A-CTDPFA303", "RS03AXPD-DP03A-01-CTDPFL304",
                                         "CE04OSSM-RID27-03-CTDBPC000")] == ["seafloor", "shallow profiler", "200 m platform", "deep profiler", "surface mooring"]
    assert inst.instrument_class("RS03AXBS-LJ03A-12-DOSTAD301") == "oxygen" and inst.instrument_class(SEAFLOOR) == "CTD"
    with pytest.raises(ValueError):
        inst.placement("RS03AXBS-XX03A-12-CTDPFB301")


def test_deployments_keep_only_ctd_and_oxygen_sensors():
    assert set(_deployments()["rd"]) == {SEAFLOOR, PROFILER}


# ------------------------------------------------------------------ sample status
def test_discrete_status_separates_accepted_unflagged_flagged_and_missing():
    value = pd.Series([34.6, 34.6, 34.6, 34.6, np.nan])
    flag = pd.Series([OK, MISSING, "*0000000000001000", "*0000000000001100", MISSING])
    assert inst._status(value, flag).tolist() == ["accepted", "unflagged", "flagged", "flagged", "none"]


# ------------------------------------------------------------------ matching
def test_rov_bottle_at_the_instrument_is_matched_and_a_distant_one_is_not():
    m = _match([_bottle("J1", "2020-03-01T00:00:00Z", 45.8168, -129.7540, 2605, rov=True),
                _bottle("J2", "2020-03-01T00:00:00Z", 45.8168 + 2 / 111.195, -129.7540, 2605, rov=True)])
    seafloor = m[m["rd"] == SEAFLOOR]
    assert seafloor["Cast"].tolist() == ["J1"] and seafloor["timing"].tolist() == ["deployed"]
    assert seafloor["depth_difference_m"].iloc[0] == pytest.approx(2.0)


def test_ship_cast_bottle_needs_the_right_depth_but_may_be_kilometres_away():
    near_bottom = _bottle("C1", "2020-03-01T00:00:00Z", 45.8168 + 1.5 / 111.195, -129.7540, 2590, label="RS03AXPS")
    mid_water = _bottle("C1", "2020-03-01T00:00:00Z", 45.8168 + 1.5 / 111.195, -129.7540, 2000, label="RS03AXPS", position="B")
    m = _match([near_bottom, mid_water])
    assert m[m["rd"] == SEAFLOOR]["Niskin/Bottle Position"].tolist() == ["A"]
    assert sorted(m[m["rd"] == PROFILER]["Niskin/Bottle Position"]) == ["A", "B"]          # profiler: no depth test
    assert m.groupby("rd")["label_names_instrument"].all().to_dict() == {SEAFLOOR: False, PROFILER: True}        # the label names the profiler's site


def test_bottle_between_deployments_is_a_gap_match_only_within_the_limit():
    soon = _bottle("J1", "2020-08-02T00:00:00Z", 45.8168, -129.7540, 2607, rov=True)       # 24 h after deployment 1 stopped
    late = _bottle("J2", "2020-08-04T20:00:00Z", 45.8168, -129.7540, 2607, rov=True)       # 4 h before deployment 2 started
    far = _bottle("J3", "2020-08-20T00:00:00Z", 45.8168, -129.7540, 2607, rov=True)        # deployed (2)
    m = _match([soon, late, far], max_gap_hours=12.0)
    s = m[m["rd"] == SEAFLOOR].set_index("Cast")
    assert s["timing"].to_dict() == {"J2": "gap", "J3": "deployed"}                         # J1 is 24 h from both: dropped
    assert s.loc["J2", "gap_hours"] == pytest.approx(4.0) and s.loc["J2", "deployment"] == 2


def test_label_naming_the_instrument_or_its_site_is_recorded():
    m = _match([_bottle("J1", "2020-03-01T00:00:00Z", 45.8168, -129.7540, 2607, rov=True, label="CTDPFB301,DOSTAD301"),
                _bottle("J2", "2020-03-01T00:00:00Z", 45.8168, -129.7540, 2607, rov=True, label="RS03AXBS", position="B"),
                _bottle("J3", "2020-03-01T00:00:00Z", 45.8168, -129.7540, 2607, rov=True, label="CTDPFB101", position="C")])
    assert m[m["rd"] == SEAFLOOR].sort_values("Cast")["label_names_instrument"].tolist() == [True, True, False]


# ------------------------------------------------------------------ inventory
def test_inventory_counts_each_bottle_once_with_its_best_replicate_status():
    rows = [_bottle("J1", "2020-03-01T00:00:00Z", 45.8168, -129.7540, 2607, rov=True, sal=34.6, sal_flag="*0000000000001000"),   # flagged replicate
            _bottle("J1", "2020-03-01T00:00:00Z", 45.8168, -129.7540, 2607, rov=True, sal=34.7, sal_flag=OK),                      # accepted replicate
            _bottle("J1", "2020-03-01T00:00:00Z", 45.8168, -129.7540, 2607, rov=True, position="B", sal=34.6, sal_flag=MISSING),   # unflagged
            _bottle("J1", "2020-03-01T00:00:00Z", 45.8168, -129.7540, 2607, rov=True, position="C", sal=MISSING, sal_flag=MISSING, o2=MISSING, o2_flag=MISSING),
            _bottle("J1", "2020-03-01T00:00:00Z", 45.8168, -129.7540, 2607, rov=True, position="D", niskin="*0000000000010100")]  # leaking Niskin
    inv = inst.inventory(_match(rows))
    r = inv[inv["reference_designator"] == SEAFLOOR].iloc[0]
    assert (r["bottles"], r["bottles_niskin_ok"], r["rov_bottles"]) == (4, 3, 4)
    assert [r[f"salinity_{k}"] for k in inst.STATUSES] == [1, 1, 0, 1]
    assert [r[f"oxygen_{k}"] for k in inst.STATUSES] == [2, 0, 0, 1]
    assert r["instrument"] == "CTDPFB301" and r["class"] == "CTD" and r["timing"] == "deployed"
