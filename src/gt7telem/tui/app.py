"""TRACE in the terminal: Live, Laps, Race, Records and Settings.

Everything shown comes from the same modules the desktop windows use: the chart groups (lap_charts / race_charts via
core/charts.py), the tools (core/registry.py -> analysis_tools.py), records.py and config.py. This file only lays them
out; it contains no analysis of its own.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)
from textual_plotext import PlotextPlot

from .. import __version__, config, records, udp, voice, webui_server
from .. import labels as L
from ..core import charts, registry
from .chartview import ChartGrid

BAR = "█"
ORDER = ["live", "laps", "race", "records", "settings"]


def bar(frac, width=30, colour="green"):
    frac = max(0.0, min(1.0, float(frac or 0)))
    n = int(round(frac * width))
    return Text(BAR * n, style=colour) + Text("░" * (width - n), style="grey35")


def fmt_ms(ms):
    if not ms or ms <= 0:
        return "--:--.---"
    m, rest = divmod(int(ms), 60000)
    return f"{m}:{rest / 1000:06.3f}"


def laps_folder() -> Path:
    return Path(config.load().get("LAPS_FOLDER") or config.LAPS_FOLDER)


class ReportScreen(ModalScreen):
    """A text report from one of the analysis tools."""

    BINDINGS = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]
    DEFAULT_CSS = """
    ReportScreen { align: center middle; }
    ReportScreen > Vertical { width: 90%; height: 80%; border: round $accent; background: $surface; padding: 1 2; }
    ReportScreen VerticalScroll { height: 1fr; }
    """

    def __init__(self, title: str, body: str):
        super().__init__()
        self._title, self._body = title, body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(self._title, style="bold"))
            with VerticalScroll():
                yield Static(Text(self._body))
            yield Button("Close", id="close")

    @on(Button.Pressed, "#close")
    def _close(self):
        self.dismiss()


# ── Live ─────────────────────────────────────────────────────────────────────────────────────────────────────────
class LivePane(Vertical):
    DEFAULT_CSS = """
    LivePane #conn { height: 3; }
    LivePane #conn Input { width: 24; }
    LivePane #body { height: 1fr; }
    LivePane #gauges { width: 1fr; padding: 0 1; }
    LivePane #map { width: 44; }
    """

    def __init__(self):
        super().__init__()
        self._strategy = udp.StintTracker()
        self._announcer = voice.AlertAnnouncer()
        self._trail = deque(maxlen=1500)
        self._last_lap = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="conn"):
            yield Label(" Console IP ")
            yield Input(value=config.load().get("PS_IP", "") or "", placeholder="192.168.x.x", id="ip")
            yield Button("Connect", id="connect", variant="primary")
            yield Button("Auto-detect", id="detect")
            yield Static("", id="status")
        with Horizontal(id="body"):
            yield Static("Waiting for telemetry...", id="gauges")
            yield PlotextPlot(id="map")

    def on_mount(self):
        self.set_interval(0.1, self.refresh_live)

    @on(Button.Pressed, "#connect")
    def _connect(self):
        ip = self.query_one("#ip", Input).value.strip()
        if ip:
            udp.set_ip(ip)
            config.save(PS_IP=ip)
            self.app.notify(f"Connecting to {ip}")

    @on(Button.Pressed, "#detect")
    def _detect(self):
        self.app.notify("Looking for your console...")

        def work():
            ip = udp.discover_ps_ip()
            self.app.call_from_thread(self._found, ip)
        threading.Thread(target=work, daemon=True).start()

    def _found(self, ip):
        if ip:
            self.query_one("#ip", Input).value = ip
            self.app.notify(f"Found {ip}")
        else:
            self.app.notify("No console found. Is it on and on this network?", severity="warning")

    def refresh_live(self):
        try:
            d = udp.get_snapshot()
        except Exception:
            return
        connected = bool(udp.is_connected())
        self.query_one("#status", Static).update(
            Text(" ● connected" if connected else " ○ waiting", style="green" if connected else "yellow"))
        if not d or not connected:
            return
        temps = tuple(d.get(k) or 0 for k in ("tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr"))
        try:
            self._strategy.update(int(d.get("lap_number") or 0), int(d.get("total_laps") or 0),
                                  float(d.get("fuel_remaining") or 0), d.get("last_lap_ms"), temps,
                                  float(d.get("speed_kmh") or 0))
            snap = self._strategy.snapshot()
        except Exception:
            snap = {}
        f = webui_server.build_frame(d, {"connected": True, "strategy": snap,
                                         "car": d.get("car_name"), "track": d.get("track_name")})
        self.query_one("#gauges", Static).update(self._gauges(f, snap))
        if f["x"] or f["z"]:
            if f["spd"] > 1:
                self._trail.append((f["x"], f["z"]))
            self._draw_map(f)
        self._speak(f, snap)

    def _gauges(self, f, snap):
        t = Text()
        t.append(f"{f['car'] or 'Car'}  -  {f['track'] or 'Track'}\n", style="bold")
        t.append(f"  {f['spd']:6.0f} km/h   gear {f['gear'] if f['gear'] > 0 else 'N'}"
                 f"{'  (shift to ' + str(f['sug']) + ')' if f['sug'] else ''}\n\n")
        mx = f["maxrpm"] or 8000
        t.append("  RPM   ").append_text(bar(f["rpm"] / mx, 32, "red" if f["lim"] else "cyan"))
        t.append(f"  {f['rpm']}\n")
        t.append("  THR   ").append_text(bar(f["thr"], 32, "green")).append("\n")
        t.append("  BRK   ").append_text(bar(f["brk"], 32, "red")).append("\n")
        t.append(f"  G-force  lat {f['latg']:+.2f}  long {f['long']:+.2f}\n\n")
        t.append("  Tyre temp (C)   FL {0:5.1f}  FR {1:5.1f}\n                  RL {2:5.1f}  RR {3:5.1f}\n".format(*f["tt"]))
        t.append("  Tyre slip       FL {0:5.2f}  FR {1:5.2f}\n                  RL {2:5.2f}  RR {3:5.2f}\n\n".format(*f["ts"]))
        t.append(f"  Lap {f['lap']}/{f['laps'] or '-'}   cur {fmt_ms(f['cur'])}   last {fmt_ms(f['last'])}"
                 f"   best {fmt_ms(f['best'])}\n")
        if f["delta"] is not None:
            t.append(f"  Delta vs reference {f['delta']:+.2f}s\n", style="green" if f["delta"] <= 0 else "red")
        t.append(f"  Fuel {f['fuel']:.1f}/{f['cap']:.0f}")
        if f["fl"] is not None:
            t.append(f"   {f['fl']:.1f} laps left   {f['fpl'] or 0:.2f}/lap")
        if f["pit"]:
            t.append(f"   pit by lap {f['pit']}", style="yellow")
        t.append("\n")
        if f["tyre"]:
            t.append(f"  Tyres: {f['tyre']}   pace loss {f['loss'] or 0:.2f}s   stint {f['stint']} laps\n")
        if f["tcs"] or f["asm"]:
            t.append("  " + ("TCS " if f["tcs"] else "") + ("ASM" if f["asm"] else "") + "\n", style="yellow")
        return t

    def _draw_map(self, f):
        plot = self.query_one("#map", PlotextPlot)
        plt = plot.plt
        plt.clear_figure()
        plt.theme("dark")
        pts = list(self._trail)
        plt.scatter([p[0] for p in pts], [p[1] for p in pts], marker="braille", color=(95, 179, 168))
        plt.scatter([f["x"]], [f["z"]], marker="dot", color=(226, 69, 90))
        plt.title("Track")
        plt.xticks([])
        plt.yticks([])
        plot.refresh()

    def _speak(self, f, snap):
        if not config.load().get("VOICE_ENABLED", True):
            return
        fuel_laps = f["fl"] if f["fl"] is not None else 99
        active = set()
        if fuel_laps < 2:
            active.add("fuel")
        for line in self._announcer.update(active, time.monotonic(), fuel_laps=fuel_laps):
            voice.speak(line)


# ── Analyst panes (Laps and Race share one implementation; only the data source differs) ──────────────────────
class AnalystPane(Vertical):
    DEFAULT_CSS = """
    AnalystPane #side { width: 44; }
    AnalystPane #files { height: 1fr; }
    AnalystPane #main { width: 1fr; }
    AnalystPane #bar { height: 3; }
    AnalystPane #tools { height: auto; }
    AnalystPane Button { min-width: 10; }
    """
    KIND = "lap"

    def __init__(self):
        super().__init__()
        self.data_a = self.df_a = self.data_b = self.df_b = None
        self.path_a = self.path_b = None
        self._rows: list[dict] = []

    # per-kind hooks -------------------------------------------------------------------------------------------
    def groups(self):
        return registry.lap_groups()

    def tools(self):
        return registry.LAP_TOOLS

    def scan_files(self):
        from ..lap_charts import _scan_laps
        return _scan_laps(laps_folder())

    def load_file(self, path):
        from ..lap_charts import load_lap
        return load_lap(path)

    def chart_spec(self, group):
        return charts.lap_chart(group, self.df_a, self.df_b)

    def run_tool(self, kind):
        from .. import analysis_tools
        return analysis_tools.lap_tool(kind, self.df_a, self.df_b, self.data_a, self.data_b)

    # layout --------------------------------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="side"):
                yield Label("Files (a = load as A, b = load as B)")
                yield DataTable(id="files", cursor_type="row")
                yield Static("A: -   B: -", id="loaded")
                yield Label("Analysis tools")
                with Vertical(id="tools"):
                    for kind, label, _c in self.tools():
                        yield Button(label, id=f"tool-{kind}")
            with Vertical(id="main"):
                with Horizontal(id="bar"):
                    yield Select([(g, g) for g in self.groups()], value=self.groups()[0], allow_blank=False,
                                 id="group")
                    yield Button("Reload list", id="reload")
                yield ChartGrid(id="chart")

    def on_mount(self):
        t = self.query_one("#files", DataTable)
        t.add_columns("Date", "Car", "Track", "Time")
        self.reload_files()

    def reload_files(self):
        t = self.query_one("#files", DataTable)
        t.clear()
        self._rows = self.scan_files()
        for r in self._rows:
            t.add_row(r.get("date", ""), r.get("car", "")[:22], r.get("track", "")[:20], r.get("lap_time", ""))

    def _selected(self):
        t = self.query_one("#files", DataTable)
        i = t.cursor_row
        return self._rows[i] if 0 <= i < len(self._rows) else None

    async def key_a(self):
        await self._load_slot("a")

    async def key_b(self):
        await self._load_slot("b")

    async def _load_slot(self, slot):
        row = self._selected()
        if not row:
            return
        try:
            data, df = self.load_file(row["path"])
        except Exception as e:
            self.app.notify(f"Couldn't load that file: {e}", severity="error")
            return
        if slot == "a":
            self.data_a, self.df_a, self.path_a = data, df, row["path"]
        else:
            self.data_b, self.df_b, self.path_b = data, df, row["path"]
        self.query_one("#loaded", Static).update(
            f"A: {Path(self.path_a).name if self.path_a else '-'}   B: {Path(self.path_b).name if self.path_b else '-'}")
        await self.draw()

    @on(Select.Changed, "#group")
    async def _group(self):
        await self.draw()

    @on(Button.Pressed, "#reload")
    def _reload(self):
        self.reload_files()

    async def draw(self):
        grid = self.query_one("#chart", ChartGrid)
        if self.df_a is None:
            await grid.show(None, "Pick a file and press 'a' to load it as Lap A.")
            return
        group = self.query_one("#group", Select).value
        try:
            await grid.show(self.chart_spec(group))
        except Exception as e:
            await grid.show(None, f"Couldn't draw this chart: {e}")

    @on(Button.Pressed)
    def _tool(self, event: Button.Pressed):
        bid = event.button.id or ""
        if not bid.startswith("tool-"):
            return
        try:
            title, body = self.run_tool(bid[5:])
        except Exception as e:
            title, body = "Analysis tools", f"Couldn't run that tool: {e}"
        self.app.push_screen(ReportScreen(title, body))


class LapsPane(AnalystPane):
    KIND = "lap"

    def compose(self) -> ComposeResult:
        yield from super().compose()


class RacePane(AnalystPane):
    KIND = "race"

    def groups(self):
        return registry.race_groups()

    def tools(self):
        return registry.RACE_TOOLS

    def scan_files(self):
        rows = []
        folder = laps_folder()
        for f in sorted(folder.rglob("race_*.json"), key=lambda q: q.stat().st_mtime, reverse=True)[:200]:
            rows.append({"path": str(f), "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                         "car": f.parent.parent.name if f.parent.name.lower() == "races" else f.parent.name,
                         "track": f.stem, "lap_time": ""})
        return rows

    def load_file(self, path):
        from ..race_charts import load_race
        return load_race(path)

    def chart_spec(self, group):
        return charts.race_chart(group, self.df_a, self.df_b)

    def run_tool(self, kind):
        from .. import analysis_tools
        from ..race_charts import race_lap_frames
        return "Race analysis", analysis_tools.race_report(kind, race_lap_frames(self.df_a))


# ── Records and settings ─────────────────────────────────────────────────────────────────────────────────────────
class RecordsPane(Vertical):
    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter by car or track", id="filter")
        yield DataTable(id="rec-table", cursor_type="row")

    def on_mount(self):
        t = self.query_one("#rec-table", DataTable)
        t.add_columns("Track", "Car", "Best lap", "Laps", "Set")
        self._all = records.build_records(laps_folder())
        self._fill("")

    def _fill(self, text):
        t = self.query_one("#rec-table", DataTable)
        t.clear()
        for r in records.filter_records(self._all, text):
            t.add_row(r["track"], r["car"], records._fmt(r["time_s"]), str(r.get("laps", "")),
                      str(r.get("recorded_at", ""))[:8])

    @on(Input.Changed, "#filter")
    def _changed(self, e: Input.Changed):
        self._fill(e.value)


class SettingsPane(VerticalScroll):
    def compose(self) -> ComposeResult:
        cfg = config.load()
        yield Label("Preferences (saved to settings.json, same as the desktop app)")
        for key, label in registry.SETTINGS:
            with Horizontal(classes="setting"):
                yield Switch(value=bool(cfg.get(key, False)), id=f"sw-{key}")
                yield Label(f"  {label}")
        yield Label(f"\nTRACE {__version__}")

    @on(Switch.Changed)
    def _changed(self, e: Switch.Changed):
        key = (e.switch.id or "")[3:]
        if key:
            config.save(**{key: bool(e.value)})


# ── App ──────────────────────────────────────────────────────────────────────────────────────────────────────────
class TraceApp(App):
    TITLE = "TRACE - GT7 Telemetry"
    CSS = """
    Screen { background: #08080a; }
    Header { background: #c8102e; }
    TabbedContent { height: 1fr; }
    .setting { height: 3; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("1", "tab('live')", "Live"), ("2", "tab('laps')", "Laps"),
                ("3", "tab('race')", "Race"), ("4", "tab('records')", "Records"), ("5", "tab('settings')", "Settings")]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="live"):
            with TabPane("Live", id="live"):
                yield LivePane()
            with TabPane("Laps", id="laps"):
                yield LapsPane()
            with TabPane("Race", id="race"):
                yield RacePane()
            with TabPane("Records", id="records"):
                yield RecordsPane()
            with TabPane("Settings", id="settings"):
                yield SettingsPane()
        yield Footer()

    def action_tab(self, name: str):
        self.query_one(TabbedContent).active = name


def main() -> int:
    TraceApp().run()
    return 0


_ = L  # labels.py is the one source of wording; registry.py reads it for the settings list
