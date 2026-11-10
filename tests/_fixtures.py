"""Deterministic full-telemetry laps and races for chart / parity tests (every channel the chart groups read)."""
import numpy as np

HZ = 10.0
LAP_M = 3600.0
APEXES = (500.0, 1300.0, 2200.0, 3000.0)


def _lap_samples(lap_no=1, t0=0.0, slow=0.0, n=720, race=False):
    pos = np.linspace(0, LAP_M, n)
    spd = np.full(n, 190.0)
    for a in APEXES:
        spd -= np.clip(100 - np.abs(pos - a) * 0.7, 0, None)
    spd = np.clip(spd - slow, 40, None)
    dt = np.concatenate([[0.0], np.diff(pos) / (spd[1:] / 3.6)])
    t = t0 + np.cumsum(dt)
    th = pos / LAP_M * 2 * np.pi
    x, z = 450 * np.cos(th), 280 * np.sin(th)
    y = 6 * np.sin(2 * th)
    hdg = np.unwrap(np.arctan2(np.gradient(x), np.gradient(z)))
    yaw = np.gradient(hdg, t)
    near = np.min([np.abs(pos - a) for a in APEXES], axis=0)
    brake = np.where((near < 150) & (near > 30) & (pos < np.array([min(APEXES, key=lambda a: abs(a - p)) for p in pos])), 0.8, 0.0)
    thr = np.where(near > 60, 1.0, 0.25)
    gear = np.clip((spd // 45).astype(int) + 1, 1, 6)
    rpm = 3000 + (spd % 45) * 100
    rows = []
    for i in range(n):
        s = {
            "t": round(float(t[i]), 3), "track_position": float(pos[i]),
            "world_x": float(x[i]), "world_y": float(y[i]), "world_z": float(z[i]), "heading": float(hdg[i]),
            "speed_kmh": float(spd[i]), "throttle": float(thr[i]), "brake": float(brake[i]),
            "steering": float(np.sign(yaw[i]) * min(1.0, abs(yaw[i]) * 3)), "gear": int(gear[i]),
            "suggested_gear": int(gear[i]), "rpm": float(rpm[i]), "max_rpm": 8000.0, "rpm_limiter": 7800.0,
            "rpm_warning": 7000.0, "boost": 0.3, "clutch": 0.0, "clutch_engaged": 1.0, "rpm_after_clutch": float(rpm[i]),
            "tyre_temp_fl": 80 + 6 * np.sin(th[i]), "tyre_temp_fr": 82 + 5 * np.sin(th[i]),
            "tyre_temp_rl": 84 + 4 * np.sin(th[i]), "tyre_temp_rr": 85 + 3 * np.sin(th[i]),
            "tyre_slip_fl": 1.0 + 0.02 * np.sin(th[i]), "tyre_slip_fr": 1.0, "tyre_slip_rl": 1.03, "tyre_slip_rr": 1.02,
            "susp_fl": 0.09 + 0.01 * np.sin(th[i]), "susp_fr": 0.09, "susp_rl": 0.1, "susp_rr": 0.1,
            "ride_height_mm": 60.0, "fuel_remaining": float(max(0.0, 80.0 - 2.0 * (lap_no - 1) - 2.0 * pos[i] / LAP_M)),
            "fuel_capacity": 100.0, "oil_temp": 95.0, "water_temp": 88.0, "oil_pressure": 4.5,
            "ang_x": 0.0, "ang_y": float(yaw[i]), "ang_z": 0.0,
            "vel_x": float(spd[i] / 3.6 * np.sin(hdg[i])), "vel_y": 0.0, "vel_z": float(spd[i] / 3.6 * np.cos(hdg[i])),
            "surface_type": "road",
        }
        if race:
            s.update(lap_number=lap_no, current_position=3, total_positions=8, in_pit=0)
        rows.append(s)
    return rows


def full_lap_data():
    s = _lap_samples()
    return {"lap_time_s": s[-1]["t"], "car": "Test Car", "track": "Test Ring", "samples": s,
            "gear_ratios": [3.2, 2.2, 1.6, 1.25, 1.0, 0.82], "tyre_radius_m": 0.33}


def slower_lap_data():
    s = _lap_samples(slow=18.0)
    return {"lap_time_s": s[-1]["t"], "car": "Test Car", "track": "Test Ring", "samples": s,
            "gear_ratios": [3.2, 2.2, 1.6, 1.25, 1.0, 0.82], "tyre_radius_m": 0.33}


def full_race_data(laps=4):
    rows, t0 = [], 0.0
    for lap in range(1, laps + 1):
        s = _lap_samples(lap_no=lap, t0=t0, slow=4.0 * (lap % 2), race=True)
        rows += s
        t0 = s[-1]["t"] + 0.1
    return {"car": "Test Car", "track": "Test Ring", "total_laps": laps, "samples": rows,
            "gear_ratios": [3.2, 2.2, 1.6, 1.25, 1.0, 0.82], "tyre_radius_m": 0.33}
