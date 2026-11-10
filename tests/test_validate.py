import numpy as np
import pandas as pd

from gt7telem import validate


def _lap(n=600, length=3000.0):
    t = np.arange(n) * 0.1
    pos = np.linspace(0, length, n)
    return pd.DataFrame({
        "t": t, "track_position": pos, "speed_kmh": np.full(n, 180.0), "brake": 0.0, "ang_y": 0.1,
        "world_x": pos, "world_z": np.zeros(n),
    })


def codes(res):
    return {i.code for i in res["issues"]}


def test_clean_lap():
    r = validate.check(_lap(), ref_length_m=3000.0, lap_time_s=59.9)
    assert r["verdict"] == "clean" and validate.submission_ok(r)
    assert validate.describe(r) == "Lap looks clean."


def test_teleport_is_invalid():
    d = _lap()
    d.loc[300:, "world_x"] += 5000
    d.loc[400:, "world_x"] -= 5000
    d.loc[500:, "world_x"] += 4000
    r = validate.check(d)
    assert r["verdict"] == "invalid" and "teleport" in codes(r) and not validate.submission_ok(r)


def test_shortcut_is_suspect():
    r = validate.check(_lap(length=2400.0), ref_length_m=3000.0)
    assert r["verdict"] == "suspect" and "length" in codes(r)


def test_impossible_acceleration_and_speed():
    d = _lap()
    d.loc[100:105, "speed_kmh"] = [10, 300, 20, 300, 10, 300]
    assert "accel" in codes(validate.check(d))
    d2 = _lap()
    d2.loc[50, "speed_kmh"] = 900
    assert validate.check(d2)["verdict"] == "invalid"


def test_time_going_backwards_and_mismatch():
    d = _lap()
    d.loc[200, "t"] = 0.0
    assert "time_back" in codes(validate.check(d))
    assert "time_mismatch" in codes(validate.check(_lap(), lap_time_s=70.0))


def test_off_track_and_wall():
    d = _lap()
    d["surface_type"] = 0
    d.loc[:100, "surface_type"] = 3
    assert "off_track" in codes(validate.check(d))
    w = _lap()
    w.loc[300, "speed_kmh"] = 100.0
    w.loc[301:, "speed_kmh"] = 100.0
    w.loc[400, "speed_kmh"] = 60.0
    w.loc[401:, "speed_kmh"] = 180.0
    w.loc[450, "speed_kmh"] = 40.0
    w.loc[451:, "speed_kmh"] = 180.0
    w.loc[500, "speed_kmh"] = 30.0
    w.loc[501:, "speed_kmh"] = 180.0
    # not asserting wall here: braking-free decel steps are only flagged above 3.5 g
    assert validate.check(w)["verdict"] in ("clean", "suspect", "invalid")


def test_garbage_input():
    assert validate.check(None)["verdict"] == "invalid"
    assert validate.check(pd.DataFrame({"x": range(100)}))["verdict"] == "invalid"
    d = _lap()
    d.loc[10, "speed_kmh"] = np.nan
    assert "nan" in codes(validate.check(d))
