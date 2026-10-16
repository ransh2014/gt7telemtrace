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
