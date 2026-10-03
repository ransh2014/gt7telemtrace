"""Tests for udp.py's per-packet state tracking: race start/end detection,
per-lap track_position, diagnostics on an IP change, and console discovery.

None of these need pycryptodome or a console -- they drive the functions
directly with synthetic input, with udp's module state isolated per test.
"""
import socket
import struct
from collections import deque
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from gt7telem import udp


@pytest.fixture
def race(monkeypatch):
    """Fresh race-detection state and a fake clock. Returns (drive, fired):
    drive(lap, total, speed, seconds) feeds 60 Hz packets; fired collects
    race_start / race_end in order."""
    clock = [1000.0]
    monkeypatch.setattr(udp, "time", SimpleNamespace(time=lambda: clock[0]))
    for name, value in {
        "_race_active": False, "_speed_hold_start": None, "_prev_paused": False,
        "_prev_loading": False, "_grid_start_armed_until": 0.0, "_event_callbacks": {},
        "_debug_prev_state": {}, "_incidents": [], "_fuel_hist": deque(),
        "_last_incident_t": {}, "_last_mix_change_t": None, "_loading_since": None,
    }.items():
        monkeypatch.setattr(udp, name, value)

    fired = []
    for event in ("race_start", "race_end"):
        udp.register_event(event, lambda parsed, event=event: fired.append(event))

    def drive(lap, total, speed, seconds, hz=60, loading=False):
        for _ in range(int(seconds * hz)):
            udp._check_events({
                "paused": False, "car_on_track": True, "loading": loading,
                "speed_kmh": speed, "lap_number": lap, "total_laps": total,
                "track_position": 0.0, "fuel_remaining": 50.0,
            })
            clock[0] += 1 / hz

    return drive, fired


def test_race_ends_once_even_if_the_car_keeps_driving_at_speed(race):
    """Past the flag the car is still on track and often still fast on the
    cool-down lap; the rolling-start check used to re-fire a start/end pair
    every 2 s of that, each one a near-empty saved race."""
    drive, fired = race
    drive(lap=3, total=3, speed=150, seconds=5)
    drive(lap=4, total=3, speed=120, seconds=10)
    assert fired == ["race_start", "race_end"]


def test_the_next_race_still_starts_after_a_finished_one(race):
    drive, fired = race
    drive(lap=3, total=3, speed=150, seconds=5)
    drive(lap=4, total=3, speed=120, seconds=5)
    drive(lap=1, total=5, speed=150, seconds=5)
    assert fired == ["race_start", "race_end", "race_start"]


def test_time_trial_driving_never_starts_a_race(race):
    """Time trial / practice report total_laps == 0. A rolling start used to
    fire there after 2 s at 80 km/h and the "race" never ended."""
    drive, fired = race
    drive(lap=1, total=0, speed=200, seconds=30)
    drive(lap=2, total=0, speed=200, seconds=30)
    assert fired == []
    assert udp._race_active is False


def test_quitting_mid_race_ends_it_and_the_next_race_starts(race):
    drive, fired = race
    drive(lap=2, total=5, speed=150, seconds=5)
    drive(lap=2, total=5, speed=0, seconds=0.5, loading=True)   # brief blip: still racing
    assert fired == ["race_start"]
    drive(lap=2, total=5, speed=0, seconds=2, loading=True)     # quit to menu
    assert fired == ["race_start", "race_end"]
    drive(lap=1, total=3, speed=150, seconds=5)
    assert fired == ["race_start", "race_end", "race_start"]


def test_is_connected_goes_false_when_packets_stop(monkeypatch):
    now = [500.0]
    monkeypatch.setattr(udp.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(udp, "_connected", True)
    monkeypatch.setattr(udp, "_last_packet_mono", 500.0)
    assert udp.is_connected()
    now[0] += udp.STALE_AFTER - 0.1
    assert udp.is_connected()
    now[0] += 0.2
    assert not udp.is_connected()


def _plain_packet(lap, x):
    """A decrypted (plaintext) packet carrying just world X, speed and the
    lap counter -- enough for _parse's distance tracking."""
    pt = bytearray(368)
    struct.pack_into("<f", pt, 0x04, x)       # world_x
    struct.pack_into("<f", pt, 0x4C, 50.0)    # speed, m/s
    struct.pack_into("<h", pt, 0x74, lap)     # current lap
    return bytes(pt)


@pytest.fixture
def fresh_position(monkeypatch):
    for name, value in {"_prev_x": None, "_prev_z": None, "_prev_heading": None,
                        "_cum_dist": 0.0, "_prev_lap_number": None}.items():
        monkeypatch.setattr(udp, name, value)


def test_track_position_restarts_when_the_lap_counter_changes(fresh_position):
    """It used to reset only from the Dashboard's Record Lap code, so without
    a lap recording it grew across laps and broke the live delta."""
    for x in (0.0, 10.0, 20.0, 30.0):
        parsed = udp._parse(_plain_packet(lap=1, x=x))
    assert parsed["track_position"] == pytest.approx(30.0)
    # The step across the line counts toward the new lap.
    parsed = udp._parse(_plain_packet(lap=2, x=40.0))
    assert parsed["track_position"] == pytest.approx(10.0)
    parsed = udp._parse(_plain_packet(lap=2, x=55.0))
    assert parsed["track_position"] == pytest.approx(25.0)


@pytest.fixture
def fresh_connection(monkeypatch):
    monkeypatch.setattr(udp, "_diag", dict(udp._diag, bind_error=None))
    for name, value in {"_ps4_ip": "10.0.0.1", "_connected": False,
                        "_source": None, "_latest": {}}.items():
        monkeypatch.setattr(udp, name, value)


def test_switching_console_ip_clears_the_old_consoles_counters(fresh_connection):
    """Leftover packets_received from the old console made get_last_error()
    return None -- "unknown error" -- when the new IP was simply wrong."""
    udp._diag_update(packets_received=50, decrypt_failures=3, heartbeats_sent=9)
    udp.set_ip("10.0.0.2")
    diag = udp.get_diagnostics()
    assert diag["packets_received"] == diag["decrypt_failures"] == diag["heartbeats_sent"] == 0
    assert "received 0 packets" in udp.get_last_error()


def test_switching_console_ip_keeps_the_port_bind_error(fresh_connection):
    udp._diag_update(bind_error="Port 33740 is already in use")
    udp.set_ip("10.0.0.2")
    assert udp.get_diagnostics()["bind_error"] == "Port 33740 is already in use"


def test_reconnecting_to_the_same_ip_keeps_the_counters(fresh_connection):
    udp._diag_update(packets_received=5)
    udp.set_ip(" 10.0.0.1 ")
    assert udp.get_diagnostics()["packets_received"] == 5
    assert udp.get_ip() == "10.0.0.1"


class _FakeSocket:
    def __init__(self, replies):
        self.sent, self._replies = [], list(replies)

    def setsockopt(self, *args):
        pass

    def settimeout(self, timeout):
        pass

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def recvfrom(self, bufsize):
        if not self._replies:
            raise socket.timeout
        return self._replies.pop(0)

    def close(self):
        pass


def _fake_socket(monkeypatch, replies):
    fake = _FakeSocket(replies)
    monkeypatch.setattr(udp.socket, "socket", lambda *args, **kwargs: fake)
    return fake


def test_discovery_asks_both_ps4_and_ps5(monkeypatch):
    """Only the PS5 query (port 9302) used to be sent, so a PS4 -- which
    listens on 987 for a different protocol version -- was never found."""
    fake = _fake_socket(monkeypatch, [])
    assert udp.discover_ps_ip(timeout=0.01) is None
    sent = {addr[1]: data for data, addr in fake.sent}
    assert b"00020020" in sent[987]    # PS4
    assert b"00030010" in sent[9302]   # PS5


def test_discovery_returns_the_first_console_that_answers(monkeypatch):
    _fake_socket(monkeypatch, [
        (b"not a console", ("192.168.1.7", 5000)),
        (b"HTTP/1.1 620 Server Standby\nhost-type:PS4\n", ("192.168.1.50", 987)),
    ])
    assert udp.discover_ps_ip(timeout=1.0) == "192.168.1.50"


# ── Race Analyst pit inference ─────────────────────────────────────────────
def test_refuel_marks_pit_lane_section():
    pytest.importorskip("tkinter")
    from gt7telem.race_analyst import _infer_pit_flag
    speed = [200, 200, 70, 60, 0, 0, 60, 70, 200, 200]
    fuel  = [50, 49.9, 49.8, 49.8, 49.8, 90, 90, 90, 89.9, 89.8]
    flag = _infer_pit_flag(pd.DataFrame({"speed_kmh": speed, "fuel_remaining": fuel}))
    assert list(flag) == [0, 0, 1, 1, 1, 1, 1, 1, 0, 0]


def test_no_refuel_no_pit():
    pytest.importorskip("tkinter")
    from gt7telem.race_analyst import _infer_pit_flag
    df = pd.DataFrame({"speed_kmh": np.full(20, 50.0), "fuel_remaining": np.linspace(50, 40, 20)})
    assert _infer_pit_flag(df).sum() == 0


def test_partial_last_lap_is_not_the_best_lap():
    """A recording stopped mid-lap (or ended on the cool-down lap) left a
    short final segment that became "Best Lap" and skewed the average."""
    pytest.importorskip("tkinter")
    from gt7telem.race_analyst import build_stats, lap_split_stats
    hz = 10
    laps = [1] * 950 + [2] * 950 + [3] * 150          # lap 3 stopped after 15 s
    n = len(laps)
    df = pd.DataFrame({
        "t": np.arange(n) / hz, "lap_number": laps, "speed_kmh": 150.0,
        "brake": 0.0, "throttle": 1.0, "lat_g": 0.0, "long_g": 0.0,
        "fuel_remaining": np.linspace(80, 70, n), "coasting": 0.0, "pit_flag": 0.0,
        "current_position": [3] * 20 + [-1] * (n - 20), "total_positions": [16] * 20 + [-1] * (n - 20),
    })
    splits = lap_split_stats(df)
    assert [s["complete"] for s in splits] == [True, True, False]
    stats = build_stats({"race_duration_s": n / hz}, df)
    assert stats["Best Lap"] == "1:35.000"
    assert stats["Laps"] == "2 (+1 partial)"
    assert stats["Grid Pos"] == "P3"


# ── Fuel & tyre strategy (udp.StintTracker) ───────────────────────────────────
def _drive(tr, first_lap, laps, fuel, per_lap, total, times_ms, temps=(85, 85, 90, 90), refuel_on=None):
    """Feed `tr` like the live stream does: 10 snapshots per lap, the lap
    counter ticking over with the previous lap's time. Returns the fuel left
    and the lap the car is on next. `refuel_on` = (lap, amount) adds fuel
    halfway round that lap."""
    lap = first_lap
    tr.update(lap, total, fuel, -1, temps, 150)
    for i in range(laps):
        for k in range(10):
            fuel -= per_lap / 10
            if refuel_on and refuel_on[0] == lap and k == 5:
                fuel += refuel_on[1]
            tr.update(lap, total, fuel, times_ms[i - 1] if i else -1, temps, 150)
        lap += 1
        tr.update(lap, total, fuel, times_ms[i], temps, 150)
    return fuel, lap


def test_fuel_per_lap_is_a_median_of_clean_laps():
    tr = udp.StintTracker()
    # burn per lap: 6, 6, 12 (one thirsty lap), 6, 6
    fuel, lap = 100.0, 1
    tr.update(lap, 20, fuel, -1)
    for burn in (6.0, 6.0, 12.0, 6.0, 6.0):
        fuel -= burn
        lap += 1
        tr.update(lap, 20, fuel, 90000)
    snap = tr.snapshot()
    assert snap["fuel_per_lap"] == pytest.approx(6.0)
    assert snap["fuel_basis_laps"] == 5
    assert snap["laps_of_fuel"] == pytest.approx(fuel / 6.0)


def test_nothing_is_claimed_before_a_lap_is_done():
    tr = udp.StintTracker()
    tr.update(1, 10, 100.0, -1)
    tr.update(1, 10, 99.0, -1)
    snap = tr.snapshot()
    assert snap["fuel_per_lap"] is None and snap["laps_of_fuel"] is None
    assert snap["pit_by_lap"] is None and snap["tyre_state"] is None


def test_finish_check_and_pit_by_lap():
    # 12-lap race at 9 % a lap: after 6 laps there is fuel for 5.1 laps but 6 left to run
    tr = udp.StintTracker()
    fuel, lap = _drive(tr, 1, 6, 100.0, 9.0, 12, [92000] * 6)
    snap = tr.snapshot()
    assert lap == 7 and snap["stop_needed"] is True
    assert snap["spare_laps"] == pytest.approx(46.0 / 9.0 - 6)
    # 0.5 lap in hand: finish laps 7-10 then pit
    assert snap["pit_by_lap"] == 10

    # an 8-lap race at 5 % a lap has plenty: no stop, no pit-by lap
    tr = udp.StintTracker()
    _drive(tr, 1, 4, 100.0, 5.0, 8, [92000] * 4)
    snap = tr.snapshot()
    assert snap["stop_needed"] is False and snap["pit_by_lap"] is None and snap["spare_laps"] > 0.5


def test_pit_by_is_this_lap_when_the_tank_is_nearly_empty():
    tr = udp.StintTracker()
    _drive(tr, 1, 6, 60.0, 9.0, 12, [92000] * 6)   # 6 % left = 0.67 laps
    assert tr.snapshot()["pit_by_lap"] == 7          # box at the end of the lap it is on


def test_practice_with_no_race_length_still_gives_a_pit_by_lap():
    tr = udp.StintTracker()
    _drive(tr, 1, 3, 100.0, 9.0, 0, [92000] * 3)
    snap = tr.snapshot()
    assert snap["spare_laps"] is None and snap["pit_by_lap"] is not None


def test_a_refuel_is_not_counted_as_a_lap_and_starts_a_new_stint():
    tr = udp.StintTracker()
    _drive(tr, 1, 4, 100.0, 6.0, 20, [92000] * 4)
    assert tr.snapshot()["stint_age"] == 4
    # lap 5 includes a +40 refuel: its burn must not enter the average, and the stint restarts
    fuel, lap = _drive(tr, 5, 1, 76.0, 6.0, 20, [95000], refuel_on=(5, 40.0))
    snap = tr.snapshot()
    assert snap["fuel_per_lap"] == pytest.approx(6.0)
    assert snap["fuel_basis_laps"] == 4
    assert snap["stint_age"] == 1


def test_new_tyres_button_restarts_only_the_stint():
    tr = udp.StintTracker()
    _drive(tr, 1, 5, 100.0, 5.0, 20, [92000, 92100, 92200, 92300, 92400])
    tr.reset_stint()
    snap = tr.snapshot()
    assert snap["stint_age"] == 0 and snap["pace_loss_s"] is None
    assert snap["fuel_per_lap"] == pytest.approx(5.0)   # fuel history is kept


def test_tyre_pace_loss_trend_and_state():
    tr = udp.StintTracker()
    # lap 1 (out-lap) is slow and ignored; then 92.0 climbing 0.4 s a lap
    times = [95000, 92000, 92400, 92800, 93200, 93600]
    _drive(tr, 1, 6, 100.0, 5.0, 20, times)
    snap = tr.snapshot()
    assert snap["stint_clean_laps"] == 5
    assert snap["pace_loss_s"] == pytest.approx(1.2)      # best 92.0 vs the better of the last two (93.2)
    assert snap["trend_s_per_lap"] == pytest.approx(0.4)
    assert snap["tyre_state"] == "Fading"


def test_a_traffic_lap_does_not_count_as_tyre_wear():
    tr = udp.StintTracker()
    times = [95000, 92000, 92100, 99000, 92200, 92300]   # lap 4 is 7 s slow (off / traffic)
    _drive(tr, 1, 6, 100.0, 5.0, 20, times)
    snap = tr.snapshot()
    assert snap["stint_clean_laps"] == 4
    assert snap["pace_loss_s"] < 0.5 and snap["tyre_state"] in ("Fresh", "Good")


def test_race_restart_or_skipped_laps_wipe_the_estimates():
    tr = udp.StintTracker()
    _drive(tr, 1, 4, 100.0, 6.0, 12, [92000] * 4)
    assert tr.snapshot()["fuel_per_lap"] is not None
    tr.update(1, 12, 100.0, -1)          # lap counter went backwards: restarted
    assert tr.snapshot()["fuel_per_lap"] is None
    _drive(tr, 1, 2, 100.0, 6.0, 12, [92000] * 2)
    tr.update(7, 12, 60.0, 92000)        # jumped from lap 3 to 7: don't trust the gap
    assert tr.snapshot()["fuel_per_lap"] is None


def test_tyre_heat_summary_for_the_last_lap():
    tr = udp.StintTracker()
    _drive(tr, 1, 2, 100.0, 5.0, 20, [92000] * 2, temps=(110, 112, 95, 96))
    heat = tr.snapshot()["heat"]
    assert heat["front_avg"] == pytest.approx(111.0) and heat["rear_avg"] == pytest.approx(95.5)
    assert heat["pct_hot"] == pytest.approx(100.0) and heat["pct_cold"] == 0.0


def test_ev_battery_is_tracked_like_fuel():
    tr = udp.StintTracker()
    _drive(tr, 1, 4, 100.0, 7.0, 10, [92000] * 4)
    assert tr.snapshot()["fuel_per_lap"] == pytest.approx(7.0)


# ── Race Analyst: fuel per lap from a recording ───────────────────────────────
def test_race_fuel_per_lap_skips_refuel_laps_and_the_partial_lap():
    from gt7telem.race_analyst import fuel_per_lap_stats
    rows, fuel, t = [], 100.0, 0.0
    for lap in range(1, 6):
        for k in range(10):
            if lap == 3 and k == 5:
                fuel += 40
            fuel -= 0.5
            t += 1.0
            rows.append(dict(t=t, lap_number=lap, fuel_remaining=fuel, fuel_capacity=100.0, pit_flag=0.0))
    df = pd.DataFrame(rows)
    fpl, laps_tank, used = fuel_per_lap_stats(df)
    assert fpl == pytest.approx(5.0) and laps_tank == pytest.approx(20.0) and used == 3
    assert fuel_per_lap_stats(df.iloc[:5]) == (None, None, 0)


# ── Lap Analyst: shareable lap card ───────────────────────────────────────────
def _card_lap(n=300, gps=True):
    from gt7telem.lap_analyst import load_lap_data
    th = np.linspace(0, 2 * np.pi, n)
    x, z = 400 * np.cos(th), 250 * np.sin(th)
    dist = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(z)))])
    samples = []
    for i in range(n):
        s = dict(t=i * 0.1, speed_kmh=float(150 + 80 * np.sin(3 * th[i])), throttle=0.6, brake=0.1,
                 track_position=float(dist[i]), gear=3, ang_y=0.1, fuel_remaining=70 - i * 0.02,
                 fuel_capacity=100.0, tyre_temp_fl=80, tyre_temp_fr=80, tyre_temp_rl=88, tyre_temp_rr=88)
        if gps:
            s.update(world_x=float(x[i]), world_z=float(z[i]))
        samples.append(s)
    return load_lap_data({"car": "Test Car", "track": "test_circuit", "lap_time_s": 83.456,
                          "recorded_at": "20261002_141500", "samples": samples})


@pytest.mark.parametrize("gps", [True, False])
def test_lap_card_is_a_1200x630_png(gps):
    import io

    from PIL import Image

    from gt7telem.lap_analyst import lap_card_png
    data, df = _card_lap(gps=gps)
    png = lap_card_png(data, df)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(png)).size == (1200, 630)


def test_lap_card_for_a_ghost_lap_with_only_the_compact_fields():
    from gt7telem.lap_analyst import lap_card_png, load_lap_data
    samples = [dict(track_position=float(i * 5), speed_kmh=120.0 + i % 40, throttle=0.5, brake=0.0,
                    steering=0.0, gear=3, t=i * 0.1) for i in range(200)]
    data, df = load_lap_data({"car": "Ghost Car", "track": "somewhere", "lap_time_s": 99.9, "samples": samples})
    assert lap_card_png(data, df)[:4] == b"\x89PNG"
