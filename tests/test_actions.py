"""core/actions.py and the terminal UI's use of it: exports, notes, overlays, replay, weight, compare."""
import asyncio
import json
import warnings

import pytest
from _fixtures import full_lap_data, full_race_data, slower_lap_data

warnings.filterwarnings("ignore")

from textual.widgets import DataTable, Select, Switch, TextArea  # noqa: E402

from gt7telem import labels, lap_charts, race_charts  # noqa: E402
from gt7telem.core import actions, registry  # noqa: E402
from gt7telem.tui import app as tui  # noqa: E402


@pytest.fixture()
def laps(tmp_path, monkeypatch):
    d = tmp_path / "Test Ring"
    (d / "races").mkdir(parents=True)
    for n, data in (("lap_1.json", full_lap_data()), ("lap_2.json", slower_lap_data())):
        (d / n).write_text(json.dumps(data), encoding="utf-8")
    (d / "races" / "race_1.json").write_text(json.dumps(full_race_data()), encoding="utf-8")
    monkeypatch.setattr(tui, "laps_folder", lambda: tmp_path)
    monkeypatch.setattr(tui.udp, "get_snapshot", lambda: {})
    monkeypatch.setattr(tui.udp, "is_connected", lambda: False)
    monkeypatch.setattr(tui.config, "save", lambda **kw: True)
    monkeypatch.setattr("gt7telem.trackdetect.fetch_shared", lambda *a, **k: None)
    monkeypatch.setattr(actions.dyno, "_weights_path", lambda: tmp_path / "w.json")
    return tmp_path


def test_notes_roundtrip_and_failure(tmp_path):
    p = tmp_path / "lap.json"
    p.write_text(json.dumps(full_lap_data()), encoding="utf-8")
    assert actions.save_notes(p, "  late on the brakes  ")
    assert json.loads(p.read_text(encoding="utf-8"))["notes"] == "late on the brakes"
    assert not actions.save_notes(tmp_path / "missing.json", "x")


def test_exports(tmp_path):
    data, df = lap_charts.load_lap_data(full_lap_data())
    assert actions.export_csv(df, tmp_path / "a.csv").read_text(encoding="utf-8").startswith(("distance", "t", "track"))
    assert actions.export_lap_card(data, df, tmp_path / "c.png").read_bytes()[:4] == b"\x89PNG"
    html = actions.export_chart_html(tmp_path / "c.html", "lap", "Inputs", df).read_text(encoding="utf-8")
    assert "data:image/png;base64," in html and "Inputs" in html
    rd, rdf = race_charts.load_race_data(full_race_data())
    assert actions.export_race_card(rd, rdf, tmp_path / "r.png").read_bytes()[:4] == b"\x89PNG"
    assert actions.export_chart_html(tmp_path / "r.html", "race", "Laps", rdf).exists()


def test_car_weight_is_remembered(laps):
    _d, df = lap_charts.load_lap_data(full_lap_data())
    df.attrs["car"] = "Test Car"
    assert actions.car_weight("Test Car") is None
    assert actions.set_car_weight(df, 1300) and df.attrs["mass_kg"] == 1300
    assert actions.car_weight("Test Car") == 1300
    assert not actions.set_car_weight(df, 10)          # out of range is refused


def test_duplicates_found_and_moved(tmp_path):
    d = tmp_path / "T"
    d.mkdir()
    for n in ("a.json", "b.json"):
        (d / n).write_text(json.dumps(full_lap_data()), encoding="utf-8")
    groups = actions.find_duplicates(tmp_path)
    assert groups
    moved, errors = actions.move_duplicates(groups, tmp_path)
    assert len(moved) == 1 and not errors


def test_theory_pool_orders_laps():
    pool = actions.theory_pool("A", None, [("o1", "O")], [("disk", "D")])
    assert [p[0] for p in pool] == ["Lap A", "o1", "disk"]


def test_submit_refuses_unusable_laps_before_any_request(monkeypatch):
    sent = []
    monkeypatch.setattr(actions.leaderboard, "submit_lap", lambda *a, **k: sent.append(a) or (True, "ok"))
    assert actions.submit_lap({"samples": []}, "name") == (False, "data")
    assert actions.submit_lap({"lap_time_s": 90, "samples": []}, "name") == (False, "data")
    assert actions.submit_lap({"lap_time_s": 0, "samples": [{"t": 0}], "physics_era": 99}, "name")[0] is False
    assert not sent                                     # nothing was handed to the network layer
    assert set(actions.SUBMIT_MESSAGES) >= {"session", "data", "server", "network"}


def test_submit_sends_one_request_for_a_good_lap(monkeypatch):
    sent = []
    monkeypatch.setattr(actions.leaderboard, "submit_lap", lambda *a, **k: sent.append(a) or (True, "ok"))
    monkeypatch.setattr(actions.runtime_config, "save", lambda **k: True)
    data = full_lap_data()
    data["physics_era"] = actions.leaderboard.PHYSICS_ERA
    assert actions.submit_lap(data, "  Ransh  ") == (True, "")
    assert len(sent) == 1 and sent[0][3] == "Ransh"


def test_the_suite_cannot_reach_the_internet():
    import urllib.request
    with pytest.raises(RuntimeError, match="must not use the network"):
        urllib.request.urlopen("https://example.com/")


def test_terminal_laps_screen_features(laps, monkeypatch):
    async def go():
        app = tui.TraceApp()
        async with app.run_test(size=(190, 60)) as pilot:
            await pilot.press("2")
            await pilot.pause()
            pane = app.query_one(tui.LapsPane)
            tbl = pane.query_one("#files", DataTable)
            tbl.move_cursor(row=0)
            await pane.key_a()
            tbl.move_cursor(row=1)
            await pane.key_b()
            await pane.key_o()
            assert len(pane.extra) == 1 and pane.df_b is not None
            # compare off: charts get no lap B; on: they do
            seen = []
            real = tui.charts.lap_chart
            monkeypatch.setattr(tui.charts, "lap_chart", lambda g, a, b=None, **kw: seen.append(b is not None) or real(g, a, b, **kw))
            pane.query_one("#group", Select).value = "Engine"
            await pilot.pause()
            assert seen and seen[-1] is False
            pane.query_one("#compare", Switch).value = True
            await pilot.pause()
            assert seen[-1] is True
            # exports through the same code path the buttons use
            out = laps / "_out"
            out.mkdir()
            pane.export_csv(out / "x.csv")
            pane.export_card(out / "x.png")
            pane.export_html(out / "x.html", "Inputs", pane.df_b)
            assert all((out / n).stat().st_size > 0 for n in ("x.csv", "x.png", "x.html"))
            # notes save into the file and reload with it
            pane.query_one("#notes", TextArea).text = "check T4"
            await pane.do_action("notes")
            assert json.loads(open(pane.path_a, encoding="utf-8").read())["notes"] == "check T4"
            # replay screen opens and steps
            await pane.do_action("replay")
            await pilot.pause()
            assert isinstance(app.screen, tui.ReplayScreen)
            await pilot.press("right", "space", "escape")
            await pilot.pause()
    asyncio.run(go())


def test_terminal_race_screen_actions(laps):
    async def go():
        app = tui.TraceApp()
        async with app.run_test(size=(190, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            pane = app.query_one(tui.RacePane)
            await pane.key_a()
            out = laps / "_out"
            out.mkdir()
            pane.export_csv(out / "r.csv")
            pane.export_card(out / "r.png")
            pane.export_html(out / "r.html", "Laps", None)
            assert all((out / n).stat().st_size > 0 for n in ("r.csv", "r.png", "r.html"))
    asyncio.run(go())


def test_action_labels_exist_in_both_front_ends():
    lap_src = open(lap_charts.__file__.replace("lap_charts", "lap_analyst"), encoding="utf-8").read()
    race_src = open(race_charts.__file__.replace("race_charts", "race_analyst"), encoding="utf-8").read()
    for attr in ("EXPORT_CSV", "EXPORT_CHART_HTML", "EXPORT_LAP_CARD", "BACKUP_ALL_LAPS", "FIND_DUPLICATE_LAPS",
                 "SUBMIT_TO_LEADERBOARD", "LOAD_COMMUNITY_LINE", "ADD_OVERLAY_LAP"):
        assert f"L.{attr}" in lap_src, f"the Tk Lap Analyst no longer has {attr}"
        assert getattr(labels, attr) in [lbl for _k, lbl in registry.LAP_ACTIONS] + [registry.ADD_OVERLAY_LABEL]
    for attr in ("EXPORT_CSV", "EXPORT_CHART_HTML", "EXPORT_RACE_CARD"):
        assert f"L.{attr}" in race_src
        assert getattr(labels, attr) in [lbl for _k, lbl in registry.RACE_ACTIONS]
