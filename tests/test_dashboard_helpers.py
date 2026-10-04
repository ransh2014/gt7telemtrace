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
