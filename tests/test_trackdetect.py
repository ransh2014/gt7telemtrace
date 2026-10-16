import math

from gt7telem import trackdetect as td

SUGO = "SUGO International Racing Course"


def _lap(x0=100.0, z0=-200.0, heading=10.0, length=3586.0, n=400):
    """Straight-ish synthetic lap leaving (x0,z0) at `heading` degrees."""
    h = math.radians(heading)
    step = length / n
    return [{"world_x": x0 + math.cos(h) * step * i, "world_z": z0 + math.sin(h) * step * i}
            for i in range(n)]


def _store(tmp_path):
    return td.FingerprintStore(tmp_path / "a.json", tmp_path / "b.json")


def test_fingerprint_position_and_heading():
    fp = td.fingerprint_of(_lap())
    assert abs(fp["x"] - 100.0) < 0.2 and abs(fp["z"] + 200.0) < 0.2
    assert td.angle_diff(fp["heading"], 10.0) < 0.5


def test_fingerprint_needs_movement_and_coords():
    assert td.fingerprint_of([]) is None
    assert td.fingerprint_of([{"world_x": 0, "world_z": 0}] * 5) is None
    assert td.fingerprint_of([{"world_x": 5, "world_z": 5}] * 5) is None


def test_angle_diff_wraps():
    assert td.angle_diff(359, 1) == 2


def test_learn_and_match(tmp_path):
    st = _store(tmp_path)
    fp = td.fingerprint_of(_lap())
    assert st.add(SUGO, fp) is True
    assert st.add(SUGO, fp) is False            # near-identical is not stored twice
    near = td.fingerprint_of(_lap(x0=104.0, z0=-197.0, heading=18.0))
    assert st.match(near)[0][0] == SUGO
    far = td.fingerprint_of(_lap(x0=400.0))
    assert st.match(far) == []
    assert st.match(td.fingerprint_of(_lap(heading=100.0))) == []   # other direction


def test_store_persists(tmp_path):
    _store(tmp_path).add(SUGO, td.fingerprint_of(_lap()))
    assert _store(tmp_path).known_layouts() == [SUGO]


def test_shared_rows_match(tmp_path):
    st = _store(tmp_path)
    st.set_shared([{"layout_name": SUGO, "x": 100, "z": -200, "heading": 10}, {"bad": 1}])
    assert st.match({"x": 101, "z": -199, "heading": 12})[0][0] == SUGO
    assert _store(tmp_path).known_layouts() == [SUGO]


def test_shortlist_by_length():
    assert SUGO in td.shortlist_by_length(3586)
    assert td.shortlist_by_length(0) == []
    assert td.shortlist_by_length(1) == []


def test_can_learn_rejects_wrong_length():
    assert td.can_learn(SUGO, _lap(length=3586))
    assert not td.can_learn(SUGO, _lap(length=2000))
    assert not td.can_learn("Nowhere", _lap())


def test_reverse_twin_is_symmetric_when_present():
    for name in list(td.layouts())[:200]:
        t = td.reverse_twin(name)
        if t:
            assert td.reverse_twin(t) == name

def _race(laps=3, per=400, lap0=0):
    """Race samples: optional lap-0 countdown, a grid run-up far from the line, then
    laps that each start on the line (the lap counter ticks up there)."""
    s = [{"lap_number": 0, "world_x": -300.0, "world_z": 50.0}] * lap0
    s += [{"lap_number": 1, "world_x": -300.0 + i, "world_z": 50.0} for i in range(60)]
    for k in range(laps):
        for smp in _lap(length=3586, n=per):
            s.append(dict(smp, lap_number=k + 2))
    return s


def test_rollover_and_race_fingerprints(tmp_path):
    st = _store(tmp_path)
    st.add(SUGO, td.fingerprint_of(_lap()))
    for lap0 in (0, 20):                       # with and without a lap-0 countdown
        race = _race(lap0=lap0)
        assert td.rollover_indexes(race, 2)
        hits = [h for fp in td.race_fingerprints(race) for h in st.match(fp)]
        assert hits and hits[0][0] == SUGO
    assert SUGO in td.shortlist_by_length(td.race_lap_lengths(_race())[0])


def test_race_step_offers_the_track_at_the_line(tmp_path, monkeypatch):
    st = _store(tmp_path)
    st.add(SUGO, td.fingerprint_of(_lap()))
    shown = []
    from types import SimpleNamespace

    from gt7telem import dashboard
    app = SimpleNamespace(track_var=SimpleNamespace(get=lambda: ""), _td_store=st,
                          _td_rejected=set(), _show_track_offer=shown.append)
    dashboard.App._td_race_reset(app)
    monkeypatch.setattr(dashboard.session, "race_samples", [], raising=False)
    for smp in _race(laps=1):
        dashboard.session.race_samples.append(smp)
        dashboard.App._td_race_step(app)
    assert shown == [SUGO]
    assert app._td_race_done
