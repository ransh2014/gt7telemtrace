import pandas as pd
from test_corners import _lap

from gt7telem import consistency, corners


def test_clean_laps_drop_the_slow_outlier():
    times = [90.0, 90.4, 125.0, 90.2, 89.9]  # lap 3 is a pit lap
    assert [i for i, _ in consistency.clean_laps(times)] == [0, 1, 3, 4]


def test_stats_on_steady_laps_score_high():
    s = consistency.lap_time_stats([90.0, 90.1, 89.9, 90.0, 90.05])
    assert s["n"] == 5 and s["n_clean"] == 5
    assert s["best"] == 89.9
    assert s["spread"] < 0.25 and s["score"] >= 90
    assert abs(s["trend"]) < 0.05


def test_stats_on_ragged_laps_score_low_and_trend_is_signed():
    ragged = consistency.lap_time_stats([90.0, 92.5, 90.4, 93.0, 91.0])
    steady = consistency.lap_time_stats([90.0, 90.1, 90.2, 90.1])
    assert ragged["score"] < steady["score"]
    slowing = consistency.lap_time_stats([90.0, 90.5, 91.0, 91.5])
    assert abs(slowing["trend"] - 0.5) < 1e-6
    assert consistency.trend_label(slowing["trend"]).startswith("slowing")
    assert consistency.trend_label(-0.4).startswith("speeding up")


def test_stats_with_too_few_laps_do_not_invent_a_score():
    one = consistency.lap_time_stats([90.0])
    assert one["score"] is None and one["trend"] is None and one["std"] == 0.0
    two = consistency.lap_time_stats([90.0, 90.3])
    assert two["score"] is None
    assert consistency.lap_time_stats([]) is None
    assert consistency.lap_time_stats([float("nan"), -3, 0]) is None
    assert consistency.trend_label(None) == "not enough laps"


def test_time_loss_summary_names_the_three_worst():
    _, _, deltas = corners.match_corners(_lap(speed_drop=15), _lap())
    worst = corners.time_loss_summary(deltas, 2)
    assert len(worst) == 2
    assert worst[0].time_s >= worst[1].time_s > 0
    assert corners.describe_time_loss(deltas).startswith("Time lost: T")


def test_time_loss_summary_empty_when_lap_matches_reference():
    _, _, deltas = corners.match_corners(_lap(), _lap())
    assert corners.time_loss_summary(deltas) == []
    assert "No corner" in corners.describe_time_loss(deltas)
    assert corners.describe_time_loss([]) == "No corner lost time against the reference"


def test_score_laps_keys_by_reference_corner_and_survives_bad_laps():
    ref = _lap()
    laps = [(1, _lap(speed_drop=5)), (2, _lap(speed_drop=10)), (3, _lap(speed_drop=20)), (4, pd.DataFrame())]
    ref_corners, scored = corners.score_laps(laps, ref)
    assert len(ref_corners) == 3
    assert [lap_id for lap_id, _ in scored] == [1, 2, 3, 4]
    assert set(scored[0][1]) == {1, 2, 3}
    assert scored[3][1] == {}
    # slower laps lose more time in each corner
    assert scored[2][1][2].time_s > scored[0][1][2].time_s


def test_corner_spreads_rank_by_lap_to_lap_variation():
    ref = _lap()
    # corner 1 is identical every lap; the others vary with the lap's speed drop
    laps = [(i, _lap(speed_drop=d)) for i, d in enumerate([0, 8, 16, 24], 1)]
    _, scored = corners.score_laps(laps, ref)
    spreads = corners.corner_spreads(scored)
    assert len(spreads) == 3
    assert spreads == sorted(spreads, key=lambda c: c.std_time_s)
    assert all(c.n == 4 and c.std_time_s > 0 for c in spreads)
    # needs min_laps matches: two laps are not enough
    assert corners.corner_spreads(scored[:2]) == []
