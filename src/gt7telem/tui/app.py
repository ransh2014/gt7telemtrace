"""TRACE in the terminal: Live, Laps, Race, Records and Settings.

Everything shown comes from the same modules the desktop windows use: the chart groups (lap_charts / race_charts via
core/charts.py), the tools (core/registry.py -> analysis_tools.py), records.py and config.py. This file only lays them
out; it contains no analysis of its own.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
    TextArea,
)
from textual_plotext import PlotextPlot

from .. import __version__, config, records, udp, webui_server
from .. import labels as L
from ..core import actions, charts, registry
from ..core.live import LiveEngine
from ..core.recorder import Hooks
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
    LivePane #conn, LivePane #rec { height: 3; }
    LivePane #conn Input { width: 22; }
    LivePane #rec Input { width: 24; }
    LivePane #body { height: 1fr; }
    LivePane #gauges { width: 1fr; }
    LivePane #side { width: 46; }
    LivePane #map { height: 1fr; }
    LivePane #log { height: 10; border: round $primary 30%; }
    LivePane #offer { height: 3; display: none; background: $primary 20%; }
    """

    def __init__(self):
        super().__init__()
        self._trail = deque(maxlen=1500)
        self.engine = None

    def _make_engine(self):
        def cfg(k, dflt=True):
            return bool(config.load().get(k, dflt))

        hooks = Hooks(
            laps_folder=laps_folder,
            track=lambda: self.query_one("#track", Input).value,
            car=lambda: self.query_one("#car", Input).value,
            rate=lambda: int(config.load().get("SAMPLE_RATE", 30) or 30),
            log=self._log,
            state=self._on_state,
            auto_lap_enabled=lambda: cfg("AUTO_LAP_ENABLED", False),
            notify=lambda title, msg: self._ui(self.app.notify, f"{title}: {msg}"),
            gear_info=lambda: (list(udp.get("gear_ratios") or []), float(udp.get("tyre_radius_m") or 0.0)),
        )
        return LiveEngine(hooks, voice_enabled=lambda: cfg("VOICE_ENABLED"), ghost_enabled=lambda: cfg("GHOST_ENABLED"),
                          analytics_enabled=lambda: cfg("ANALYTICS_ENABLED", False))

    def compose(self) -> ComposeResult:
        with Horizontal(id="conn"):
            yield Label(" Console IP ")
            yield Input(value=config.load().get("PS_IP", "") or "", placeholder="192.168.x.x", id="ip")
            yield Button("Connect", id="connect", variant="primary")
            yield Button("Auto-detect", id="detect")
            yield Static("", id="status")
        with Horizontal(id="rec"):
            yield Label(" Car ")
            yield Input(placeholder="car name", id="car")
            yield Label(" Track ")
            yield Input(placeholder="track name", id="track")
            yield Button("Record Lap", id="reclap")
            yield Button("Record Race", id="recrace")
        with Horizontal(id="offer"):
            yield Static("", id="offer-text")
            yield Button("Use it", id="offer-yes", variant="success")
            yield Button("Not this", id="offer-no")
        with Horizontal(id="body"):
            yield Static("Waiting for telemetry...", id="gauges")
            with Vertical(id="side"):
                yield PlotextPlot(id="map")
                yield RichLog(id="log", wrap=True, markup=False)

    def on_mount(self):
        self.engine = self._make_engine()
        self.engine.start()
        self.set_interval(0.1, self.refresh_live)

    # -- hooks (may be called from worker threads) -----------------------------------------------
    def _ui(self, fn, *args):
        try:
            self.app.call_from_thread(fn, *args)
        except Exception:
            fn(*args)

    def _log(self, msg):
        def write():
            self.query_one("#log", RichLog).write(f"{time.strftime('%H:%M:%S')}  {msg}")
        self._ui(write)

    def _on_state(self, kind):
        labels = {"armed": ("Waiting for start line", "warning"), "recording": ("Stop recording", "error"),
                  "idle": ("Record Lap", "default"), "race": ("Stop race", "error"),
                  "race_idle": ("Record Race", "default")}
        if kind not in labels:
            return
        text, variant = labels[kind]

        def apply():
            btn = self.query_one("#recrace" if kind.startswith("race") else "#reclap", Button)
            btn.label, btn.variant = text, variant
        self._ui(apply)

    # -- buttons ---------------------------------------------------------------------------------
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

    @on(Button.Pressed, "#reclap")
    def _reclap(self):
        self.engine.toggle_lap()

    @on(Button.Pressed, "#recrace")
    def _recrace(self):
        self.engine.toggle_race()

    @on(Button.Pressed, "#offer-yes")
    def _offer_yes(self):
        name = self.engine.accept_track()
        if name:
            self.query_one("#track", Input).value = name

    @on(Button.Pressed, "#offer-no")
    def _offer_no(self):
        self.engine.decline_track()

    # -- frame -----------------------------------------------------------------------------------
    def refresh_live(self):
        try:
            out = self.engine.poll()
            d = udp.get_snapshot()
        except Exception:
            return
        self.query_one("#status", Static).update(
            Text(" ● connected" if out["connected"] else " ○ waiting", style="green" if out["connected"] else "yellow"))
        self.query_one("#offer").display = bool(out["track_offer"])
        if out["track_offer"]:
            self.query_one("#offer-text", Static).update(f" Looks like {out['track_offer']} - use it as the track?")
        if not d or not out["connected"]:
            return
        f = webui_server.build_frame(d, {"connected": True, "strategy": out["strategy"], "delta": out["delta"],
                                         "vs_top": out["gap"], "cur_ms": int(out["cur_elapsed"] * 1000) or None,
                                         "car": self.query_one("#car", Input).value or d.get("car_name"),
                                         "track": self.query_one("#track", Input).value or d.get("track_name"),
                                         "coach": out["cue"]})
        self.query_one("#gauges", Static).update(self._gauges(f, out))
        if f["x"] or f["z"]:
            if f["spd"] > 1:
                self._trail.append((f["x"], f["z"]))
            self._draw_map(f)

    def _gauges(self, f, out):
        t = Text()
        t.append(f"{f['car'] or 'Car'}  -  {f['track'] or 'Track'}\n", style="bold")
        for show, label, colour in ((out["recording"], "REC LAP", "red"), (out["armed"], "ARMED", "dark_orange"),
                                    (out["race_recording"], "REC RACE", "red"), (out["paused"], "PAUSED", "dark_orange")):
            if show:
                t.append(f" {label} ", style=f"bold white on {colour}").append(" ")
        t.append("\n")
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
        if f["top"] is not None:
            t.append(f"  Gap to top lap     {f['top']:+.2f}s\n", style="green" if f["top"] <= 0 else "red")
        t.append(f"  Fuel {f['fuel']:.1f}/{f['cap']:.0f}")
        if f["fl"] is not None:
            t.append(f"   {f['fl']:.1f} laps left   {f['fpl'] or 0:.2f}/lap")
        if f["pit"]:
            t.append(f"   pit by lap {f['pit']}", style="yellow")
        t.append("\n")
        if f["tyre"]:
            t.append(f"  Tyres: {f['tyre']}   pace loss {f['loss'] or 0:.2f}s   stint {f['stint']} laps\n")
        if out["alerts"]:
            names = {"fuel": "FUEL LOW", "hot": "TYRES HOT", "cold": "TYRES COLD"}
            t.append("  " + "  ".join(names[a] for a in out["alerts"]) + "\n", style="bold yellow")
        if f["tcs"] or f["asm"]:
            t.append("  " + ("TCS " if f["tcs"] else "") + ("ASM" if f["asm"] else "") + "\n", style="yellow")
        if f["coach"]:
            t.append(f"\n  >> {f['coach']}\n", style="bold cyan")
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


class AskScreen(ModalScreen):
    """Ask for one line of text (a file path, a name, a number)."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    AskScreen { align: center middle; }
    AskScreen > Vertical { width: 80%; height: auto; border: round $accent; background: $surface; padding: 1 2; }
    """

    def __init__(self, prompt: str, default: str = ""):
        super().__init__()
        self._prompt, self._default = prompt, default

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(self._prompt))
            yield Input(value=self._default, id="answer")
            with Horizontal():
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self):
        self.query_one("#answer", Input).focus()

    @on(Input.Submitted)
    def _submit(self):
        self.dismiss(self.query_one("#answer", Input).value.strip() or None)

    @on(Button.Pressed, "#ok")
    def _ok(self):
        self.dismiss(self.query_one("#answer", Input).value.strip() or None)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self):
        self.dismiss(None)


class ReplayScreen(ModalScreen):
    """Scrub through a lap: left/right step, space plays, +/- changes speed, escape closes."""

    BINDINGS = [("escape", "dismiss", "Close"), ("space", "toggle", "Play/Pause"), ("left", "step(-1)", "Back"),
                ("right", "step(1)", "Forward"), ("shift+left", "step(-20)", "Back 20"),
                ("shift+right", "step(20)", "Forward 20"), ("plus", "speed(2)", "Faster"), ("minus", "speed(0.5)", "Slower"),
                ("home", "seek(0)", "Start")]
    DEFAULT_CSS = """
    ReplayScreen { align: center middle; }
    ReplayScreen > Vertical { width: 96%; height: 92%; border: round $accent; background: $surface; padding: 0 1; }
    ReplayScreen #rp-info { height: 9; }
    ReplayScreen #rp-map { height: 1fr; }
    """

    def __init__(self, df, title: str, dfb=None):
        super().__init__()
        self.df, self.dfb, self._title = df, dfb, title
        self.i, self.playing, self.rate = 0, False, 1.0
        self._t = df["t"].to_numpy(dtype=float)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(f"Replay - {self._title}   (space play, arrows step, +/- speed, esc close)", style="bold"))
            yield Static("", id="rp-info")
            yield PlotextPlot(id="rp-map")

    def on_mount(self):
        self.set_interval(1 / 20, self._tick)
        self._draw(full=True)

    def action_toggle(self):
        self.playing = not self.playing

    def action_step(self, n: int):
        self.playing = False
        self.i = max(0, min(len(self.df) - 1, self.i + int(n)))
        self._draw()

    def action_seek(self, n: int):
        self.i = int(n)
        self._draw()

    def action_speed(self, factor: float):
        self.rate = max(0.125, min(16.0, self.rate * float(factor)))

    def _tick(self):
        if not self.playing:
            return
        target = self._t[self.i] + self.rate / 20
        j = int(self._t.searchsorted(target))
        self.i = min(j, len(self.df) - 1)
        if self.i >= len(self.df) - 1:
            self.playing = False
        self._draw()

    def _draw(self, full=False):
        r = self.df.iloc[self.i]
        t = Text()
        t.append(f"  t {r['t']:7.2f}s   speed {r['speed_kmh']:5.0f} km/h   gear {int(r['gear'])}   rpm {r['rpm']:5.0f}"
                 f"   pos {r['track_position']:6.0f} m   x{self.rate:g}\n")
        t.append("  THR   ").append_text(bar(r["throttle"], 30, "green")).append("\n")
        t.append("  BRK   ").append_text(bar(r["brake"], 30, "red")).append("\n")
        t.append("  STEER ").append_text(bar((r["steering"] + 1) / 2, 30, "cyan")).append(f"  {r['steering']:+.2f}\n")
        t.append(f"  Tyres C  {r['tyre_temp_fl']:.0f} {r['tyre_temp_fr']:.0f} / {r['tyre_temp_rl']:.0f} {r['tyre_temp_rr']:.0f}"
                 f"    fuel {r['fuel_remaining']:.1f}\n")
        t.append(f"  G  lat {r['lat_g']:+.2f}  long {r['long_g']:+.2f}\n")
        if self.dfb is not None:
            pos = r["track_position"]
            j = int(abs(self.dfb["track_position"].to_numpy(dtype=float) - pos).argmin())
            rb = self.dfb.iloc[j]
            gap = r["t"] - rb["t"]
            t.append(f"  vs Lap B at this spot: {gap:+.2f}s   B speed {rb['speed_kmh']:.0f} km/h\n",
                     style="green" if gap <= 0 else "red")
        self.query_one("#rp-info", Static).update(t)
        plot = self.query_one("#rp-map", PlotextPlot)
        plt = plot.plt
        plt.clear_figure()
        plt.theme("dark")
        step = max(1, len(self.df) // 600)
        plt.scatter(self.df["world_x"].to_numpy()[::step].tolist(), self.df["world_z"].to_numpy()[::step].tolist(),
                    marker="braille", color=(95, 179, 168))
        plt.scatter([float(r["world_x"])], [float(r["world_z"])], marker="dot", color=(226, 69, 90))
        plt.title("Track")
        plt.xticks([])
        plt.yticks([])
        plot.refresh()


# ── Analyst panes (Laps and Race share one implementation; only the data source differs) ──────────────────────
class AnalystPane(Vertical):
    DEFAULT_CSS = """
    AnalystPane #side { width: 46; }
    AnalystPane #files { height: 12; }
    AnalystPane #main { width: 1fr; }
    AnalystPane #bar { height: 3; }
    AnalystPane #tools, AnalystPane #actions { height: auto; }
    AnalystPane Button { min-width: 10; }
    AnalystPane #notes { height: 6; }
    """
    KIND = "lap"

    def __init__(self):
        super().__init__()
        self.data_a = self.df_a = self.data_b = self.df_b = None
        self.path_a = self.path_b = None
        self.extra: list[dict] = []        # overlay laps: {"label", "df", "color"}
        self.consensus = None
        self.theory_disk = None
        self._rows: list[dict] = []

    # per-kind hooks -------------------------------------------------------------------------------------------
    def groups(self):
        return registry.lap_groups()

    def tools(self):
        return registry.LAP_TOOLS

    def action_list(self):
        return registry.LAP_ACTIONS

    def scan_files(self):
        from ..lap_charts import _scan_laps
        return _scan_laps(laps_folder())

    def load_file(self, path):
        from ..lap_charts import load_lap
        return load_lap(path)

    def compare_on(self) -> bool:
        return bool(self.query_one("#compare", Switch).value)

    def chart_spec(self, group):
        dfb = self.df_b if self.compare_on() else None
        extra = [(e["label"], e["df"], e["color"]) for e in self.extra]
        pool = actions.theory_pool(self.df_a, self.df_b, [(e["label"], e["df"]) for e in self.extra], self.theory_disk)
        if group == "Dyno & Tune":
            self._ensure_mass()
        return charts.lap_chart(group, self.df_a, dfb, extra=extra, consensus=self.consensus, pool=pool)

    def run_tool(self, kind):
        from .. import analysis_tools
        return analysis_tools.lap_tool(kind, self.df_a, self.df_b, self.data_a, self.data_b,
                                       [e["df"] for e in self.extra])

    def _ensure_mass(self):
        for df in (self.df_a, self.df_b if self.compare_on() else None):
            if df is not None:
                df.attrs["mass_kg"] = actions.car_weight(df.attrs.get("car") or "")

    # layout --------------------------------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal():
            with VerticalScroll(id="side"):
                yield Label("Files (a = load as A, b = load as B, o = add overlay)")
                yield DataTable(id="files", cursor_type="row")
                yield Static("A: -   B: -", id="loaded")
                with Horizontal():
                    yield Switch(value=False, id="compare")
                    yield Label("  Compare A with B")
                yield Label("Analysis tools")
                with Vertical(id="tools"):
                    for kind, label, _c in self.tools():
                        yield Button(label, id=f"tool-{kind}")
                yield Label("Actions")
                with Vertical(id="actions"):
                    for kind, label in self.action_list():
                        yield Button(label, id=f"act-{kind}")
                    if self.KIND == "lap":
                        yield Button(registry.ADD_OVERLAY_LABEL, id="act-overlay")
                        yield Button("Set car weight (kg)", id="act-weight")
                if self.KIND == "lap":
                    yield Label("Lap notes")
                    yield TextArea("", id="notes")
                    yield Button("Save notes", id="act-notes")
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

    async def key_o(self):
        await self._load_slot("o")

    def _status(self):
        self.query_one("#loaded", Static).update(
            f"A: {Path(self.path_a).name if self.path_a else '-'}   B: {Path(self.path_b).name if self.path_b else '-'}"
            f"   overlays: {len(self.extra)}")

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
            self.consensus, self.theory_disk = None, None
            if self.KIND == "lap":
                self.query_one("#notes", TextArea).text = str(data.get("notes") or "")
                self._start_theory()
        elif slot == "b":
            self.data_b, self.df_b, self.path_b = data, df, row["path"]
        else:
            colors = registry.overlay_colors()
            self.extra.append({"label": Path(row["path"]).stem[:24], "df": df,
                               "color": colors[len(self.extra) % len(colors)]})
        self._status()
        await self.draw()

    def _start_theory(self):
        data, path = dict(self.data_a), self.path_a

        def work():
            found = actions.saved_theory_laps(data, path)
            self.app.call_from_thread(self._theory_done, path, found)
        threading.Thread(target=work, daemon=True).start()

    async def _theory_done(self, path, found):
        if path == self.path_a:
            self.theory_disk = found
            if self.query_one("#group", Select).value == "Theoretical":
                await self.draw()

    @on(Select.Changed, "#group")
    async def _group(self):
        await self.draw()

    @on(Switch.Changed, "#compare")
    async def _compare(self):
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

    # buttons ------------------------------------------------------------------------------------------------
    @on(Button.Pressed)
    async def _button(self, event: Button.Pressed):
        bid = event.button.id or ""
        if bid.startswith("tool-"):
            try:
                title, body = self.run_tool(bid[5:])
            except Exception as e:
                title, body = "Analysis tools", f"Couldn't run that tool: {e}"
            self.app.push_screen(ReportScreen(title, body))
        elif bid.startswith("act-"):
            try:
                await self.do_action(bid[4:])
            except Exception as e:
                self.app.notify(f"{bid[4:]} failed: {e}", severity="error")

    def _default_path(self, suffix):
        d = self.data_a or {}
        name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in
                       str(d.get("track") or "lap") + "_" + str(d.get("recorded_at") or "x"))
        out = laps_folder() / "_exports"
        out.mkdir(parents=True, exist_ok=True)
        return str(out / f"{name}{suffix}")

    async def _ask(self, prompt, default=""):
        return await self.app.push_screen_wait(AskScreen(prompt, default))

    async def do_action(self, kind):
        if kind in ("backup",):
            self.app.notify("Backing up your laps...")
            threading.Thread(target=self._backup_worker, daemon=True).start()
            return
        if kind == "dups":
            await self._dups()
            return
        if self.df_a is None:
            self.app.notify("Load Lap A first.", severity="warning")
            return
        group = self.query_one("#group", Select).value
        dfb = self.df_b if self.compare_on() else None
        if kind == "csv":
            path = await self._ask("Save CSV as:", self._default_path(".csv"))
            if path:
                self.export_csv(path)
                self.app.notify(f"Saved {path}")
        elif kind == "html":
            path = await self._ask("Save chart page as:", self._default_path(f"_{group.replace(' ', '_')}.html"))
            if path:
                self.export_html(path, group, dfb)
                self.app.notify(f"Saved {path}")
        elif kind == "card":
            path = await self._ask("Save card (PNG) as:", self._default_path("_card.png"))
            if path:
                self.export_card(path)
                self.app.notify(f"Saved {path}")
        elif kind == "replay3d":
            from .. import analysis_tools
            if not analysis_tools.replay_ok(self.df_a):
                self.app.notify("This lap has no position data to replay.", severity="warning")
                return
            path = await self._ask("Save 3D replay (HTML) as:", self._default_path("_replay.html"))
            if path:
                analysis_tools.write_replay(path, self.df_a, self.df_b)
                self.app.notify(f"Saved {path}")
        elif kind == "replay":
            self.app.push_screen(ReplayScreen(self.df_a, Path(self.path_a or "lap").stem,
                                              self.df_b if self.compare_on() else None))
        elif kind == "community":
            d = self.data_a or {}
            car = d.get("car_display") or d.get("car", "?")
            track = d.get("track_display") or d.get("track", "?")
            self.app.notify("Loading the community line...")
            path = self.path_a

            def work():
                rows = actions.consensus_rows(car, track)
                self.app.call_from_thread(self._community_done, path, rows)
            threading.Thread(target=work, daemon=True).start()
        elif kind == "submit":
            await self._submit()
        elif kind == "overlay":
            await self.key_o()
        elif kind == "weight":
            await self._weight()
        elif kind == "notes":
            ok = actions.save_notes(self.path_a, self.query_one("#notes", TextArea).text) if self.path_a else False
            self.app.notify("Notes saved with this lap." if ok else "Couldn't save notes.",
                            severity="information" if ok else "error")

    # per-kind exports (lap) -----------------------------------------------------------------------------------
    def export_csv(self, path):
        actions.export_csv(self.df_a, path)

    def export_html(self, path, group, dfb):
        extra = [(e["label"], e["df"], e["color"]) for e in self.extra]
        actions.export_chart_html(path, "lap", group, self.df_a, dfb, extra=extra, consensus=self.consensus,
                                  pool=actions.theory_pool(self.df_a, self.df_b, [], self.theory_disk))

    def export_card(self, path):
        actions.export_lap_card(self.data_a, self.df_a, path)

    # long actions ---------------------------------------------------------------------------------------------
    def _backup_worker(self):
        path, err = actions.backup_laps()
        msg = f"Backup saved: {path}" if path else f"Backup failed: {err or 'unknown error'}"
        self.app.call_from_thread(self.app.notify, msg)

    async def _dups(self):
        folder = laps_folder()
        groups = await asyncio.to_thread(actions.find_duplicates, folder)
        extras = [p for _keep, ps in groups for p in ps]
        if not extras:
            self.app.notify("No duplicate laps found.")
            return
        ans = await self._ask(f"Found {len(extras)} extra cop{'y' if len(extras) == 1 else 'ies'} of laps you already "
                              "have. Type YES to move them to _duplicates (nothing is deleted):")
        if (ans or "").strip().upper() == "YES":
            moved, errors = actions.move_duplicates(groups, folder)
            self.app.notify(f"Moved {len(moved)} file(s)." + (f" {len(errors)} could not be moved." if errors else ""))

    def _community_done(self, path, rows):
        if path != self.path_a:
            return
        if not rows:
            self.app.notify("No community data yet for this car and track (or the server couldn't be reached).",
                            severity="warning")
            return
        self.consensus = rows
        self.run_worker(self.draw())

    async def _weight(self):
        car = self.df_a.attrs.get("car") or ""
        if not car:
            self.app.notify("This lap has no car name to attach a weight to.", severity="warning")
            return
        cur = actions.car_weight(car)
        ans = await self._ask(f"Weight of the {car} in kg (300-5000):", "" if cur is None else f"{cur:g}")
        try:
            kg = float(ans) if ans else None
        except ValueError:
            kg = None
        if kg and 300 <= kg <= 5000 and actions.set_car_weight(self.df_a, kg):
            await self.draw()
        elif ans:
            self.app.notify("Enter a number between 300 and 5000.", severity="warning")

    async def _submit(self):
        problem = actions.submission_problem(self.data_a)
        if problem:
            self.app.notify(problem, severity="warning")
            return
        d = self.data_a
        car = d.get("car_display") or d.get("car", "?")
        track = d.get("track_display") or d.get("track", "?")
        if not actions.signed_in():
            name = await self._ask("Create a free leaderboard account. Display name (this is public):",
                                   config.load().get("PSN_NAME", "") or "")
            if not name:
                return
            ok, msg = await asyncio.to_thread(actions.create_account, name)
            self.app.notify(msg, severity="information" if ok else "error")
            if not ok:
                return
        psn = await self._ask(f"Submit {car} @ {track} - {float(d.get('lap_time_s') or 0):.3f}s to the PUBLIC "
                              "leaderboard. PSN name:", config.load().get("PSN_NAME", "") or "")
        if not psn:
            return
        ok, reason = await asyncio.to_thread(actions.submit_lap, d, psn)
        self.app.notify("Submitted! If it's a new top time it'll appear on the leaderboard shortly." if ok
                        else actions.SUBMIT_MESSAGES.get(reason, "Submission failed."),
                        severity="information" if ok else "error")


class LapsPane(AnalystPane):
    KIND = "lap"


class RacePane(AnalystPane):
    KIND = "race"

    def groups(self):
        return registry.race_groups()

    def tools(self):
        return registry.RACE_TOOLS

    def action_list(self):
        return registry.RACE_ACTIONS

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
        return charts.race_chart(group, self.df_a, self.df_b if self.compare_on() else None)

    def run_tool(self, kind):
        from .. import analysis_tools
        from ..race_charts import race_lap_frames
        return "Race analysis", analysis_tools.race_report(kind, race_lap_frames(self.df_a))

    def export_csv(self, path):
        from ..race_charts import export_csv
        export_csv(self.df_a, path)

    def export_html(self, path, group, dfb):
        actions.export_chart_html(path, "race", group, self.df_a, dfb)

    def export_card(self, path):
        actions.export_race_card(self.data_a, self.df_a, path)


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
HELP = """1-5      switch screen (Live, Laps, Race, Records, Settings)
?        this help          q   quit          ctrl+p   command palette

Live     type the console IP and Connect (or Auto-detect). Record Lap arms at the next start line;
         Record Race records until you stop it. Fill Car and Track so laps save in the right folder.
         When the track is unknown TRACE offers a guess from where the lap starts: Use it / Not this.

Laps and Race
         pick a file in the list, then:  a = load as A,  b = load as B,  o = add an overlay lap (Laps)
         Compare turns lap B into the reference for every chart. The group box picks the chart.
         Analysis tools and Actions are on the left: exports, replay (space plays, arrows step),
         notes, car weight, community line, submit to the leaderboard (public, asks you first).
Records  best lap per car and track.        Settings   the same switches as the desktop app."""


_TRACE_THEME = Theme(
    name="trace", primary="#c8102e", secondary="#3b82f6", accent="#f5b301", success="#22c55e", warning="#f5b301",
    error="#ef4444", background="#08080a", surface="#111115", panel="#1c1c22", foreground="#e8e8ec", dark=True,
)


class TraceApp(App):
    TITLE = "TRACE - GT7 Telemetry"
    CSS = """
    Screen { background: $background; }
    Header { background: $primary; color: white; text-style: bold; }
    Footer { background: $panel; }
    Footer .footer-key--key { background: $primary; color: white; }
    TabbedContent { height: 1fr; padding: 0 1; }
    Tabs { background: $background; }
    Tab { padding: 0 3; color: $text-muted; }
    Tab.-active { color: white; text-style: bold; background: $primary 25%; }
    Underline > .underline--bar { color: $primary; background: $panel; }
    TabPane { padding: 1 0 0 0; }
    Button { border: none; height: 3; min-width: 12; margin: 0 1 0 0; background: $panel; color: $text; text-style: bold; }
    Button:hover { background: $primary 40%; }
    Button.-primary { background: $primary; color: white; }
    Button.-primary:hover { background: $primary 80%; }
    Input { border: none; height: 3; padding: 1 2; margin: 0 1 0 0; background: $surface; }
    Input:focus { background: $panel; border-left: thick $primary; }
    Select > SelectCurrent { border: none; height: 3; padding: 1 2; background: $surface; }
    Select:focus > SelectCurrent { background: $panel; }
    DataTable { background: $surface; border: none; padding: 0 1; }
    DataTable > .datatable--header { background: $panel; color: $text; text-style: bold; }
    DataTable > .datatable--cursor { background: $primary 50%; color: white; }
    TextArea { border: none; background: $surface; padding: 1 2; }
    #conn, #rec, #bar { height: 3; margin-bottom: 1; }
    #conn Label, #rec Label, #bar Label { height: 3; content-align: left middle; padding: 0 2 0 0; color: $text-muted; }
    #body { margin-top: 1; }
    #gauges, #map, #log, #chart, #files, #notes { height: 1fr; border: round $panel; padding: 0 2; margin: 0 1 1 0; border-title-color: $primary; border-title-style: bold; background: $surface 60%; }
    #gauges { width: 1fr; }
    #log { height: 10; }
    #notes { height: 8; }
    #files { height: 14; padding: 0; }
    #side { padding: 0 1 0 0; }
    #status { height: 3; content-align: left middle; padding: 0 2; color: $text-muted; }
    .setting { height: 3; padding: 0 1; }
    .compact AnalystPane #side { width: 34; }
    .compact LivePane #side { width: 30; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("1", "tab('live')", "Live"), ("2", "tab('laps')", "Laps"),
                ("3", "tab('race')", "Race"), ("4", "tab('records')", "Records"), ("5", "tab('settings')", "Settings"), ("question_mark", "help", "Help")]

    def on_mount(self) -> None:
        try:
            self.register_theme(_TRACE_THEME)
            self.theme = "trace"
        except Exception:
            pass
        for sel, title in (("#gauges", "Gauges"), ("#map", "Track"), ("#log", "Log"),
                           ("#files", "Saved files"), ("#notes", "Notes")):
            for w in self.query(sel):
                try:
                    w.border_title = title
                except Exception:
                    pass

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

    def on_resize(self, event):
        self.set_class(event.size.width < 120, "compact")     # narrow terminals: slimmer side panels

    def action_help(self):
        self.push_screen(ReportScreen("TRACE terminal - keys", HELP))

    def action_tab(self, name: str):
        self.query_one(TabbedContent).active = name


def main() -> int:
    TraceApp().run()
    return 0


_ = L  # labels.py is the one source of wording; registry.py reads it for the settings list
