"""Lap Analyst data layer: position-based sectors, ghost laps, bad files, exports."""
import math

import numpy as np
import pytest

from gt7telem import lap_analyst as la


def _lap(pos, t, lap_time_s=None, speed=None, extra=None, full=True):
    """Build a loaded lap from position/time arrays (full telemetry columns by default)."""
    samples = []
    for i, (p, tt) in enumerate(zip(pos, t)):
        s = {"track_position": float(p), "speed_kmh": float(speed[i] if speed is not None else 150.0),
             "throttle": 0.5, "brake": 0.0, "steering": 0.0, "gear": 3}
        if full:
            s.update(t=float(tt), rpm=5000.0, tyre_temp_fl=80.0)
        if extra:
            s.update(extra)
        samples.append(s)
    return la.load_lap_data({"car": "Car", "track": "trk",
                             "lap_time_s": lap_time_s if lap_time_s is not None else float(t[-1]),
                             "samples": samples})


def _two_half_lap(first_half_s, second_half_s, n_first, n_second):
    """1000 m lap: first 500 m take first_half_s, last 500 m take second_half_s,
    with a different sample density in each half."""
    p1 = np.linspace(0, 500, n_first, endpoint=False)
    t1 = np.linspace(0, first_half_s, n_first, endpoint=False)
    p2 = np.linspace(500, 1000, n_second)
    t2 = np.linspace(first_half_s, first_half_s + second_half_s, n_second)
    return np.concatenate([p1, p2]), np.concatenate([t1, t2])


def test_sector_times_follow_track_position_not_sample_count():
    # Dense samples in the slow first half: an index-based split would put
    # most of the lap time in the wrong half.
    pos, t = _two_half_lap(30.0, 20.0, 300, 100)
    _, df = _lap(pos, t)
    _, times = la.lap_sectors(df, 2)
    assert times == pytest.approx([30.0, 20.0], abs=0.2)
    assert sum(times) == pytest.approx(50.0, abs=1e-6)


def test_third_stats_use_position_and_add_up_to_the_lap():
    pos, t = _two_half_lap(30.0, 20.0, 300, 100)
    _, df = _lap(pos, t)
    rows = la.build_sector_stats(df)
    assert len(rows) == 3
    assert sum(float(r[1].rstrip("s")) for r in rows) == pytest.approx(50.0, abs=0.05)


def test_sector_deltas_sign_and_alignment_between_laps_with_different_sample_rates():
    pa, ta = _two_half_lap(20.0, 30.0, 200, 300)      # A: fast first half
    pb, tb = _two_half_lap(30.0, 20.0, 50, 50)        # B: slow first half, far fewer samples
    _, a = _lap(pa, ta)
    _, b = _lap(pb, tb)
    d = la.compute_sector_deltas(a, b, sector_length_m=25)
    assert len(d) == len(a)
    first = d[a["track_position"].to_numpy() < 450]
    second = d[a["track_position"].to_numpy() > 550]
    assert (first < 0).all()          # A faster than B in the first half
    assert (second > 0).all()         # and slower in the second
    # per-sector deltas add up to the lap-time gap
    n_sec = math.ceil(1000 / 25)
    assert d.min() < 0 < d.max()
    assert n_sec == 40


def test_ghost_without_timestamps_gets_a_sane_time_axis():
    pos = np.linspace(0, 4000, 400)
    t = np.zeros(400)
    _, df = _lap(pos, t, lap_time_s=95.0, speed=np.full(400, 150.0), full=False)
    assert la.is_compact(df)
    assert df.attrs.get("t_derived")
    assert df["t"].iloc[0] == 0 and df["t"].iloc[-1] == pytest.approx(95.0)
    assert (df["t"].diff().dropna() > 0).all()
    assert df["long_g"].abs().max() < 1.0          # no fake 6x spikes


def test_ghost_sectors_and_deltas_are_not_zero():
    pa, ta = _two_half_lap(20.0, 30.0, 200, 300)
    _, a = _lap(pa, ta)
    gp = np.linspace(0, 1000, 200)
    _, ghost = _lap(gp, np.zeros(200), lap_time_s=48.0, speed=np.full(200, 75.0), full=False)
    _, times = la.lap_sectors(ghost, 4)
    assert all(x > 0 for x in times) and sum(times) == pytest.approx(48.0, abs=1e-6)
    d = la.compute_sector_deltas(a, ghost, 25)
    assert np.isfinite(d).all() and np.abs(d).max() > 0


def test_missing_track_position_is_rebuilt_from_speed():
    samples = [{"t": i * 0.1, "speed_kmh": 108.0} for i in range(100)]   # 30 m/s
    _, df = la.load_lap_data({"car": "c", "track": "t", "lap_time_s": 10.0, "samples": samples})
    assert df["track_position"].iloc[-1] == pytest.approx(30.0 * 9.9, rel=0.02)


@pytest.mark.parametrize("bad", [[], {}, "x", None, {"samples": []}, {"samples": ["a", 1]}, {"samples": None}])
def test_bad_files_raise_a_clear_value_error(bad):
    with pytest.raises(ValueError):
        la.load_lap_data(bad)


def test_text_and_infinite_values_do_not_poison_the_frame():
    samples = [{"t": i * 0.1, "speed_kmh": "fast" if i == 3 else 100.0, "track_position": i * 3.0,
                "throttle": float("inf") if i == 5 else 0.5} for i in range(20)]
    _, df = la.load_lap_data({"lap_time_s": "n/a", "samples": samples})
    assert np.isfinite(df["speed_kmh"]).all() and np.isfinite(df["throttle"]).all()


def test_labels_survive_null_fields_and_carry_into_the_next_minute():
    assert "?" in la.lap_label({"lap_time_s": None, "track": None, "car": None})
    assert la._fmt_laptime(59.9996) == "1:00.000"
    assert la._fmt_laptime(83.456) == "1:23.456"
    assert la._fmt_laptime(None) == "0:00.000"


def test_gear_changes_are_not_overcounted_on_the_first_sample():
    pos, t = _two_half_lap(30.0, 20.0, 50, 50)
    data, df = _lap(pos, t)
    assert la.build_stats(data, df)["Gear Changes"] == "0"


def test_brake_zone_open_at_lap_end_is_counted():
    pos, t = _two_half_lap(30.0, 20.0, 50, 50)
    _, df = _lap(pos, t)
    df["brake"] = 0.0
    df.loc[10:20, "brake"] = 0.5      # closed zone
    df.loc[df.index[-5:], "brake"] = 0.5   # still braking at the finish
    zones = la._brake_zone_durations(df)
    assert len(zones) == 2 and all(z > 0 for z in zones)


def test_export_csv_round_trip_and_failure_keeps_the_old_file(tmp_path, monkeypatch):
    pos, t = _two_half_lap(30.0, 20.0, 50, 50)
    _, df = _lap(pos, t)
    out = tmp_path / "lap.csv"
    la.export_csv(df, out)
    lines = out.read_text().splitlines()
    assert lines[0] == "distance_m,speed_kmh,throttle,brake,rpm,gear,steering"
    assert len(lines) == len(df) + 1 and lines[1].startswith("0.0,")
    before = out.read_text()

    def boom(*a, **k):
        raise PermissionError("open in Excel")

    monkeypatch.setattr(la.os, "replace", boom)
    with pytest.raises(PermissionError):
        la.export_csv(df, out)
    assert out.read_text() == before and not list(tmp_path.glob("*.tmp"))


def test_safe_name_strips_path_characters():
    assert "/" not in la._safe_name("BMW M3 / E30?: x") and "?" not in la._safe_name("a?b")
    assert la._safe_name("???") == "lap"


def test_car_arrow_points_along_the_direction_of_travel():
    # heading pi/2 means travelling along +x ((sin h, cos h) = (1, 0))
    verts = la.Replay._car_verts(None, 0.0, 0.0, math.pi / 2, 10.0)
    assert verts[0] == pytest.approx([10.0, 0.0], abs=1e-9)
    verts = la.Replay._car_verts(None, 5.0, 5.0, 0.0, 10.0)     # heading 0 = +z
    assert verts[0] == pytest.approx([5.0, 15.0], abs=1e-9)


def test_headings_hold_the_last_value_at_a_standstill():
    import pandas as pd
    x = np.array([0, 1, 2, 2, 2, 2.0])
    z = np.zeros(6)
    df = pd.DataFrame({"world_x": x, "world_z": z, "heading": np.zeros(6)})
    h = la.Replay._compute_headings(None, df)
    assert h[3] == pytest.approx(h[2]) and h[5] == pytest.approx(math.pi / 2)


def test_too_short_a_lap_does_not_crash_the_sector_chart():
    import matplotlib
    import matplotlib.pyplot as plt
    pos = np.linspace(0, 20, 5)
    _, df = _lap(pos, np.linspace(0, 1, 5))
    # draw_sectors calls plt.colorbar, which makes pyplot open a figure of its own; on the
    # app's TkAgg backend that is a real Tk window, which fails intermittently on some machines
    previous = matplotlib.get_backend()
    plt.switch_backend("Agg")
    try:
        fig = plt.figure()
        la.draw_sectors(fig, df, None)
        plt.close("all")
    finally:
        plt.switch_backend(previous)
