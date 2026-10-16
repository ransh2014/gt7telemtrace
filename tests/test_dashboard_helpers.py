import json as _json
import random
from collections import deque
from types import SimpleNamespace

import pytest

from gt7telem import dashboard, lapclean


def test_next_due_keeps_rate_despite_jitter():
    dt = 1 / 60
    due = dashboard._next_due(0.0, 100.0, dt)
    assert abs(due - (100.0 + dt)) < 1e-9
    # tick runs 5 ms late: next deadline is still anchored to the old one
    nxt = dashboard._next_due(due, due + 0.005, dt)
    assert abs(nxt - (due + dt)) < 1e-9


def test_next_due_resyncs_after_stall():
    dt = 0.1
    nxt = dashboard._next_due(10.0, 15.0, dt)
    assert abs(nxt - 15.1) < 1e-9


def test_effective_rate_with_coarse_timer():
    dt, due, n, t = 1 / 60, 0.0, 0, 0.0
    while t < 10.0:
        t += 0.0156   # ~15.6 ms Windows timer tick
        if t >= due:
            due = dashboard._next_due(due, t, dt)
            n += 1
    assert n >= 10 * 55   # old logic gave ~32 Hz


def test_unknown_car_id_replaces_the_previous_cars_name(monkeypatch):
    names = {3612: "Chaser Tourer V '97"}
    monkeypatch.setattr(dashboard.car_db, "get_car_name", lambda i: names.get(int(i), ""))
    # known car: its proper name
    assert dashboard.car_box_autofill(3612, "", None) == "Chaser Tourer V '97"
    # switch to a car ID missing from car_ids.csv: never keep the old name
    assert dashboard.car_box_autofill(9999, "Chaser Tourer V '97", 3612) == "Unknown car (ID 9999)"
    assert dashboard.unknown_car_label(9999) == "Unknown car (ID 9999)"
    # no car reported: leave the box alone
    assert dashboard.car_box_autofill(0, "anything", 3612) is None


def test_manual_car_name_edit_sticks_for_the_same_car(monkeypatch):
    monkeypatch.setattr(dashboard.car_db, "get_car_name", lambda i: "")
    assert dashboard.car_box_autofill(9999, "Ferrari F355 Berlinetta '94", 9999) is None
    # ...but an emptied box is filled again
    assert dashboard.car_box_autofill(9999, "  ", 9999) == "Unknown car (ID 9999)"


def test_update_check_toggle_persists(tmp_path, monkeypatch):
    """The header checkbox writes config.UPDATE_CHECK_ENABLED (which launcher.py
    reads at startup) and saves it to settings.json."""
    from types import SimpleNamespace

    from gt7telem import config

    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_KEY_FILE", tmp_path / ".settings.key")
    monkeypatch.setattr(config, "UPDATE_CHECK_ENABLED", True)
    logged = []
    fake = SimpleNamespace(update_var=SimpleNamespace(get=lambda: False), log_msg=logged.append)

    dashboard.App._on_update_check_toggle(fake)
    assert config.UPDATE_CHECK_ENABLED is False
    assert config.load()["UPDATE_CHECK_ENABLED"] is False
    assert logged and "off" in logged[0]

    fake.update_var = SimpleNamespace(get=lambda: True)
    dashboard.App._on_update_check_toggle(fake)
    assert config.UPDATE_CHECK_ENABLED is True
    assert config.load()["UPDATE_CHECK_ENABLED"] is True


def test_notifications_toggle_persists_and_gates_notify(tmp_path, monkeypatch):
    """The NOTIFICATIONS checkbox writes config.NOTIFY_ENABLED, which notify.notify() checks each call."""
    from types import SimpleNamespace

    from gt7telem import config, notify

    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_KEY_FILE", tmp_path / ".settings.key")
    monkeypatch.setattr(config, "NOTIFY_ENABLED", True)
    beeps = []
    monkeypatch.setattr(notify, "_beep", lambda root=None: beeps.append(1))
    logged = []
    fake = SimpleNamespace(notify_var=SimpleNamespace(get=lambda: False), log_msg=logged.append)

    dashboard.App._on_notify_toggle(fake)
    assert config.NOTIFY_ENABLED is False and config.load()["NOTIFY_ENABLED"] is False
    assert "off" in logged[-1]
    notify.notify("PB", "x")
    assert beeps == []                                   # switched off: no sound, no toast

    fake.notify_var = SimpleNamespace(get=lambda: True)
    dashboard.App._on_notify_toggle(fake)
    assert config.NOTIFY_ENABLED is True and config.load()["NOTIFY_ENABLED"] is True
    notify.notify("PB", "x")
    assert beeps == [1]


def test_lap_clock_runs_only_on_a_real_lap():
    run = dashboard.lap_clock_running
    assert run(1, 5, False, True, False)          # mid-race
    assert run(3, 0, False, True, False)          # time trial / practice (no lap total)
    assert not run(0, 5, False, True, False)      # menu / garage (no lap yet)
    assert not run(2, 5, True, True, False)       # paused
    assert not run(2, 5, False, False, False)     # off track
    assert not run(2, 5, False, True, True)       # loading screen


def test_lap_clock_stops_after_the_final_lap():
    run = dashboard.lap_clock_running
    assert run(10, 10, False, True, False)        # still on the last lap
    assert not run(11, 10, False, True, False)    # crossed the line: 11/10 on the results run


# -- per-packet recording (UDP thread decides, the Tk tick only drains) ----------

def _simulate(rate_hz, seconds=60.0, packet_hz=60.0, jitter=0.002, seed=1):
    rng = random.Random(seed)
    dt, due, n = 1.0 / rate_hz, 0.0, 0
    for i in range(int(seconds * packet_hz)):
        now = 1000.0 + i / packet_hz + rng.uniform(-jitter, jitter)
        ok, due = dashboard._thin_accept(due, now, dt)
        n += ok
    return n / seconds


@pytest.mark.parametrize("rate", [10, 20, 30, 60])
def test_thin_accept_hits_the_chosen_rate_on_a_60hz_stream(rate):
    assert _simulate(rate) == pytest.approx(rate, rel=0.02)


def test_thin_accept_records_the_first_packet_and_resyncs_after_a_stall():
    ok, due = dashboard._thin_accept(0.0, 50.0, 0.1)
    assert ok and due == pytest.approx(50.1)
    ok, due = dashboard._thin_accept(10.0, 15.0, 0.1)
    assert ok and due == pytest.approx(15.1)


def test_thin_accept_skips_packets_that_are_clearly_early():
    ok, due = dashboard._thin_accept(10.0, 9.9, 0.1)
    assert not ok and due == 10.0


def _fake_app(rate=60):
    app = SimpleNamespace(
        _closing=False, _record_rate=rate, _rec_due=0.0, _rec_due_race=0.0,
        _rec_q=deque(maxlen=dashboard.RECORD_QUEUE_MAX), _lap_rec_since=0.0,
        _race_rec_since=0.0, _last_record_error=None,
        lap_got=[], race_got=[], after_calls=[], logs=[],
    )
    app._record_tick = lambda: None
    app.after = lambda *a, **k: app.after_calls.append(a)
    app.log_msg = app.logs.append
    app._record_sample = lambda d, now=None: app.lap_got.append((d, now))
    app._record_race_sample = lambda d, now=None: app.race_got.append((d, now))
    return app


def _set_session(monkeypatch, *, recording=False, waiting=False, race=False, paused=False):
    for name, value in (("recording", recording), ("waiting", waiting),
                        ("race_recording", race), ("paused", paused)):
        monkeypatch.setattr(dashboard.session, name, value, raising=False)


def test_every_packet_is_recorded_at_60hz_with_its_own_arrival_time(monkeypatch):
    _set_session(monkeypatch, race=True)
    app = _fake_app(60)
    for i in range(600):
        dashboard.App._on_packet_tap(app, {"i": i}, 100.0 + i / 60)
    dashboard.App._record_tick(app)
    assert [d["i"] for d, _ in app.race_got] == list(range(600))
    assert app.race_got[10][1] == pytest.approx(100.0 + 10 / 60)
    assert not app._rec_q and app.lap_got == []


def test_the_tap_thins_to_the_chosen_rate(monkeypatch):
    _set_session(monkeypatch, recording=True)
    app = _fake_app(30)
    for i in range(600):
        dashboard.App._on_packet_tap(app, {"i": i}, 100.0 + i / 60)
    dashboard.App._record_tick(app)
    assert len(app.lap_got) == pytest.approx(300, abs=1)


def test_the_tap_ignores_packets_while_paused_or_not_recording(monkeypatch):
    app = _fake_app()
    _set_session(monkeypatch)
    dashboard.App._on_packet_tap(app, {}, 1.0)
    _set_session(monkeypatch, race=True, paused=True)
    dashboard.App._on_packet_tap(app, {}, 2.0)
    assert not app._rec_q


def test_the_tick_drops_packets_from_before_a_new_recording_started(monkeypatch):
    _set_session(monkeypatch, recording=True, race=True)
    app = _fake_app()
    app._rec_q.extend([("lap", 5.0, {}), ("race", 5.0, {}), ("lap", 9.0, {}), ("race", 9.0, {})])
    app._lap_rec_since = app._race_rec_since = 8.0
    dashboard.App._record_tick(app)
    assert [n for _, n in app.lap_got] == [9.0] and [n for _, n in app.race_got] == [9.0]


def test_the_tick_keeps_rescheduling_when_a_sample_handler_raises(monkeypatch):
    _set_session(monkeypatch, race=True)
    app = _fake_app()
    app._record_race_sample = lambda d, now=None: 1 / 0
    app._rec_q.append(("race", 1.0, {}))
    dashboard.App._record_tick(app)
    assert len(app.after_calls) == 1 and "ZeroDivisionError" in app.logs[0]

# ---- auto lap ----

def _auto_app(monkeypatch, enabled=True):
    monkeypatch.setattr(dashboard.runtime_config, "AUTO_LAP_ENABLED", enabled, raising=False)
    app = SimpleNamespace(_auto_lap_since=None, _auto_lap_off_since=None,
                          _auto_lap_active=False, _auto_lap_block=False,
                          logs=[], started=0)
    app.log_msg = app.logs.append
    app.rec_btn = SimpleNamespace(config=lambda **k: None)
    app._warn_no_track = lambda modal: None

    def start():
        app.started += 1
        monkeypatch.setattr(dashboard.session, "waiting", True, raising=False)
    app._start_record = start
    app._auto_lap_disarm = lambda why: dashboard.App._auto_lap_disarm(app, why)
    monkeypatch.setattr(dashboard.session, "samples", [], raising=False)
    return app


_DRIVING = {"car_on_track": True, "speed_kmh": 80.0, "total_laps": 0}


def test_auto_lap_helpers():
    assert dashboard.auto_lap_can_arm(_DRIVING)
    assert not dashboard.auto_lap_can_arm({**_DRIVING, "speed_kmh": 0.0})
    assert not dashboard.auto_lap_can_arm({**_DRIVING, "paused": True})
    assert not dashboard.auto_lap_can_arm({**_DRIVING, "total_laps": 5})
    assert not dashboard.auto_lap_can_arm({**_DRIVING, "car_on_track": False})
    assert not dashboard.auto_lap_can_arm({**_DRIVING, "loading": True})


def test_auto_lap_off_by_default_and_never_arms_when_disabled(monkeypatch):
    from gt7telem import config
    assert config._DEFAULTS["AUTO_LAP_ENABLED"] is False
    _set_session(monkeypatch)
    app = _auto_app(monkeypatch, enabled=False)
    for i in range(50):
        dashboard.App._auto_lap_step(app, _DRIVING, i * 0.1)
    assert app.started == 0


def test_auto_lap_arms_after_hold_and_only_once(monkeypatch):
    _set_session(monkeypatch)
    app = _auto_app(monkeypatch)
    for i in range(40):
        dashboard.App._auto_lap_step(app, _DRIVING, i * 0.1)
    assert app.started == 1 and app._auto_lap_active


def test_auto_lap_does_not_arm_in_a_race_or_when_user_armed(monkeypatch):
    _set_session(monkeypatch)
    app = _auto_app(monkeypatch)
    for i in range(40):
        dashboard.App._auto_lap_step(app, {**_DRIVING, "total_laps": 3}, i * 0.1)
    assert app.started == 0
    _set_session(monkeypatch, recording=True)
    for i in range(40):
        dashboard.App._auto_lap_step(app, _DRIVING, i * 0.1)
    assert app.started == 0 and not app._auto_lap_active


def test_auto_lap_disarms_when_session_left_and_discards_partial(monkeypatch):
    _set_session(monkeypatch)
    app = _auto_app(monkeypatch)
    for i in range(20):
        dashboard.App._auto_lap_step(app, _DRIVING, i * 0.1)
    assert app._auto_lap_active
    monkeypatch.setattr(dashboard.session, "samples", [1, 2, 3], raising=False)
    menu = {"car_on_track": False, "speed_kmh": 0.0, "total_laps": 0}
    for i in range(20, 60):
        dashboard.App._auto_lap_step(app, menu, i * 0.1)
    assert not app._auto_lap_active
    assert dashboard.session.samples == [] and not dashboard.session.waiting


def test_auto_lap_user_stop_blocks_until_session_left(monkeypatch):
    _set_session(monkeypatch)
    app = _auto_app(monkeypatch)
    for i in range(20):
        dashboard.App._auto_lap_step(app, _DRIVING, i * 0.1)
    app._auto_lap_active = False       # what _stop_record does to an auto lap
    app._auto_lap_block = True
    _set_session(monkeypatch)
    for i in range(20, 80):
        dashboard.App._auto_lap_step(app, _DRIVING, i * 0.1)
    assert app.started == 1
    menu = {"car_on_track": False, "speed_kmh": 0.0, "total_laps": 0}
    dashboard.App._auto_lap_step(app, menu, 9.0)
    for i in range(100, 130):
        dashboard.App._auto_lap_step(app, _DRIVING, i * 0.1)
    assert app.started == 2

# ---- low battery + track warnings ----

def test_battery_needs_warning_rules():
    f = dashboard.battery_needs_warning
    assert f((15.0, False), True)
    assert not f((15.0, True), True)        # charging
    assert not f((60.0, False), True)       # plenty left
    assert not f((15.0, False), False)      # nothing being recorded
    assert not f(None, True)                # no battery (desktop)


def test_battery_alarm_due_after_delay_unless_acked():
    f = dashboard.battery_alarm_due
    assert not f(None, 100.0, False)
    assert not f(0.0, dashboard.BATTERY_ALARM_AFTER_S - 1, False)
    assert f(0.0, dashboard.BATTERY_ALARM_AFTER_S, False)
    assert not f(0.0, 999.0, True)


def _batt_app(monkeypatch, batt, active=True):
    monkeypatch.setattr(dashboard, "read_battery", lambda: batt)
    _set_session(monkeypatch, recording=active)
    app = SimpleNamespace(_batt_next=0.0, _batt_warned_at=None, _batt_ack=False,
                          _batt_crit=False, logs=[], beeps=[], shown=0, hidden=0)
    app.log_msg = app.logs.append
    app._battery_beep = lambda loud: app.beeps.append(loud)
    app._show_battery_bar = lambda pct: setattr(app, "shown", app.shown + 1)
    app._hide_battery_bar = lambda: setattr(app, "hidden", app.hidden + 1)
    return app


def test_battery_step_warns_then_sirens_if_ignored_then_clears(monkeypatch):
    app = _batt_app(monkeypatch, (18.0, False))
    dashboard.App._battery_step(app, 0.0)
    assert app.beeps == [False] and app._batt_warned_at == 0.0
    dashboard.App._battery_step(app, 10.0)                       # still inside the grace period
    assert app.beeps == [False]
    dashboard.App._battery_step(app, dashboard.BATTERY_ALARM_AFTER_S + 1)
    assert app.beeps[-1] is True
    monkeypatch.setattr(dashboard, "read_battery", lambda: (18.0, True))   # charger plugged in
    dashboard.App._battery_step(app, 100.0)
    assert app._batt_warned_at is None and app.hidden == 1


def test_battery_step_ack_silences_the_siren(monkeypatch):
    app = _batt_app(monkeypatch, (18.0, False))
    dashboard.App._battery_step(app, 0.0)
    app._batt_ack = True
    dashboard.App._battery_step(app, 200.0)
    assert app.beeps == [False]


def test_battery_step_ignored_when_not_recording(monkeypatch):
    app = _batt_app(monkeypatch, (5.0, False), active=False)
    dashboard.App._battery_step(app, 0.0)
    assert app.beeps == [] and app._batt_warned_at is None


def test_ask_track_skipped_after_save_as_unknown_was_chosen():
    app = SimpleNamespace(_save_unknown_ok=True)
    assert dashboard.App._ask_track_for_save(app, "lap") == ""

# ---- junk-lap guards + duplicate cleanup ----


def _lap_samples(n=200, speed=120.0):
    return [{"t": i / 60, "speed_kmh": speed, "fuel_remaining": 50 - i * 0.001} for i in range(n)]


def test_junk_reason_flags_short_parked_and_tiny_laps():
    assert lapclean.junk_reason(_lap_samples(30))
    assert lapclean.junk_reason(_lap_samples(200, speed=0.0)) == "the car never moved"
    assert "s long" in lapclean.junk_reason(_lap_samples(200), lap_seconds=2.0)
    assert "m driven" in lapclean.junk_reason(_lap_samples(200), 60.0, 40.0)
    assert lapclean.junk_reason(_lap_samples(200), 60.0, 3500.0) is None


def test_samples_signature_same_lap_same_signature():
    a = lapclean.samples_signature("t", "c", 60.123, _lap_samples())
    b = lapclean.samples_signature("t", "c", 60.123, _lap_samples())
    c = lapclean.samples_signature("t", "c", 61.0, _lap_samples())
    assert a == b and a != c


def _write_lap(folder, name, samples, recorded_at, lap_time=60.0):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(_json.dumps({
        "recorded_at": recorded_at, "track": "t", "car": "c", "lap_time_s": lap_time,
        "total_samples": len(samples), "samples": samples}), encoding="utf-8")


def test_find_and_move_duplicates_keeps_earliest_and_never_deletes(tmp_path):
    s = _lap_samples()
    tdir = tmp_path / "track_a"
    _write_lap(tdir, "c_20260101_100000.json", s, "20260101_100000")
    _write_lap(tdir, "c_20260101_100500.json", s, "20260101_100500")          # exact copy
    _write_lap(tdir, "c_20260101_101000.json", _lap_samples(201), "20260101_101000", 61.0)  # different lap
    _write_lap(tdir, "reference_c.json", s, "20260101_100000")                 # intentional copy: ignored
    groups = lapclean.find_duplicates(tmp_path)
    assert len(groups) == 1
    keep, extras = groups[0]
    assert keep.name == "c_20260101_100000.json"
    assert [p.name for p in extras] == ["c_20260101_100500.json"]
    moved, errors = lapclean.move_duplicates(groups, tmp_path)
    assert not errors and len(moved) == 1
    assert (tmp_path / lapclean.DUP_FOLDER / "track_a" / "c_20260101_100500.json").exists()
    assert (tdir / "c_20260101_100000.json").exists() and (tdir / "reference_c.json").exists()
    assert lapclean.find_duplicates(tmp_path) == []        # second scan: nothing left, and _duplicates is skipped


def test_move_duplicates_never_overwrites(tmp_path):
    s = _lap_samples()
    d = tmp_path / "t"
    _write_lap(d, "a.json", s, "1")
    _write_lap(d, "b.json", s, "2")
    dup = tmp_path / lapclean.DUP_FOLDER / "t"
    dup.mkdir(parents=True)
    (dup / "b.json").write_text("old", encoding="utf-8")
    moved, errors = lapclean.move_duplicates(lapclean.find_duplicates(tmp_path), tmp_path)
    assert not errors and (dup / "b.json").read_text(encoding="utf-8") == "old"
    assert (dup / "b_1.json").exists()
