from gt7telem import engineer as e


def snap(**kw):
    s = {"laps_of_fuel": 6.0, "stop_needed": True, "pace_loss_s": 2.0, "fuel_per_lap": 2.0}
    s.update(kw)
    return s


def test_window_latest_is_fuel_limited():
    w = e.pit_window(lap=3, total_laps=20, snap=snap())
    assert w.latest == 3 + int(6.0 - 0.5) - 1
    assert w.earliest == 3 and "repays" in w.reason


def test_no_stop_when_fuel_enough():
    w = e.pit_window(3, 10, snap(stop_needed=False))
    assert w.earliest is None and w.latest is None and "no stop" in w.reason.lower()


def test_late_in_race_a_stop_does_not_repay():
    w = e.pit_window(lap=19, total_laps=20, snap=snap())
    assert w.earliest is None and "repay" in w.reason


def test_undercut_works_and_fails():
    assert e.undercut(2.0, 1.2, 3)["works"] is True
    assert e.undercut(6.0, 1.0, 2)["works"] is False
    assert e.undercut(None, 1.0)["works"] is None


def test_overcut():
    assert e.overcut(8.0, 0.4, 2)["works"] is True
    assert e.overcut(0.5, 1.0, 3)["works"] is False


def test_simulate_finish_counts_cars_that_pass():
    r = e.simulate_finish(3, 8, 10, 20, 90.0, 1, pit_loss_s=20.0, rival_gaps_s=[-5, -2, 8, 15, 40])
    assert r["finish"] == 2 + 2 + 1       # two ahead stay ahead, two within 20 s behind pass us
    assert "P5" in r["text"]
    assert "stop" in e.simulate_finish(1, 1, 5, 10, 90, 2)["text"]


def test_pit_call_sequence():
    s = snap()
    w = e.pit_window(3, 20, s)
    assert e.pit_call(w.latest - 1, 20, s, w)[1] == "warn"
    msg, kind = e.pit_call(w.latest, 20, s, w)
    assert kind == "box" and "Box" in msg
    assert e.pit_call(w.latest, 20, s, w, last_called=w.latest) is None
