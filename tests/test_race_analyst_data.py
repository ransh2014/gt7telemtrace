import json

import numpy as np

from gt7telem import race_analyst as ra


def _race(tmp_path, n=1200, hz=60, refuel=True):
    samples = []
    fuel = 80.0
    for i in range(n):
        in_stop = refuel and 600 <= i < 660
        if in_stop:
            fuel += 0.1            # 60 Hz ramp: far below any per-sample threshold
        else:
            fuel -= 0.002
        samples.append({
            "t": i / hz, "speed_kmh": 40.0 if in_stop else 150.0,
            "fuel_remaining": fuel, "lap_number": 1 + i // 400,
            "throttle": 0.5, "brake": 0.0, "gear": 3, "rpm": 5000,
            "world_x": float(i), "world_z": 0.0,
        })
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"car": "x", "track": "y", "race_duration_s": n / hz,
                             "samples": samples}), encoding="utf-8-sig")
    return ra.load_race(p)


def test_fmt_dur_carry_and_bad_values():
    assert ra.fmt_dur(59.9996) == "1:00.000"
    assert ra.fmt_dur(None) == "--"
    assert ra.fmt_dur(float("nan")) == "--"
    assert ra.fmt_dur(65.5) == "1:05.500"


def test_refuel_ramp_detected_and_counted(tmp_path):
    _, df = _race(tmp_path)
    assert ra.count_pit_stops(df) == 1
    assert ra.build_stats(_, df)["Pit Stops"] == "1"


def test_fuel_used_is_sum_of_drops(tmp_path):
    data, df = _race(tmp_path)
    used = float(ra.build_stats(data, df)["Fuel Used"])
    assert 2.0 < used < 2.5      # ~1140 samples * 0.002, refuel doesn't cancel it


def test_lap_segments_vectorised(tmp_path):
    _, df = _race(tmp_path)
    segs = ra.get_lap_segments(df)
    assert [int(s["lap_number"].iloc[0]) for s in segs] == [1, 2, 3]


def test_export_csv_atomic(tmp_path):
    _, df = _race(tmp_path)
    out = tmp_path / "o.csv"
    ra.export_csv(df, out)
    lines = out.read_text().splitlines()
    assert lines[0].startswith("distance_m,speed_kmh")
    assert len(lines) == len(df) + 1
    assert not (tmp_path / "o.csv.part").exists()


def test_car_arrow_points_along_heading():
    rep = ra.Replay.__new__(ra.Replay)
    v = rep._car_verts(0.0, 0.0, np.pi / 2, 10)   # heading +x
    assert v[0][0] > 9 and abs(v[0][1]) < 1e-6


def test_safe_name():
    assert "/" not in ra._safe_name("a/b:c*d")
    assert ra._safe_name("") == "race"
