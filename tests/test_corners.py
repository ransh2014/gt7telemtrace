import numpy as np
import pandas as pd

from gt7telem import corners

APEXES = (600.0, 1500.0, 2400.0)


def _lap(brake_shift=0.0, throttle_shift=0.0, speed_drop=0.0):
    """Synthetic 3 km lap, 3 corners. Shifts move the brake / throttle points."""
    pos = np.arange(0, 3000, 5.0)
    spd = np.full_like(pos, 200.0)
    lat = np.zeros_like(pos)
    brk = np.zeros_like(pos)
    thr = np.ones_like(pos)
    for apex in APEXES:
        dist = np.abs(pos - apex)
        spd -= np.clip(120 - dist * 0.8, 0, None) - 0  # V-shaped dip, 120 km/h deep
        lat[dist < 60] = 1.2
        b0 = apex - 150 + brake_shift
        brk[(pos >= b0) & (pos < apex - 10)] = 0.8
        thr[(pos >= b0) & (pos < apex + 20 + throttle_shift)] = 0.0
    spd = spd - speed_drop * (lat > 0)
    t = np.concatenate([[0.0], np.cumsum(5.0 / (spd[1:] / 3.6))])
    return pd.DataFrame(
        {
            "track_position": pos,
            "speed_kmh": spd,
            "throttle": thr,
            "brake": brk,
            "lat_g": lat,
            "t": t,
            "steering": np.zeros_like(pos),
        }
    )


def test_detects_three_corners():
    cs = corners.detect_corners(_lap())
    assert len(cs) == 3
    for c, apex in zip(cs, APEXES):
        assert abs(c.apex - apex) < 30
        assert c.brake_pos is not None and c.brake_pos < c.apex
        assert c.throttle_pos is not None and c.throttle_pos > c.apex


def test_identical_laps_have_no_deltas():
    _, _, deltas = corners.match_corners(_lap(), _lap())
    assert len(deltas) == 3
    for d in deltas:
        assert d.brake_m == 0 and d.throttle_m == 0
        assert abs(d.min_speed_kmh) < 1e-6 and abs(d.time_s) < 1e-6


def test_later_braking_is_positive():
    _, _, deltas = corners.match_corners(_lap(brake_shift=20), _lap())
    assert all(d.brake_m is not None and 15 <= d.brake_m <= 25 for d in deltas)


def test_slower_corner_loses_time():
    _, _, deltas = corners.match_corners(_lap(speed_drop=15), _lap())
    assert all(d.min_speed_kmh < 0 and d.time_s > 0 for d in deltas)


def test_late_braker_label():
    df = _lap(brake_shift=20)
    cs, _, deltas = corners.match_corners(df, _lap())
    assert corners.session_label(df, cs, deltas).startswith("Late braker")


def test_label_without_reference():
    df = _lap()
    assert corners.session_label(df, corners.detect_corners(df), []) != ""


def test_unusable_lap_is_empty_not_a_crash():
    assert corners.detect_corners(pd.DataFrame({"speed_kmh": [1, 2, 3]})) == []
    assert corners.detect_corners(None) == []
    assert corners.match_corners(_lap(), pd.DataFrame())[2] == []
