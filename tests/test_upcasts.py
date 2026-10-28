import numpy as np
import pandas as pd
import pytest

from rca_sal import profiler
from tests.conftest import need


def _saw(n_cycles=3, dt=1.0, v=0.045, top=5.0, bottom=185.0, rest=600, start="2025-08-18", gaps=()):
    """Synthetic profiler: rest at depth, ascend, descend (fast), repeat."""
    z = [bottom] * rest
    for _ in range(n_cycles):
        up = np.arange(bottom, top, -v * dt)
        down = np.arange(top, bottom, 0.5 * dt)
        z += list(up) + list(down) + [bottom] * rest
    z = np.array(z)
    t = pd.Timestamp(start, tz="UTC") + pd.to_timedelta(np.arange(len(z)) * dt, unit="s")
    return pd.DataFrame({"time": t, "depth_m": z})


def test_finds_every_upcast():
    out = profiler.label_upcasts(_saw(3))
    assert sorted(set(out.upcast_id) - {-1}) == [0, 1, 2]
    assert (out.loc[out.upcast_id >= 0, "w_ms"] < 0).all()


def test_descents_and_rests_are_not_upcasts():
    out = profiler.label_upcasts(_saw(2))
    ids = out.loc[out.upcast_id >= 0, "depth_m"]
    assert ids.max() - ids.min() > 150
    assert (out.loc[out.depth_m.diff() > 0.4, "upcast_id"] == -1).all()


def test_upcast_cut_off_by_end_of_data_is_kept_if_long_enough():
    cut = profiler.label_upcasts(_saw(1).iloc[: 600 + 2500])     # 2500 s at 0.045 m/s ~ 112 m, ends mid-ascent
    assert (cut.upcast_id == 0).any()
    assert cut.index[cut.upcast_id == 0].max() >= len(cut) - 16   # last half-window (7 samples) is edge-biased


def test_short_ascent_below_min_span_is_dropped():
    df = _saw(1, top=120.0)                    # only 65 m of ascent
    assert (profiler.label_upcasts(df).upcast_id == -1).all()


def test_data_gap_inside_ascent_splits_it():
    df = _saw(1)
    a = int(np.argmax(df.depth_m.to_numpy() < 150))
    gapped = df.drop(df.index[a:a + 120]).reset_index(drop=True)      # 2 min hole
    assert set(profiler.label_upcasts(gapped).upcast_id) - {-1} == {0}   # 2nd piece (<100 m?) must not crash


@pytest.mark.parametrize("df", [pd.DataFrame({"time": pd.to_datetime([], utc=True), "depth_m": []}),
                                _saw(1).iloc[:2]])
def test_empty_or_tiny_input_returns_no_upcasts(df):
    out = profiler.label_upcasts(df)
    assert "upcast_id" in out and (out.upcast_id == -1).all()


def test_non_monotonic_time_is_an_explicit_error():
    df = _saw(1)
    df.loc[10, "time"] = df.loc[5, "time"]
    with pytest.raises(ValueError, match="strictly increasing"):
        profiler.label_upcasts(df)


def test_nan_depth_does_not_break_labelling():
    df = _saw(2)
    df.loc[2000:2010, "depth_m"] = np.nan
    out = profiler.label_upcasts(df)
    assert out.upcast_id.max() >= 0


@need("profiler_nc")
def test_real_day_has_nine_upcasts(prof25):
    day = prof25[prof25["time"] < pd.Timestamp("2025-08-19", tz="UTC")].reset_index(drop=True)   # the file runs on into Aug 19
    s = profiler.upcast_summary(profiler.label_upcasts(day))
    assert len(s) == 9
    assert (s["depth_max"] - s["depth_min"] > 170).all()
    assert pd.Timestamp("2025-08-18T16:06", tz="UTC") <= s.loc[6, "start"] <= pd.Timestamp("2025-08-18T16:07", tz="UTC")


def _summ(starts, spans):
    t = pd.to_datetime(starts, utc=True)
    return pd.DataFrame({"start": t, "end": t + pd.Timedelta(hours=1), "depth_min": 5.0, "depth_max": [5.0 + s for s in spans]},
                        index=pd.Index(range(10, 10 + len(starts)), name="upcast_id"))


def test_first_upcasts_skips_earlier_and_incomplete_upcasts():
    from rca_sal.pipeline import first_upcasts
    s = _summ(["2025-08-18T00:00", "2025-08-18T03:00", "2025-08-18T06:00", "2025-08-18T09:00", "2025-08-18T12:00"], [180, 180, 40, 180, 180])
    assert first_upcasts(s, "2025-08-18T01:00Z", 2, 100.0) == [11, 13]       # 10 is before the cast, 12 spans only 40 m


def test_first_upcasts_stops_when_too_few_complete_upcasts():
    from rca_sal.pipeline import first_upcasts
    s = _summ(["2025-08-18T00:00", "2025-08-18T03:00"], [180, 40])
    with pytest.raises(ValueError, match=r"only 1 complete upcast.*incomplete: \{11: 40\.0\}"):
        first_upcasts(s, "2025-08-17T00:00Z", 2, 100.0)
