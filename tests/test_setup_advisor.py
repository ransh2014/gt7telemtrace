import numpy as np
import pandas as pd

from gt7telem import setup_advisor as sa


def _lap(yaw_gain=1.0, spin=1.0, ride=50.0, n=400, angle=True):
    """A lap that steers in a sine wave. yaw_gain<1 = understeer, >1 = oversteer."""
    t = np.arange(n) * 0.05
    v = np.full(n, 30.0)                       # 108 km/h
    ang = 0.08 * np.sin(t * 0.8)               # road-wheel angle, rad
    wb = 2.6
    expect = v * np.tan(ang) / wb
    df = pd.DataFrame({
        "t": t, "speed_kmh": v * 3.6, "throttle": np.where(np.sin(t * 0.8 + 1) > 0, 0.9, 0.0),
        "brake": np.where(np.sin(t * 0.8 + 1) <= -0.5, 0.5, 0.0),
        "ang_y": expect * yaw_gain, "tyre_slip_fl": 1.0, "tyre_slip_fr": 1.0,
        "tyre_slip_rl": spin, "tyre_slip_rr": spin, "ride_height_mm": ride,
    })
    if angle:
        df["wheel_steering_angle"] = [(a, a) for a in ang]
        df["wheel_base"] = wb
    return df


def _balance(r):
    return [f for f in r["findings"] if f.key.startswith(("under", "over"))]


def test_balanced_car_has_no_balance_findings():
    r = sa.diagnose(_lap())
    assert r["ok"] and not _balance(r)


def test_understeer_found_with_fixes():
    r = sa.diagnose(_lap(yaw_gain=0.5))
    f = [f for f in _balance(r) if f.key.startswith("under")]
    assert f and "ARB" in " ".join(f[0].fixes)


def test_oversteer_found():
    r = sa.diagnose(_lap(yaw_gain=1.8))
    assert any(f.key.startswith("over") for f in _balance(r))


def test_wheelspin_and_bottoming():
    r = sa.diagnose(_lap(spin=1.4, ride=2.0))
    keys = {f.key for f in r["findings"]}
    assert "wheelspin" in keys and "bottoming" in keys


def test_missing_angle_data_is_reported_not_guessed():
    r = sa.diagnose(_lap(yaw_gain=0.4, angle=False))
    assert r["ok"] and "can't be measured" in r["msg"]
    assert not _balance(r)


def test_short_lap_not_ok():
    assert not sa.diagnose(_lap(n=20))["ok"]
    assert not sa.diagnose(None)["ok"]
    assert sa.describe(sa.diagnose(None))


def test_ab_better_when_understeer_fixed():
    before = [_lap(yaw_gain=0.5) for _ in range(3)]
    after = [_lap(yaw_gain=1.0) for _ in range(3)]
    r = sa.compare_ab(before, after, [90.2, 90.4, 90.3], [89.8, 89.9, 89.7])
    assert r["verdict"] == "better" and r["better"]


def test_ab_worse_and_needs_laps():
    r = sa.compare_ab([_lap()] * 3, [_lap(yaw_gain=0.5)] * 3)
    assert r["verdict"] == "worse"
    assert "at least two" in sa.compare_ab([_lap()], [_lap()])["text"]
