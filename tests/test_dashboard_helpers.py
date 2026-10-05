from gt7telem import dashboard


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


def test_session_has_wall_start():
    assert hasattr(dashboard.session, "race_start_wall")


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
