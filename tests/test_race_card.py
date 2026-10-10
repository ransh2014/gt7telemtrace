"""Race card, lap card spec, per-lap Corners and the Consistency tab (pure logic + headless drawing)."""
import io
import json

import numpy as np
import pytest
from matplotlib.figure import Figure
from test_corners import _lap

from gt7telem import share_card
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


def test_race_card_spec_shows_only_recorded_values(tmp_path):
    data, df = _race(tmp_path, pit_lap=3)
    spec = ra.race_card_spec(data, df)
    assert spec["kind"] == "RACE" and spec["car"] == "Test Car" and spec["track"] == "test circuit"
    assert spec["headline"]["big_label"] == "BEST LAP"
    times = [t for _n, t, _f in ra.race_lap_frames(df)]
    assert spec["headline"]["big_value"] == share_card.fmt_time(min(times), 2)
    assert spec["headline"]["big_value"].count(".") == 1 and len(spec["headline"]["big_value"].split(".")[1]) == 2
    assert dict(spec["headline"]["tiles"]) == {"LAPS": "5", "TOP SPEED": f"{df['speed_kmh'].max():.0f} km/h"}
    chart = spec["chart"]
    assert chart["x"] == [1, 2, 3, 4, 5] and chart["best"] == 0 and chart["y"] == times
    assert "dim" not in chart          # every lap drawn the same way, the best one highlighted
    assert "fuel" not in spec
    assert spec["map"] is not None and len(spec["map"]["x"]) > 100


def test_race_card_uses_the_names_as_typed(tmp_path):
    data, df = _race(tmp_path)
    data = {**data, "car_display": "Porsche 911 GT3 RS (992) '22", "track_display": "Nürburgring 24h"}
    spec = ra.race_card_spec(data, df)
    assert spec["car"] == "Porsche 911 GT3 RS (992) '22" and spec["track"] == "Nürburgring 24h"


def test_race_card_spec_without_gps(tmp_path):
    data, df = _race(tmp_path)
    df = df.assign(world_x=0.0, world_z=0.0)
    spec = ra.race_card_spec(data, df)
    assert spec["map"] is None


def test_race_card_bars_reach_the_slowest_lap(tmp_path):
    """A slow lap must be drawn at its real height, not cut off at the top."""
    data, df = _race(tmp_path, drops=(0, 6, 60, 3))
    spec = ra.race_card_spec(data, df)
    fig = Figure()
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    FigureCanvasAgg(fig)
    share_card._draw_chart(fig, spec)
    ax = fig.axes[0]
    assert ax.get_ylim()[1] > max(spec["chart"]["y"])
    assert all(b.get_height() == pytest.approx(y) for b, y in zip(ax.patches, spec["chart"]["y"]))


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


def test_lap_card_spec_shows_only_recorded_values():
    from test_udp_state import _card_lap
    data, df = _card_lap()
    spec = la.lap_card_spec(data, df)
    assert spec["kind"] == "LAP" and spec["headline"]["big_value"] == "1:23.456"
    assert spec["headline"]["big_label"] == "LAP TIME"
    assert dict(spec["headline"]["tiles"]) == {"TOP SPEED": f"{df['speed_kmh'].max():.0f} km/h",
                                               "FUEL USED": "6.0%"}
    chart = spec["chart"]
    assert chart["kind"] == "trace" and "sector_times" not in chart and "edges" not in chart
    assert len(chart["x"]) == len(chart["y"]) == len(df)
    assert spec["map"] is not None and "fuel" not in spec


def test_lap_card_spec_without_fuel_or_gps():
    from test_udp_state import _card_lap
    data, df = _card_lap(gps=False)
    df = df.assign(fuel_remaining=50.0)
    spec = la.lap_card_spec(data, df)
    assert spec["map"] is None and dict(spec["headline"]["tiles"])["FUEL USED"] == "--"


def test_lap_card_says_incomplete_instead_of_a_time():
    from test_udp_state import _card_lap
    data, df = _card_lap()
    spec = la.lap_card_spec({**data, "incomplete": True}, df)
    assert spec["headline"]["big_value"] == "INCOMPLETE"
    assert la.lap_card_spec({**data, "lap_time_s": 0}, df)["headline"]["big_value"] == "--:--.---"


@pytest.mark.parametrize("cap, used", [(100.0, "6.0%"), (0.0, "6.0% battery"), (5.0, "5.98 of 5")])
def test_lap_card_fuel_is_in_the_gauge_unit(cap, used):
    from test_udp_state import _card_lap
    data, df = _card_lap()
    spec = la.lap_card_spec(data, df.assign(fuel_capacity=cap))
    assert dict(spec["headline"]["tiles"])["FUEL USED"] == used


def test_lap_card_uses_the_names_as_typed():
    from test_udp_state import _card_lap
    data, df = _card_lap()
    spec = la.lap_card_spec({**data, "car_display": "Mercedes-AMG GT Black Series '20",
                             "track_display": "Autodrome Lago Maggiore - GP"}, df)
    assert spec["car"] == "Mercedes-AMG GT Black Series '20"
    assert spec["track"] == "Autodrome Lago Maggiore - GP"


def test_ghost_lap_card_has_no_map_and_no_made_up_numbers():
    samples = [dict(track_position=float(i * 5), speed_kmh=120.0 + i % 40, throttle=0.5, brake=0.0,
                    steering=0.0, gear=3) for i in range(200)]
    data, df = la.load_lap_data({"car": "Ghost Car", "track": "somewhere", "lap_time_s": 99.9, "samples": samples})
    spec = la.lap_card_spec(data, df.assign(world_x=range(200), world_z=range(200)))
    assert spec["map"] is None
    assert dict(spec["headline"]["tiles"]) == {"TOP SPEED": "159 km/h", "FUEL USED": "--"}


def test_car_name_is_never_cut():
    short = "Test Car"
    assert share_card.car_title(short) == (short, 14.0)
    mid = "Porsche 911 GT3 RS (992) '22"
    text, size = share_card.car_title(mid)
    assert text == mid and size < 14.0
    long = "Volkswagen Polo GTI Gr.B Rally Car Concept Special Edition '99"
    text, size = share_card.car_title(long)
    assert text.replace("\n", " ") == long and "\n" in text


def test_fmt_time_decimals():
    assert share_card.fmt_time(116.804) == "1:56.804"
    assert share_card.fmt_time(116.804, 2) == "1:56.80"
    assert share_card.fmt_time(59.996, 2) == "1:00.00"
    assert share_card.fmt_time(None, 2) == "--:--.--"


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


def test_race_groups_registered():
    names = [g[0] for g in ra.GROUPS]
    assert names[-2:] == ["Corners", "Consistency"]
