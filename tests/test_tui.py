"""The terminal UI, driven headlessly with Textual's test pilot (no terminal needed)."""
import asyncio
import json
import warnings

import pytest
from _fixtures import full_lap_data, full_race_data, slower_lap_data

pytest.importorskip("textual")
warnings.filterwarnings("ignore")

from textual.widgets import DataTable, Select, TabbedContent  # noqa: E402

from gt7telem import records  # noqa: E402
from gt7telem.core import registry  # noqa: E402
from gt7telem.tui import app as tui  # noqa: E402


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    laps = tmp_path / "Test Ring"
    (laps / "races").mkdir(parents=True)
    for name, data in (("lap_1.json", full_lap_data()), ("lap_2.json", slower_lap_data())):
        (laps / name).write_text(json.dumps(data), encoding="utf-8")
    (laps / "races" / "race_1.json").write_text(json.dumps(full_race_data()), encoding="utf-8")
    monkeypatch.setattr(tui, "laps_folder", lambda: tmp_path)
    monkeypatch.setattr(tui.udp, "get_snapshot", lambda: {})          # never open the real UDP sockets in tests
    monkeypatch.setattr(tui.udp, "is_connected", lambda: False)
    monkeypatch.setattr(tui.config, "save", lambda **kw: True)
    monkeypatch.setattr("gt7telem.trackdetect.fetch_shared", lambda *a, **k: None)   # no network in tests
    return tmp_path


def run(coro):
    return asyncio.run(coro)


def test_every_screen_opens_and_tabs_switch(folder):
    async def go():
        app = tui.TraceApp()
        async with app.run_test(size=(160, 50)) as pilot:
            tabs = app.query_one(TabbedContent)
            for key, name in (("2", "laps"), ("3", "race"), ("4", "records"), ("5", "settings"), ("1", "live")):
                await pilot.press(key)
                await pilot.pause()
                assert tabs.active == name
    run(go())


def test_laps_screen_lists_files_and_draws_every_chart_group(folder):
    async def go():
        app = tui.TraceApp()
        async with app.run_test(size=(180, 60)) as pilot:
            await pilot.press("2")
            await pilot.pause()
            pane = app.query_one(tui.LapsPane)
            assert len(pane._rows) == 2
            pane.query_one("#files", DataTable).move_cursor(row=0)
            await pilot.pause()
            await pane.key_a()
            pane.query_one("#files", DataTable).move_cursor(row=1)
            await pane.key_b()
            await pilot.pause()
            assert pane.df_a is not None and pane.df_b is not None
            for g in registry.lap_groups():          # the same list the Tk window builds its tabs from
                pane.query_one("#group", Select).value = g
                await pilot.pause()
                assert pane.query_one("#chart").children, g
    run(go())


def test_race_screen_and_tools(folder):
    async def go():
        app = tui.TraceApp()
        async with app.run_test(size=(180, 60)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            pane = app.query_one(tui.RacePane)
            assert len(pane._rows) == 1
            await pane.key_a()
            await pilot.pause()
            assert pane.df_a is not None
            for g in registry.race_groups():
                pane.query_one("#group", Select).value = g
                await pilot.pause()
                assert pane.query_one("#chart").children, g
            for kind, _label, _c in registry.RACE_TOOLS:
                title, body = pane.run_tool(kind)
                assert body.strip()
    run(go())


def test_tool_buttons_match_the_registry_and_show_a_report(folder):
    async def go():
        app = tui.TraceApp()
        async with app.run_test(size=(180, 60)) as pilot:
            await pilot.press("2")
            await pilot.pause()
            pane = app.query_one(tui.LapsPane)
            ids = {b.id for b in pane.query("Button") if (b.id or "").startswith("tool-")}
            assert ids == {f"tool-{k}" for k, _l, _c in registry.LAP_TOOLS}
            await pane.key_a()
            await pilot.pause()
            await pilot.click("#tool-setup")
            await pilot.pause()
            assert isinstance(app.screen, tui.ReportScreen)
    run(go())


def test_records_and_settings_screens(folder):
    async def go():
        app = tui.TraceApp()
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.press("4")
            await pilot.pause()
            assert app.query_one("#rec-table", DataTable).row_count == len(records.build_records(folder))
            await pilot.press("5")
            await pilot.pause()
            for key, _label in registry.SETTINGS:
                assert app.query_one(f"#sw-{key}")
    run(go())


def test_live_screen_without_a_console_does_not_crash(folder):
    async def go():
        app = tui.TraceApp()
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(0.35)
            assert "waiting" in str(app.query_one("#status").render())
    run(go())
