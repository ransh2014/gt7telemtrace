"""dyno.py rebuilt from a simulated car whose true power curve and gearbox are known."""
import math

import numpy as np
import pandas as pd

from gt7telem import dyno

MASS = 1400.0
C0, C1 = 0.12, 3e-4
K = {1: 300.0, 2: 200.0, 3: 150.0, 4: 115.0, 5: 92.0, 6: 76.0}     # rpm per m/s
REV = 8000.0
PEAK_RPM = 6500.0
PEAK_W = 260.0                                                     # W/kg at the wheels


def true_power(r):
    x = (r - PEAK_RPM) / 2800.0
    return PEAK_W * max(0.25, 1.0 - 0.9 * x * x) if r < PEAK_RPM else PEAK_W * max(0.5, 1.0 - 0.7 * x * x)


def sim(shift_at=7400.0, dt=1 / 60):
    rows, t, v, g = [], 0.0, 6.0, 1
    def row(thr, brk, rpm, v, g):
        return dict(t=t, speed_kmh=v * 3.6, rpm=rpm, gear=g, throttle=thr, brake=brk,
                    vel_x=v, vel_y=0.0, vel_z=0.0, ang_y=0.0, tyre_slip_fl=1.0, tyre_slip_fr=1.0,
                    tyre_slip_rl=1.0, tyre_slip_rr=1.0, rpm_limiter=REV, boost=0.0)
    while True:                                   # full throttle through the gears
        rpm = K[g] * v
        if rpm >= shift_at and g < 6:
            for _ in range(15):                   # a 0.25 s shift: torque cut, no drive
                rows.append(row(1.0, 0.0, K[g + 1] * v, v, g + 1)); t += dt
                v -= (C0 + C1 * v * v) * dt
            g += 1
            continue
        rows.append(row(1.0, 0.0, rpm, v, g)); t += dt
        f = true_power(rpm) / v - (C0 + C1 * v * v)
        v += f * dt
        if rpm >= REV or f <= 0.02 or len(rows) > 20000:
            break
    for _ in range(600):                          # lift and coast down
        rows.append(row(0.0, 0.0, K[g] * v, v, g)); t += dt
        v -= (C0 + C1 * v * v) * dt
        if v < 15:
            break
    return pd.DataFrame(rows)


def test_rebuilds_power_curve_and_gearbox():
    res = dyno.analyse(sim(), gear_ratios=[3.2, 2.1, 1.6, 1.25, 1.0, 0.82, 0, 0], mass_kg=MASS)
    assert res["ok"], res["msg"]
    assert res["resist"]["src"] == "fitted"
    assert abs(res["resist"]["c1"] - C1) / C1 < 0.15
    assert abs(res["peak_wkg"] - PEAK_W) / PEAK_W < 0.08
    assert abs(res["peak_rpm"] - PEAK_RPM) < 700
    assert abs(res["peak_p_disp"] - PEAK_W * MASS / dyno.HP_W) / (PEAK_W * MASS / dyno.HP_W) < 0.08
    for gd in res["gears"]:
        assert abs(gd["k"] - K[gd["g"]]) / K[gd["g"]] < 0.02
        assert abs(gd["top_kmh"] - REV / K[gd["g"]] * 3.6) < 3.0


def test_without_weight_reports_power_to_weight():
    res = dyno.analyse(sim())
    assert res["ok"] and res["mass"] is None and res["unit_p"] == "hp/t"
    assert abs(res["peak_p_disp"] - PEAK_W * 1000 / dyno.HP_W) / (PEAK_W * 1000 / dyno.HP_W) < 0.08


def test_shift_point_is_after_the_power_peak():
    res = dyno.analyse(sim())
    opts = [gd["opt_shift"] for gd in res["gears"] if gd["opt_shift"]]
    assert opts and all(res["peak_rpm"] <= o <= res["rev_lim"] + 1 for o in opts)


def test_unusable_laps_say_why():
    assert dyno.analyse(None)["ok"] is False
    short = sim().iloc[:50]
    assert dyno.analyse(short)["ok"] is False and short is not None
    flat = sim().assign(throttle=0.2)
    assert "full-throttle" in dyno.analyse(flat)["msg"]


def test_early_shifting_is_flagged():
    df = sim(shift_at=5200.0)
    res = dyno.analyse(df)
    hints = dyno.advise(df, res)
    assert any(h["area"] in ("Shifting", "Dyno") for h in hints)


def test_understeer_entry_is_flagged():
    df = sim()
    n = len(df)
    df["wheel_base"] = 2.6
    yaw = 0.25
    v = df["speed_kmh"].to_numpy() / 3.6
    df["ang_y"] = yaw
    neutral = 2.6 * yaw / np.maximum(v, 1.0)
    df["wheel_steering_angle_l"] = neutral * 2.0
    df["wheel_steering_angle_r"] = neutral * 2.0
    df["throttle"] = 0.0
    df["brake"] = 0.4
    bal = dyno._balance(df, v)
    assert bal and bal["entry"][0] > 1.8
    hints = dyno.advise(df, None)
    assert any(h["area"] == "Balance" and "entry" in h["finding"] for h in hints) and n > 0


def test_weight_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(dyno, "_weights_path", lambda: tmp_path / "w.json")
    assert dyno.load_weight("Some Car") is None
    assert dyno.save_weight("Some Car", 1234.5)
    assert math.isclose(dyno.load_weight("some car"), 1234.5)
    assert not dyno.save_weight("Some Car", 12)


def test_dyno_tab_draws_from_a_loaded_lap():
    import pytest
    pytest.importorskip("tkinter")
    from matplotlib.figure import Figure

    from gt7telem import lap_analyst
    raw = sim()
    data = {"car": "Test Car", "lap_time_s": 60.0, "tyre_radius_m": 0.33,
            "gear_ratios": [3.2, 2.1, 1.6, 1.25, 1.0, 0.82, 0.0, 0.0],
            "samples": raw.to_dict("records")}
    _d, df = lap_analyst.load_lap_data(data)
    assert df.attrs["car"] == "Test Car" and df.attrs["gear_ratios"][0] == 3.2
    assert df.attrs["tyre_radius_m"] == 0.33
    df.attrs["mass_kg"] = MASS
    fig = Figure()
    lap_analyst.draw_dyno(fig, df, df)             # Lap B = same lap: exercises the compare path
    assert len(fig.axes) >= 4
    assert any(name == "Dyno & Tune" for name, *_ in lap_analyst.GROUPS)
