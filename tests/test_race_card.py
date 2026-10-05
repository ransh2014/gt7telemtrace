"""Race card, lap card spec, per-lap Corners and the Consistency tab (pure logic + headless drawing)."""
import io
import json

import numpy as np
import pytest
from matplotlib.figure import Figure
from test_corners import _lap

from gt7telem import consistency, share_card
from gt7telem import lap_analyst as la
from gt7telem import race_analyst as ra


def _samples_for_lap(lap_no, t0, speed_drop, pit=False):
    """One synthetic 3 km lap (3 corners) as saved-sample dicts. lat_g is derived
    from ang_y on load, so ang_y is set to give ~1.2 g in the corners."""
    d = _lap(speed_drop=speed_drop)
    n = len(d)
    th = np.linspace(0, 2 * np.pi, n)
    out = []
    fuel = 90.0 - lap_no * 3.0
    for i in range(n):
        v = float(d["speed_kmh"].iloc[i])
        in_pit = pit and i >= n - 40
        if in_pit:
            fuel += 0.4
            v = 40.0
        out.append({
            "t": round(t0 + float(d["t"].iloc[i]), 4), "track_position": float(d["track_position"].iloc[i]),
            "speed_kmh": v, "throttle": float(d["throttle"].iloc[i]), "brake": float(d["brake"].iloc[i]),
            "ang_y": float(d["lat_g"].iloc[i]) * 9.81 / max(v / 3.6, 1.0), "steering": 0.0, "gear": 3,
            "rpm": 6000, "lap_number": lap_no, "fuel_remaining": fuel - i * 0.001, "fuel_capacity": 100.0,
            "world_x": float(400 * np.cos(th[i])), "world_z": float(250 * np.sin(th[i])),
            "tyre_temp_fl": 80, "tyre_temp_fr": 80, "tyre_temp_rl": 85, "tyre_temp_rr": 85,
        })
    return out, t0 + float(d["t"].iloc[-1]) + 0.1


def _race(tmp_path, drops=(0, 6, 12, 3, 20), pit_lap=None):
    samples, t0 = [], 0.0
    for k, drop in enumerate(drops, 1):
        s, t0 = _samples_for_lap(k, t0, drop, pit=(k == pit_lap))
        samples += s
    # a trailing partial lap, as a recording stopped before the line leaves
    s, t0 = _samples_for_lap(len(drops) + 1, t0, 0)
    samples += s[:60]
    data = {"car": "Test Car", "track": "test_circuit", "race_duration_s": round(t0, 3),
            "recorded_at": "20261002_141500", "samples": samples}
    p = tmp_path / "race.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return ra.load_race(p)


def _png_size(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def test_race_lap_frames_skip_the_partial_lap(tmp_path):
    _, df = _race(tmp_path)
    laps = ra.race_lap_frames(df)
    assert [n for n, _t, _f in laps] == [1, 2, 3, 4, 5]
    assert all(len(f) >= 20 for _n, _t, f in laps)
    # slower laps have the bigger speed drop
    times = {n: t for n, t, _f in laps}
    assert times[5] > times[2] > times[1]


def test_best_lap_baseline_is_the_fastest_lap(tmp_path):
    _, df = _race(tmp_path)
    label, frame, note = ra.best_lap_baseline(df)
    assert "lap 1" in label and frame is not None and note == ""


def test_baseline_without_complete_laps_says_so():
    import pandas as pd
    _label, frame, note = ra.best_lap_baseline(
        pd.DataFrame({"lap_number": [1, 1, 1], "t": [0, 1, 2], "speed_kmh": [1, 1, 1]}).assign(
            lat_g=0.0, track_position=[0, 1, 2], throttle=0, brake=0, steering=0))
    assert frame is None and "No complete lap" in note


def test_pb_baseline_missing_file_gives_a_helpful_note(tmp_path):
    label, frame, note = ra.pb_file_baseline({"car": "Test Car", "track": "test_circuit"}, tmp_path)
    assert frame is None and "No saved PB lap" in note and "reference_test_car" in note


def test_pb_baseline_loads_the_saved_reference_lap(tmp_path):
    samples, _ = _samples_for_lap(1, 0.0, 0)
    ref_dir = tmp_path / "test_circuit"
    ref_dir.mkdir()
    path = ra.pb_reference_path({"car": "Test Car", "track": "test_circuit"}, tmp_path)
    assert path.parent == ref_dir
    path.write_text(json.dumps({"lap_time_s": 59.5, "recorded_at": "20261002_141500", "samples": samples}),
                    encoding="utf-8")
    label, frame, note = ra.pb_file_baseline({"car": "Test Car", "track": "test_circuit"}, tmp_path)
    assert note == "" and frame is not None and "0:59.500" in label
    assert "lat_g" in frame.columns


def test_pb_baseline_refuses_a_pre_update_lap(tmp_path):
    samples, _ = _samples_for_lap(1, 0.0, 0)
    d = tmp_path / "test_circuit"
    d.mkdir()
    ra.pb_reference_path({"car": "c", "track": "test_circuit"}, tmp_path).write_text(
        json.dumps({"lap_time_s": 59.5, "recorded_at": "20260101_100000", "samples": samples}), encoding="utf-8")
    _l, frame, note = ra.pb_file_baseline({"car": "c", "track": "test_circuit"}, tmp_path)
    assert frame is None and "1.71" in note


def test_corner_scores_for_every_lap_against_the_best_lap(tmp_path):
    _, df = _race(tmp_path)
    _label, ref, _ = ra.best_lap_baseline(df)
    laps, ref_corners, scored = ra.score_race_corners(df, ref)
    assert len(ref_corners) == 3 and len(laps) == len(scored) == 5
    by_lap = {lap_id: deltas for lap_id, deltas in scored}
    assert all(abs(d.time_s) < 1e-6 for d in by_lap[1].values())          # lap 1 vs itself
    assert by_lap[5][2].time_s > by_lap[2][2].time_s > 0                  # slower laps lose more
    # cached: the same frame object is returned the second time
    assert ra.score_race_corners(df, ref)[2] is scored


def test_corner_baseline_modes(tmp_path):
    data, df = _race(tmp_path)
    assert ra.corner_baseline(data, df, "best")[1] is not None
    assert ra.corner_baseline(data, df, "pb", tmp_path)[1] is None


def test_pit_stops_and_fuel_per_lap(tmp_path):
    _, df = _race(tmp_path, pit_lap=3)
    assert ra.pit_stop_laps(df) == [3]
    burn = ra.lap_fuel_burn(df)
    assert [b[0] for b in burn] == [1, 2, 3, 4, 5]
    assert [b[2] for b in burn] == [False, False, True, False, False]
    assert all(b[1] > 0 for b in burn)
    _, df2 = _race(tmp_path)
    assert ra.pit_stop_laps(df2) == []


def test_race_card_spec_fills_the_four_blocks(tmp_path):
    data, df = _race(tmp_path, pit_lap=3)
    spec = ra.race_card_spec(data, df)
    assert spec["kind"] == "RACE" and spec["car"] == "Test Car" and spec["track"] == "Test Circuit"
    tiles = dict(spec["headline"]["tiles"])
    assert set(tiles) == {"BEST LAP", "AVG LAP", "CONSISTENCY", "LAPS", "TOP SPEED", "FUEL USED"}
    assert tiles["LAPS"] == "5" and tiles["BEST LAP"] != "--"
    assert spec["headline"]["big_label"] == "RACE TIME"
    chart = spec["chart"]
    assert chart["x"] == [1, 2, 3, 4, 5] and chart["best"] == 0 and len(chart["y"]) == 5
    assert spec["map"] is not None and len(spec["map"]["x"]) > 100
    assert spec["fuel"]["headline"].startswith("1 stop: lap 3")
    assert spec["fuel"]["bars"]["pit"][2] is True


def test_race_card_spec_with_no_pit_stop_and_no_gps(tmp_path):
    data, df = _race(tmp_path)
    df = df.assign(world_x=0.0, world_z=0.0)
    spec = ra.race_card_spec(data, df)
    assert spec["map"] is None and spec["fuel"]["headline"] == "no pit stops"


def test_race_card_is_a_1200x630_png(tmp_path):
    data, df = _race(tmp_path, pit_lap=3)
    assert _png_size(ra.race_card_png(data, df)) == (1200, 630)


def test_race_card_survives_a_bare_recording(tmp_path):
    """No laps at all (one short stint) must still give a card, not an exception."""
    samples = [{"t": i * 0.1, "speed_kmh": 100.0, "lap_number": 1, "fuel_remaining": 50.0} for i in range(40)]
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"car": "x", "track": "y", "samples": samples}), encoding="utf-8")
    data, df = ra.load_race(p)
    assert _png_size(ra.race_card_png(data, df)) == (1200, 630)


def test_lap_and_race_cards_share_one_renderer():
    assert la.share_card is ra.share_card is share_card
    assert la._fmt_laptime(59.9996) == share_card.fmt_time(59.9996) == "1:00.000"
    assert share_card.fmt_time(None) == "--:--.---"


def test_lap_card_spec_has_sectors_map_and_fuel():
    from test_udp_state import _card_lap
    data, df = _card_lap()
    spec = la.lap_card_spec(data, df)
    assert spec["kind"] == "LAP" and spec["headline"]["big_value"] == "1:23.456"
    assert len(spec["chart"]["sector_times"]) == 3 and len(spec["chart"]["edges"]) == 4
    assert spec["map"] is not None
    assert spec["fuel"]["headline"].endswith("used") and "trace" in spec["fuel"]
    names = [n for n, _ in spec["headline"]["tiles"]]
    assert names == ["TOP SPEED", "AVG SPEED", "FULL THROTTLE", "MAX LAT G", "AVG TYRE", "STYLE"]


def test_lap_card_spec_without_fuel_or_gps():
    from test_udp_state import _card_lap
    data, df = _card_lap(gps=False)
    df = df.assign(fuel_remaining=50.0)
    spec = la.lap_card_spec(data, df)
    assert spec["map"] is None and spec["fuel"]["headline"] == "no fuel data"


@pytest.mark.parametrize("draw", [ra.draw_corners, ra.draw_consistency])
def test_race_corner_and_consistency_charts_draw(tmp_path, draw):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    _, df = _race(tmp_path)
    fig = Figure(figsize=(13, 9))
    FigureCanvasAgg(fig)
    draw(fig, df)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    assert len(buf.getvalue()) > 5000


def test_corners_chart_explains_a_missing_pb(tmp_path):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    data, df = _race(tmp_path)
    fig = Figure()
    FigureCanvasAgg(fig)
    ra.draw_corners(fig, df, None, ra.corner_baseline(data, df, "pb", tmp_path))
    texts = [t.get_text() for ax in fig.axes for t in ax.texts]
    assert any("No saved PB lap" in t for t in texts)


def test_consistency_numbers_match_the_stats_module(tmp_path):
    data, df = _race(tmp_path)
    times = [t for _n, t, _f in ra.race_lap_frames(df)]
    stats = consistency.lap_time_stats(times)
    spec = ra.race_card_spec(data, df)
    cons = dict(spec["headline"]["tiles"])["CONSISTENCY"]
    assert f"±{stats['std']:.2f}s" in cons


def test_race_groups_registered():
    names = [g[0] for g in ra.GROUPS]
    assert names[-2:] == ["Corners", "Consistency"]
