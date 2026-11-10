from types import SimpleNamespace

import numpy as np
from test_coach import _costly
from test_corners import _lap

from gt7telem import analysis_tools as at


def _lap_with_world(df):
    d = df.copy()
    th = np.linspace(0, 2 * np.pi, len(d))
    d["world_x"], d["world_y"], d["world_z"] = 300 * np.cos(th), 0.0, 200 * np.sin(th)
    return d


def test_reports_ask_for_missing_laps():
    assert "Load Lap A" in at.setup_report(None)
    assert "Load Lap A" in at.validate_report(None)
    assert "Load Lap A" in at.dna_report(None)
    assert "Lap B" in at.coach_report(_lap(), None)
    assert "Lap B" in at.dyno_compare_report(_lap(), None)


def test_coach_and_plan_reports():
    slow = _costly(_lap(throttle_shift=40), 20, 60)
    assert "T" in at.coach_report(slow, _lap()) and "Coach" in at.coach_report(slow, _lap())
    laps = [(_lap(), 90.0), (slow, float(slow["t"].iloc[-1])), (None, 0), (_costly(_lap(), 20, 80, 0.8), 95.0)]
    assert "corner" in at.plan_report(laps).lower()


def test_dna_report_does_not_touch_the_real_history(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    df = _lap().assign(steering=0.3)
    text = at.dna_report(df, [df, df], save=True)
    assert "Share this code" in text and "DNA1:" in text
    assert (tmp_path / ".gt7telem" / "driver_dna.json").exists()


def test_replay_helpers(tmp_path):
    df = _lap_with_world(_lap())
    assert at.replay_ok(df) and not at.replay_ok(None) and not at.replay_ok(_lap())
    p = at.write_replay(tmp_path / "r.html", df, df, "T")
    assert p.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_race_report_all_kinds():
    laps = [(1, 90.0, _lap()), (2, 95.0, _costly(_lap(throttle_shift=40), 20, 60)), (3, 91.0, _lap())]
    for kind in ("coach", "plan", "dna", "validate"):
        assert at.race_report(kind, laps).strip()
    assert "at least two" in at.race_report("coach", laps[:1])
    assert "Lap 2" in at.race_report("validate", laps)


def _tk_or_skip():
    import tkinter

    import pytest
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display for Tk")
    root.destroy()


def test_lap_analyst_tool_buttons_run_on_a_loaded_lap():
    _tk_or_skip()
    from gt7telem import lap_analyst
    app = lap_analyst.AnalystApp()
    try:
        app.withdraw()
        a = _lap_with_world(_lap()).assign(steering=0.3)
        b = _lap_with_world(_costly(_lap(), 20, 60))
        app._da, app._dfa = {"lap_time_s": 90.0}, a
        app._db, app._dfb = {"lap_time_s": 92.0}, b
        for kind in ("coach", "setup", "dna", "plan", "validate", "dyno"):
            title, text = app._tool_text(kind)
            assert title and isinstance(text, str) and text.strip()
    finally:
        app.destroy()


def test_race_analyst_builds_with_the_tool_buttons_and_runs_them(monkeypatch):
    _tk_or_skip()
    from gt7telem import race_analyst
    shown = []
    monkeypatch.setattr(race_analyst.messagebox, "showinfo", lambda *a, **k: shown.append(a))   # modal otherwise
    app = race_analyst.AnalystApp()
    try:
        app.withdraw()
        app._show_tool("coach")          # nothing loaded: must say so, not crash
        assert shown and "Load Race A" in shown[0][1]
    finally:
        app.destroy()


def test_dashboard_hooks_speak_cues_and_log_tips(monkeypatch):
    from gt7telem import coach, dashboard
    spoken, logged = [], []
    monkeypatch.setattr(dashboard.voice, "speak", spoken.append)
    monkeypatch.setattr(dashboard.runtime_config, "VOICE_ENABLED", True)
    monkeypatch.setattr(dashboard, "session", SimpleNamespace(paused=False))
    fake = SimpleNamespace(log_msg=logged.append, _live_coach=coach.LiveCoach(),
                           _radio={"text": "", "until": 0.0, "lap": None})
    samples = [{**r, "ang_y": r["lat_g"] * 9.81 / max(r["speed_kmh"] / 3.6, 1)}
               for r in _costly(_lap(throttle_shift=40), 20, 60).to_dict("records")]
    ref = [{**r, "ang_y": r["lat_g"] * 9.81 / max(r["speed_kmh"] / 3.6, 1)} for r in _lap().to_dict("records")]
    dashboard.App._coach_lap(fake, samples, ref)
    assert logged and logged[0].startswith("Coach:") and fake._live_coach.tips
    tip = fake._live_coach.tips[0]
    at_m = tip.brake_pos if tip.brake_pos is not None else tip.apex
    dashboard.App._speak_race_calls(fake, {"track_position": at_m - 80, "speed_kmh": 150.0, "lap_number": 1,
                                           "total_laps": 0}, {})
    assert spoken == [tip.cue] and fake._radio["text"] == tip.cue
    # a pit call comes through the same path once per lap
    fake._live_coach.set_tips([])
    snap = {"laps_of_fuel": 2.2, "stop_needed": True, "pace_loss_s": 2.0}
    d = {"track_position": 10.0, "speed_kmh": 150.0, "lap_number": 3, "total_laps": 10}
    dashboard.App._speak_race_calls(fake, d, snap)
    dashboard.App._speak_race_calls(fake, d, snap)
    assert spoken.count("Box this lap. Box, box.") == 1
