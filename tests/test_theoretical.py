import numpy as np
import pandas as pd

from gt7telem import theoretical


def _lap(speeds, length=3000.0, step=5.0):
    """A lap where speed (km/h) is a function of position: `speeds(pos) -> km/h`."""
    pos = np.arange(0.0, length, step)
    v = np.array([speeds(p) for p in pos]) / 3.6
    t = np.concatenate([[0.0], np.cumsum(step / v[1:])])
    return pd.DataFrame({"track_position": pos, "t": t, "speed_kmh": v * 3.6})


FAST, SLOW = 180.0, 150.0


def test_stitching_two_complementary_laps_beats_both():
    a = _lap(lambda p: FAST if p < 1500 else SLOW)      # quick first half
    b = _lap(lambda p: SLOW if p < 1500 else FAST)      # quick second half
    r = theoretical.theoretical_best([("A", a), ("B", b)])
    assert r is not None and len(r["labels"]) == 2
    assert r["theoretical"] < r["actual"] - 1.0
    assert abs(r["gap"] - (r["actual"] - r["theoretical"])) < 1e-9
    # all fast: 3000 m at 180 km/h = 60 s
    assert abs(r["theoretical"] - 3000 / (FAST / 3.6)) < 0.3


def test_the_best_real_lap_is_held_back_where_the_other_lap_is_quicker():
    a = _lap(lambda p: FAST if p < 1500 else SLOW)
    b = _lap(lambda p: SLOW if p < 1500 else FAST)
    r = theoretical.theoretical_best([("A", a), ("B", b)])
    best, other = r["best_idx"], 1 - r["best_idx"]      # the two totals are within a hair of each other
    worst_zone = r["zones"][0]
    # the best lap is slow in one half (A: the second, B: the first); the other lap donates it
    assert worst_zone["donor"] == other
    assert worst_zone["start"] >= 1400 if best == 0 else worst_zone["end"] <= 1600
    # flip it: now B is the quicker lap overall, and is held back in the other half
    c = _lap(lambda p: SLOW if p < 1500 else FAST * 1.02)
    r2 = theoretical.theoretical_best([("A", a), ("C", c)])
    assert r2["labels"][r2["best_idx"]] == "C" and r2["zones"][0]["end"] <= 1600 and r2["zones"][0]["donor"] == 0
    assert r["zones"] == sorted(r["zones"], key=lambda z: z["loss"], reverse=True)
    assert abs(sum(z["loss"] for z in r["zones"]) - r["gap"]) < 0.02
    assert abs(r["cum_gap"][-1] - r["gap"]) < 1e-9


def test_identical_laps_have_no_gap_and_no_zones():
    a = _lap(lambda p: 160.0)
    r = theoretical.theoretical_best([("A", a), ("B", a.copy())])
    assert r["gap"] < 1e-9 and r["zones"] == []


def test_needs_two_usable_laps_of_the_same_layout():
    a = _lap(lambda p: 160.0)
    assert theoretical.theoretical_best([("A", a)]) is None
    assert theoretical.theoretical_best([]) is None
    assert theoretical.theoretical_best([("A", a), ("B", pd.DataFrame({"x": [1]}))]) is None
    longer = _lap(lambda p: 160.0, length=3600.0)       # another layout: 20 % longer
    assert theoretical.theoretical_best([("A", a), ("B", a.copy()), ("C", longer)])["skipped"] == 1
    assert theoretical.theoretical_best([("A", a), ("C", longer)]) is None     # two laps, two layouts: nothing to stitch


def test_laps_of_slightly_different_length_still_line_up():
    a = _lap(lambda p: FAST if p < 1500 else SLOW, length=3000.0)
    b = _lap(lambda p: SLOW if p < 1515 else FAST, length=3030.0)    # +1 %, recorded a little long
    r = theoretical.theoretical_best([("A", a), ("B", b)])
    assert r is not None and r["skipped"] == 0 and r["gap"] > 0


def test_usable_rejects_short_or_stationary_laps():
    assert not theoretical.usable(None)
    assert not theoretical.usable(pd.DataFrame({"track_position": [0, 1], "t": [0, 1]}))
    assert not theoretical.usable(pd.DataFrame({"track_position": np.zeros(50), "t": np.arange(50.0)}))
    assert theoretical.usable(_lap(lambda p: 150.0))


# ── Lap Analyst wiring ────────────────────────────────────────────────────────
def _row(car, track, t, path, incomplete=False, current=True):
    return {"car": car, "track": track, "time_s": t, "path": path, "incomplete": incomplete,
            "current_era": current, "recorded_at": "20261002_141500"}


def test_candidates_are_the_fastest_same_car_and_track_laps_without_lap_a_itself():
    from gt7telem import lap_analyst as la
    rows = [_row("Car A", "Spa", 142.0, "/l/1.json"), _row("Car A", "Spa", 140.0, "/l/2.json"),
            _row("Car A", "Spa", 139.0, "/l/a.json"),                      # lap A's own file
            _row("car a", "SPA", 141.0, "/l/3.json"),                      # same, other capitalisation
            _row("Car B", "Spa", 130.0, "/l/4.json"),                      # other car
            _row("Car A", "Monza", 80.0, "/l/5.json"),                     # other track
            _row("Car A", "Spa", 120.0, "/l/6.json", incomplete=True),
            _row("Car A", "Spa", 125.0, "/l/7.json", current=False)]
    got = la.theory_candidates(rows, {"car_display": "Car A", "track_display": "Spa",
                                      "recorded_at": "20261002_141500"}, "/l/a.json", 3)
    assert [r["path"] for r in got] == ["/l/2.json", "/l/3.json", "/l/1.json"]
    assert [r["path"] for r in la.theory_candidates(rows, {"car": "Car A", "track": "Spa"}, None, 1)] == ["/l/a.json"]


def test_theoretical_chart_draws_and_explains_when_there_is_nothing_to_stitch():
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from gt7telem import lap_analyst as la
    a = _lap(lambda p: FAST if p < 1500 else SLOW)
    b = _lap(lambda p: SLOW if p < 1500 else FAST)
    fig = Figure(figsize=(12, 9))
    FigureCanvasAgg(fig)
    la.draw_theoretical(fig, a, None, [("Lap A", a), ("Lap B", b), ("old", a.copy())])
    texts = " ".join(t.get_text() for ax in fig.axes for t in ax.texts)
    assert "Theoretical" in texts and "Held back most at" in texts
    fig2 = Figure()
    FigureCanvasAgg(fig2)
    la.draw_theoretical(fig2, a)
    assert any("at least two" in t.get_text() for ax in fig2.axes for t in ax.texts)


def test_lap_analyst_registers_the_theoretical_group():
    from gt7telem import lap_analyst as la
    assert la.GROUPS[-1][0] == "Theoretical"
