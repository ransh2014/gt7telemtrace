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
