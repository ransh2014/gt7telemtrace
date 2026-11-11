"""The Tk Dashboard and the terminal Recorder must record laps identically: same packet stream in, same log lines, same
lap saves, same state changes out. Both call core.recorder.lap_step; this feeds one stream through both entry points."""
import types

from gt7telem import dashboard
from gt7telem.core import recorder as rc


def stream():
    """Arm on lap 1, record lap 2 (120 packets), finish it (lap 3), then restart (lap counter goes back to 1)."""
    pk = []
    pk += [{"lap_number": 1, "track_position": 0.0}] * 3                       # armed, waiting for the line
    for i in range(120):
        pk.append({"lap_number": 2, "track_position": float(i), "speed_kmh": 100.0 + i, "last_lap_ms": 0})
    for i in range(120):
        pk.append({"lap_number": 3, "track_position": float(i), "speed_kmh": 90.0 + i, "last_lap_ms": 91234})
    pk += [{"lap_number": 1, "track_position": 0.0}] * 2                       # restart: partial lap kept
    return pk


def run_recorder(packets):
    log, states, saves = [], [], []
    hooks = rc.Hooks(laps_folder=lambda: None, track=lambda: "T", car=lambda: "C", rate=lambda: 30, log=log.append,
                     state=states.append)
    r = rc.Recorder(hooks)
    r.save_lap = lambda samples, ms, incomplete=False, background=False, **k: saves.append((len(samples), ms, incomplete))
    r.s.waiting, r.s.recording, r.s.prev_lap, r.s.samples = True, False, 0, []
    t = 0.0
    for p in packets:
        t += 0.033
        r.record_sample(p, now=t)
    return log, states, saves


def run_dashboard(packets):
    log, states, saves = [], [], []

    class Btn:
        def config(self, text=None, bg=None):
            states.append("recording" if text and "STOP" in text.upper() else "armed")

    fake = types.SimpleNamespace(rec_btn=Btn(), log_msg=log.append)
    fake._save_lap = lambda samples, lap_time_ms=None, incomplete=False, prompt=True, background=False: saves.append(
        (len(samples), lap_time_ms, incomplete))
    fake._sample_dict = lambda d, start_t, now: rc.sample_dict(d, start_t, now)
    s = rc.session
    s.waiting, s.recording, s.prev_lap, s.samples = True, False, 0, []
    t = 0.0
    for p in packets:
        t += 0.033
        dashboard.App._record_sample(fake, p, now=t)
    return log, states, saves


def test_dashboard_and_recorder_record_the_same_laps():
    a = run_recorder(stream())
    b = run_dashboard(stream())
    assert a[0] == b[0], "log lines differ"
    assert a[1] == b[1], "state changes differ"
    assert a[2] == b[2], "saved laps differ"
    assert a[2] == [(120, 91234, False), (120, None, True)] or a[2][0] == (120, 91234, False)
    assert a[2][-1][2] is True                                                  # the restart kept a partial lap


def test_time_attack_hook_still_runs_for_recorded_packets():
    seen = []
    fake = types.SimpleNamespace(rec_btn=types.SimpleNamespace(config=lambda **k: None), log_msg=lambda m: None,
                                 _save_lap=lambda *a, **k: None, _sample_dict=lambda d, st, now: rc.sample_dict(d, st, now),
                                 _td_step=seen.append)
    s = rc.session
    s.waiting, s.recording, s.prev_lap, s.samples = True, False, 0, []
    for i, p in enumerate([{"lap_number": 1}, {"lap_number": 2, "track_position": 1.0}, {"lap_number": 2, "track_position": 2.0}]):
        dashboard.App._record_sample(fake, p, now=i * 0.1)
    assert seen == [2, 2]                                                       # once per recorded sample, as before


# ── race recording and auto lap: the same rules in both front ends ──────────────────────────────────────────────────
def _fresh_session():
    s = rc.session
    s.waiting = s.recording = s.race_recording = s.paused = False
    s.samples, s.race_samples, s.prev_lap, s.paused_at = [], [], 0, None
    s.start_t = s.race_start_t = 0.0
    return s


def _race_packets(n):
    return [{"lap_number": 1, "speed_kmh": 100.0 + i % 50, "track_position": float(i)} for i in range(n)]


def test_race_recording_matches_between_dashboard_and_recorder(monkeypatch):
    # Recorder
    _fresh_session()
    log_a, saves_a = [], []
    hooks = rc.Hooks(laps_folder=lambda: None, log=log_a.append, rate=lambda: 30)
    r = rc.Recorder(hooks)
    r.save_race = lambda samples, background=False, notify_end=False, **k: saves_a.append((len(samples), notify_end))
    monkeypatch.setattr(rc.time, "monotonic", lambda: 100.0)
    r.start_race()
    for i, p in enumerate(_race_packets(50)):
        r.record_race_sample(p, now=100.0 + i * 0.033)
    r.stop_race(notify_end=True)

    # Dashboard
    _fresh_session()
    log_b, saves_b = [], []
    btn = types.SimpleNamespace(config=lambda **k: None)
    fake = types.SimpleNamespace(race_btn=btn, log_msg=log_b.append, _record_rate=30, _td_race_reset=lambda: None,
                                 _sample_dict=lambda d, st, now: rc.sample_dict(d, st, now))
    fake._save_race = lambda samples, background=False, notify_end=False, **k: saves_b.append((len(samples), notify_end))
    fake._stop_record_race = lambda notify_end=False: dashboard.App._stop_record_race(fake, notify_end)
    dashboard.App._start_record_race(fake)
    for i, p in enumerate(_race_packets(50)):
        dashboard.App._record_race_sample(fake, p, now=100.0 + i * 0.033)
    dashboard.App._stop_record_race(fake, notify_end=True)

    assert saves_a == saves_b == [(50, True)]
    assert [m for m in log_a if "started" in m] == [m for m in log_b if "started" in m]


def test_race_limit_stops_and_saves_in_both(monkeypatch):
    monkeypatch.setattr(rc, "RACE_SAMPLE_MAX", 20)
    monkeypatch.setattr(rc, "RACE_SAMPLE_WARN", 10)
    outs = []
    for kind in ("recorder", "dashboard"):
        _fresh_session()
        log, saves = [], []
        if kind == "recorder":
            r = rc.Recorder(rc.Hooks(laps_folder=lambda: None, log=log.append, rate=lambda: 30))
            r.save_race = lambda samples, **k: saves.append(len(samples))
            r.start_race()
            for i, p in enumerate(_race_packets(40)):
                r.record_race_sample(p, now=i * 0.033)
        else:
            fake = types.SimpleNamespace(race_btn=types.SimpleNamespace(config=lambda **k: None), log_msg=log.append,
                                         _record_rate=30, _td_race_reset=lambda: None,
                                         _sample_dict=lambda d, st, now: rc.sample_dict(d, st, now))
            fake._save_race = lambda samples, **k: saves.append(len(samples))
            fake._stop_record_race = lambda notify_end=False: dashboard.App._stop_record_race(fake, notify_end)
            dashboard.App._start_record_race(fake)
            for i, p in enumerate(_race_packets(40)):
                dashboard.App._record_race_sample(fake, p, now=i * 0.033)
        outs.append((saves, [m for m in log if "large" in m or "limit" in m]))
    assert outs[0][0] == outs[1][0] == [20]                      # stopped and saved exactly at the limit
    assert len(outs[0][1]) == len(outs[1][1]) == 2               # one warning, one limit message


def test_pause_shift_moves_both_clocks_the_same_way():
    s = _fresh_session()
    s.start_t, s.race_start_t, s.paused, s.paused_at = 10.0, 20.0, True, 100.0
    gap = rc.pause_shift(s, 107.5)
    assert gap == 7.5 and s.start_t == 17.5 and s.race_start_t == 27.5 and not s.paused and s.paused_at is None
    assert rc.pause_shift(s, 200.0) == 0.0 and s.start_t == 17.5


def _auto_trace(kind, monkeypatch):
    """Drive the same packet timeline through auto-lap and return (event, time) pairs."""
    _fresh_session()
    ev = []
    drive = {"loading": False, "car_on_track": True, "total_laps": 0, "speed_kmh": 100.0}
    off = dict(drive, car_on_track=False)
    timeline = [(i * 0.1, drive) for i in range(30)] + [(3.0 + i * 0.5, off) for i in range(10)]
    if kind == "recorder":
        r = rc.Recorder(rc.Hooks(laps_folder=lambda: None, log=lambda m: ev.append(m), auto_lap_enabled=lambda: True))
        r.save_lap = lambda *a, **k: None
        for t, d in timeline:
            r.auto_lap_step(d, t)
    else:
        monkeypatch.setattr(dashboard.runtime_config, "AUTO_LAP_ENABLED", True, raising=False)
        fake = types.SimpleNamespace(
            _auto_lap_since=None, _auto_lap_off_since=None, _auto_lap_active=False, _auto_lap_block=False,
            rec_btn=types.SimpleNamespace(config=lambda **k: None), log_msg=lambda m: ev.append(m),
            _warn_no_track=lambda *a: None)

        def start():
            rc.session.waiting = True
            ev.append("Armed -- cross the start line to begin recording")
        fake._start_record = start
        fake._auto_lap_disarm = lambda why: dashboard.App._auto_lap_disarm(fake, why)
        for t, d in timeline:
            dashboard.App._auto_lap_step(fake, d, t)
    return ev


def test_auto_lap_arms_and_disarms_identically(monkeypatch):
    a = _auto_trace("recorder", monkeypatch)
    b = _auto_trace("dashboard", monkeypatch)
    assert a == b
    assert any("armed" in m for m in a) and any("session ended" in m for m in a)
