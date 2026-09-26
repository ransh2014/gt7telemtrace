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
