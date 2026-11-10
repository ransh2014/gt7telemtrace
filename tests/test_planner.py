from test_coach import _costly
from test_corners import _lap

from gt7telem import planner


def _session():
    laps = [_lap(), _costly(_lap(), 20, 80, 0.8), _costly(_lap(), 20, 80, 0.85), _costly(_lap(), -140, -30, 0.9)]
    times = [float(f["t"].iloc[-1]) for f in laps]
    return laps, times


def test_needs_enough_laps():
    r = planner.plan([_lap(), _lap()], [90.0, 90.1])
    assert not r["ok"] and "at least" in r["text"]


def test_finds_corners_to_drill_and_gives_targets():
    laps, times = _session()
    r = planner.plan(laps, times, n=2)
    assert r["ok"] and r["best_lap"] == 0
    assert 1 <= len(r["drills"]) <= 2 and r["total_gain_s"] > 0
    d = r["drills"][0]
    assert "km/h" in d.target and f"T{d.corner}" in r["text"]


def test_identical_laps_say_nothing_stands_out():
    laps = [_lap(), _lap(), _lap()]
    r = planner.plan(laps, [90.0, 90.0, 90.0])
    assert r["ok"] and not r["drills"] and "consistent" in r["text"]


def test_progress_history(tmp_path):
    p = tmp_path / "p.json"
    assert "First session" in planner.progress(planner.load_history(p), "gt-r|monza")
    assert planner.record_session("gt-r|monza", "2026-11-01", 91.0, 92.0, p)
    assert planner.record_session("gt-r|monza", "2026-11-08", 90.4, 91.5, p)
    assert planner.record_session("gt-r|monza", "2026-11-08", 90.3, 91.4, p)   # same day replaces
    h = planner.load_history(p)
    assert len(h) == 2
    text = planner.progress(h, "gt-r|monza")
    assert "0.70s faster" in text and "2 sessions" in text
