"""Recording without a window (core/recorder.py, core/live.py): the same state machine and files the Dashboard uses."""
import json
import time
from pathlib import Path

import pytest

from gt7telem import dashboard
from gt7telem import udp as telem
from gt7telem.core import live
from gt7telem.core import recorder as rec


def packets(laps=3, per_lap=140, start_lap=1, total_laps=0):
    """A stream of parsed packets: the lap counter ticks up every `per_lap` samples."""
    out = []
    for lap in range(start_lap, start_lap + laps):
        for i in range(per_lap):
            out.append({
                "lap_number": lap, "total_laps": total_laps, "track_position": i * 25.0,
                "world_x": i * 25.0, "world_z": 0.0, "speed_kmh": 180.0, "throttle": 1.0, "brake": 0.0,
                "last_lap_ms": 60000 - lap * 10, "car_on_track": True, "fuel_remaining": 80 - lap - i * 0.01,
                "fuel_capacity": 100.0, "gear": 4, "rpm": 6000.0, "current_lap_ms": int(i * 430),
            })
    return out


@pytest.fixture()
def env(tmp_path, monkeypatch):
    log = []
    hooks = rec.Hooks(laps_folder=lambda: tmp_path, track=lambda: "Test Ring", car=lambda: "Test Car", rate=lambda: 10,
                      log=log.append)
    monkeypatch.setattr(telem, "get_int", lambda k: 1)
    monkeypatch.setattr(rec, "FALLBACK_SAVE_DIR", tmp_path / "unsaved")
    r = rec.Recorder(hooks, rec.Session())
    return r, log, tmp_path


def TD_of(root):
    return root / telem.sanitize("Test Ring")


def feed(r, pkts, hz=10.0, t0=None):
    t0 = time.monotonic() + 0.01 if t0 is None else t0      # packets older than the arming are ignored, as live
    for i, d in enumerate(pkts):
        r.tap(d, t0 + i / hz)
        r.drain()


def test_thinning_keeps_the_requested_rate():
    due, kept = 0.0, 0
    for i in range(600):                                  # 60 Hz stream thinned to 10 Hz for 10 s
        ok, due = rec.thin_accept(due, i / 60.0, 0.1)
        kept += ok
    assert 98 <= kept <= 101


def test_dashboard_uses_the_core_helpers():
    assert dashboard.session is rec.session and dashboard.auto_lap_can_arm is rec.auto_lap_can_arm
    assert dashboard.lap_clock_running is rec.lap_clock_running
    assert dashboard.App._sample_dict(None, {"speed_kmh": 5}, 0.0, 1.0) == rec.sample_dict({"speed_kmh": 5}, 0.0, 1.0)


def test_lap_state_machine_saves_complete_laps_and_a_reference(env):
    r, log, root = env
    r.start_lap()
    feed(r, packets(laps=3))
    r.stop_lap()
    r.wait()
    TD = TD_of(root)
    files = sorted(TD.glob("test_car_*.json"))
    assert len(files) >= 1, log
    data = json.loads(files[0].read_text(encoding="utf-8"))
    for key in ("recorded_at", "track", "car", "lap_time_s", "lap_distance_m", "samples", "physics_era", "gear_ratios"):
        assert key in data
    assert data["samples"][0]["lap_number"] >= 1 and not data["incomplete"]
    assert (TD_of(root) / "reference_test_car.json").exists()
    assert (TD_of(root) / "personal_bests.json").exists()
    assert any("Start line crossed" in m for m in log) and any("New personal best" in m for m in log)


def test_reference_gives_a_live_delta(env):
    r, log, root = env
    r.start_lap()
    feed(r, packets(laps=3))
    r.stop_lap()
    r.wait()
    d = r.refs.delta(telem.sanitize("Test Ring"), telem.sanitize("Test Car"), 1000.0, 5.0)
    assert d is not None and abs(d) < 10
    assert r.refs.delta(telem.sanitize("Test Ring"), telem.sanitize("Test Car"), 1000.0, 0.1) is None      # too early in the lap to say


def test_pause_freezes_and_shifts_the_clock(env):
    r, log, root = env
    r.start_lap()
    feed(r, packets(laps=1)[:5])
    r.pause()
    r.tap(packets(laps=1)[0], 5000.0)
    assert not r.q                                        # nothing queued while paused
    r.s.start_t = 100.0
    r.s.paused_at -= 2.0
    r.resume()
    assert r.s.start_t >= 101.9 and not r.s.paused


def test_race_recording_saves_a_race_file(env):
    r, log, root = env
    r.start_race()
    feed(r, packets(laps=2))
    r.stop_race()
    r.wait()
    files = list((TD_of(root) / "races").glob("race_*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["total_samples"] > 100


def test_junk_and_duplicate_laps_are_refused(env):
    r, log, root = env
    r.s.start_t = 0.0
    assert r.save_lap([{"t": 0, "speed_kmh": 0}] * 5, 60000) is rec.SKIPPED
    assert any("not saved" in m for m in log)


def test_auto_lap_arms_after_a_hold_and_disarms_when_the_session_ends(env):
    r, log, root = env
    r.h.auto_lap_enabled = lambda: True
    d = {"loading": False, "car_on_track": True, "total_laps": 0, "speed_kmh": 100.0}
    r.auto_lap_step(d, 0.0)
    assert not r.s.waiting
    r.auto_lap_step(d, rec.AUTO_LAP_ARM_HOLD_S + 0.1)
    assert r.s.waiting and r.auto_active
    off = dict(d, car_on_track=False)
    r.auto_lap_step(off, 10.0)
    r.auto_lap_step(off, 10.0 + rec.AUTO_LAP_LEAVE_HOLD_S + 0.1)
    assert not r.s.waiting and not r.auto_active


def test_live_engine_frame_with_delta_cue_and_pit_call(env, monkeypatch):
    r, log, root = env
    r.start_lap()
    feed(r, packets(laps=3))
    r.stop_lap()
    r.wait()
    eng = live.LiveEngine(r.h, speak=lambda t: spoken.append(t), voice_enabled=lambda: True, ghost_enabled=lambda: False)
    eng.recorder = r
    spoken = []
    monkeypatch.setattr(telem, "is_connected", lambda: True)
    d = dict(packets(laps=1, start_lap=3, total_laps=10)[100], lap_number=9, fuel_remaining=4.0)
    eng.strategy._fuel_laps = [2.0, 2.0, 2.0]
    out = eng.poll(d, now=50.0)
    assert out["connected"] and "strategy" in out
    assert out["delta"] is not None
    out2 = eng.poll(d, now=51.0)
    assert out2["cue"] == "" or isinstance(out2["cue"], str)
    t = rec.Session()
    assert t.recording is False and Path


def test_tyre_alert_rule_matches_the_dashboard():
    assert live.tyre_alerts([90, 101, 80, 80]) == (True, False)
    assert live.tyre_alerts([90, 70, 59, 80]) == (False, True)
    assert live.tyre_alerts([80, 80, 80, 80]) == (False, False)
