"""Live gap to the leaderboard's top lap: ghost clock, the one-request fetch, and the Dashboard's once-per-car+track rule."""
import io
import json
import threading
import urllib.parse
from types import SimpleNamespace

import numpy as np
import pytest

from gt7telem import config, dashboard, ghost, leaderboard


def _samples(length=3000.0, n=300, kmh=180.0, shuffle=False):
    pos = np.linspace(0, length, n)
    rows = [{"track_position": float(p), "speed_kmh": kmh, "throttle": 1.0, "brake": 0.0, "steering": 0.0, "gear": 5}
            for p in pos]
    if shuffle:
        rows = rows[::-1]
    return rows


def test_ghost_clock_is_scaled_to_the_lap_time():
    g = ghost.build_ghost(_samples(), 60000)           # 3000 m at constant speed, 60.000 s
    assert g is not None and g.lap_time_s == 60.0
    assert g.time_at(0.0) == pytest.approx(0.0)
    assert g.time_at(1500.0) == pytest.approx(30.0, abs=0.05)
    assert g.time_at(3000.0) == pytest.approx(60.0, abs=0.05)


def test_ghost_clock_follows_speed_not_distance():
    # slow first half, fast second half: the first half must take most of the lap
    rows = _samples(kmh=100.0)[:150] + _samples(kmh=200.0)[150:]
    g = ghost.build_ghost(rows, 90000)
    assert g.time_at(1500.0) > 90.0 * 0.55


def test_gap_sign_and_range():
    g = ghost.build_ghost(_samples(), 60000)
    assert g.gap(1500.0, 31.5) == pytest.approx(1.5, abs=0.05)       # behind
    assert g.gap(1500.0, 29.0) == pytest.approx(-1.0, abs=0.05)      # ahead
    assert g.gap(-5.0, 1.0) is None and g.gap(3100.0, 70.0) is None  # off the ghost's lap


def test_unsorted_and_duplicate_positions_are_handled():
    rows = _samples(shuffle=True) + _samples()[:10]
    g = ghost.build_ghost(rows, 60000)
    assert g is not None and g.time_at(1500.0) == pytest.approx(30.0, abs=0.1)


@pytest.mark.parametrize("samples,ms", [
    (None, 60000), ([], 60000), (_samples(n=5), 60000), (_samples(length=50.0), 60000),
    (_samples(), 0), (_samples(), None), (_samples(), float("nan")), (_samples(), "soon"),
    ([{"track_position": "x", "speed_kmh": None}] * 50, 60000), ([1, 2, 3] * 20, 60000),
])
def test_unusable_data_gives_no_ghost(samples, ms):
    assert ghost.build_ghost(samples, ms) is None


def test_zero_speed_samples_cannot_make_an_absurd_clock():
    g = ghost.build_ghost(_samples(kmh=0.0), 60000)
    assert g is not None and g.time_at(3000.0) == pytest.approx(60.0, abs=0.05)


def test_format_gap_colours():
    assert ghost.format_gap(None)[0] == "--"
    assert ghost.format_gap(0.456) == ("+0.46s", "#e74c3c")
    assert ghost.format_gap(-0.2) == ("-0.20s", "#2ecc71")


# ── leaderboard.get_top_lap_ghost: one read-only request ─────────────────────
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_top_lap_ghost_makes_one_request_for_one_lap(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=0):
        seen.append(req)
        return _Resp(json.dumps([{"id": 7, "lap_time_ms": 83456, "psn_name": "Fast", "samples": _samples()}]).encode())

    monkeypatch.setattr(leaderboard.urllib.request, "urlopen", fake_urlopen)
    row = leaderboard.get_top_lap_ghost("Test Car", "Test Circuit")
    assert row["id"] == 7 and len(seen) == 1
    req = seen[0]
    assert req.get_method() == "GET" and req.data is None
    q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
    assert q["limit"] == ["1"] and q["order"] == ["lap_time_ms.asc"]
    assert q["car_name"] == ['eq."Test Car"'] and q["track_name"] == ['eq."Test Circuit"']
    assert q["created_at"][0].startswith("gte.2026-08-20")            # current physics era only
    assert "samples" in q["select"][0]
    assert "authorization" in {k.lower() for k in req.headers}


@pytest.mark.parametrize("body", ["[]", "{}", "not json", json.dumps([{"id": 1, "samples": []}])])
def test_top_lap_ghost_is_none_when_there_is_nothing_to_show(monkeypatch, body):
    monkeypatch.setattr(leaderboard.urllib.request, "urlopen", lambda req, timeout=0: _Resp(body.encode()))
    assert leaderboard.get_top_lap_ghost("c", "t") is None


def test_top_lap_ghost_is_none_offline(monkeypatch):
    def offline(req, timeout=0):
        raise OSError("no network")

    monkeypatch.setattr(leaderboard.urllib.request, "urlopen", offline)
    assert leaderboard.get_top_lap_ghost("c", "t") is None


# ── Dashboard: once per car+track, silent when it can't ──────────────────────
def _fake_app(car="Test Car", track="Test Circuit"):
    posted, logged = [], []
    app = SimpleNamespace(
        car_var=SimpleNamespace(get=lambda: car), track_var=SimpleNamespace(get=lambda: track),
        _ghosts={}, _ghost_fetching=set(), log_msg=logged.append,
        _post=lambda fn, *a: posted.append((fn, a)))
    app._ghost_key = lambda: dashboard.App._ghost_key(app)
    return app, posted, logged


def _run_fetch(app, monkeypatch, row):
    calls, done = [], threading.Event()

    def fake_fetch(car, track):
        calls.append((car, track))
        done.set()
        return row

    monkeypatch.setattr(leaderboard, "get_top_lap_ghost", fake_fetch)
    monkeypatch.setattr(config, "GHOST_ENABLED", True)
    dashboard.App._maybe_fetch_ghost(app)
    done.wait(0.3)
    return calls


def _drain(posted):
    for _ in range(100):
        if posted:
            break
        threading.Event().wait(0.02)
    for fn, args in posted:
        fn(*args)
    posted.clear()


def test_dashboard_fetches_once_per_car_and_track(monkeypatch):
    app, posted, logged = _fake_app()
    row = {"lap_time_ms": 83456, "psn_name": "Fast", "samples": _samples()}
    assert _run_fetch(app, monkeypatch, row) == [("Test Car", "Test Circuit")]
    _drain(posted)
    key = ("Test Car", "Test Circuit")
    assert isinstance(app._ghosts[key], ghost.Ghost)
    assert any("1:23.456" in m and "Fast" in m for m in logged)
    # polled again every 100 ms: no second request
    assert _run_fetch(app, monkeypatch, row) == []
    # a different track is a new session -> asked again
    app.track_var = SimpleNamespace(get=lambda: "Other Track")
    assert len(_run_fetch(app, monkeypatch, row)) == 1


def test_dashboard_skips_silently_when_nothing_to_show(monkeypatch):
    app, posted, logged = _fake_app()
    assert len(_run_fetch(app, monkeypatch, None)) == 1
    _drain(posted)
    assert app._ghosts[("Test Car", "Test Circuit")] is None and logged == []


def test_dashboard_needs_both_car_and_track_and_respects_the_toggle(monkeypatch):
    app, posted, _ = _fake_app(track="")
    assert _run_fetch(app, monkeypatch, {"samples": _samples(), "lap_time_ms": 1}) == []
    app, posted, _ = _fake_app()
    calls = []
    monkeypatch.setattr(leaderboard, "get_top_lap_ghost", lambda *a: calls.append(a))
    monkeypatch.setattr(config, "GHOST_ENABLED", False)
    dashboard.App._maybe_fetch_ghost(app)
    assert calls == [] and app._ghost_fetching == set()


def test_ghost_toggle_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_KEY_FILE", tmp_path / ".settings.key")
    monkeypatch.setattr(config, "GHOST_ENABLED", True)
    logged, label = [], {}
    fake = SimpleNamespace(ghost_var=SimpleNamespace(get=lambda: False), log_msg=logged.append,
                           ghost_lbl=SimpleNamespace(config=lambda **kw: label.update(kw)))
    dashboard.App._on_ghost_toggle(fake)
    assert config.GHOST_ENABLED is False and config.load()["GHOST_ENABLED"] is False
    assert label["text"] == "--" and "no leaderboard requests" in logged[0]
