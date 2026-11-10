"""Draw a chart spec (core/figspec.py) in the terminal.

Line, bar, scatter and heat-map axes become plotext plots; axes that only carry text (the Corners table, tuning hints,
"load a lap" messages) become monospace text panels. Axes are laid out in rows and columns from their position in the
original figure, so a chart group looks like the desktop one, just in character cells.
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static
from textual_plotext import PlotextPlot

PLOT_ROWS = 14


def _rgb(hexcol, default=(95, 179, 168)):
    if not hexcol or not str(hexcol).startswith("#") or len(hexcol) < 7:
        return default
    return tuple(int(hexcol[i:i + 2], 16) for i in (1, 3, 5))


def _pairs(x, y):
    return [(a, b) for a, b in zip(x, y) if a is not None and b is not None]


def has_plot(ax: dict) -> bool:
    return bool(ax["lines"] or ax["bars"] or ax["scatter"] or ax["segments"] or ax["images"])


def text_of(ax: dict) -> str:
    """The words on an axes, top to bottom, for text-only panels."""
    parts = [t for t in sorted(ax["texts"], key=lambda t: -t["y"])]
    return "\n".join(t["s"] for t in parts)


def draw_axes(plot: PlotextPlot, ax: dict) -> None:
    """Fill a PlotextPlot from one axes spec."""
    plt = plot.plt
    plt.clear_figure()
    plt.theme("dark")
    plt.title(ax["title"] or "")
    if ax["xlabel"]:
        plt.xlabel(ax["xlabel"])
    if ax["ylabel"]:
        plt.ylabel(ax["ylabel"])
    for ln in ax["lines"]:
        pts = _pairs(ln["x"], ln["y"])
        if len(pts) >= 2:
            plt.plot([p[0] for p in pts], [p[1] for p in pts], color=_rgb(ln["color"]), label=ln["label"] or None,
                     marker="braille")
    for b in ax["bars"]:
        xs = [x + w / 2 for x, w in zip(b["x"], b["w"]) if x is not None and w is not None]
        hs = [h for h in b["h"] if h is not None]
        n = min(len(xs), len(hs))
        if n:
            cols = [_rgb(c) for c in b["color"]][:n]
            plt.bar(xs[:n], hs[:n], color=cols[0] if len(set(cols)) == 1 else cols)
    for sc in ax["scatter"]:
        pts = _pairs(sc["x"], sc["y"])
        if pts:
            plt.scatter([p[0] for p in pts], [p[1] for p in pts], marker="braille",
                        color=_rgb(sc["color"]) if sc.get("color") else (200, 200, 200))
    for sg in ax["segments"]:
        # a coloured line (a track map shaded by speed): draw as one braille line per value band
        pts = [p for p in sg["pts"] if None not in p]
        if pts:
            xs = [p[0] for p in pts] + [pts[-1][2]]
            ys = [p[1] for p in pts] + [pts[-1][3]]
            plt.plot(xs, ys, color=_rgb(sg.get("color"), (95, 179, 168)), marker="braille")
    for im in ax["images"]:
        shape = im["shape"]
        vals = im["values"]
        if len(shape) == 2 and shape[0] * shape[1] == len(vals):
            rows = [[0.0 if v is None else v for v in vals[r * shape[1]:(r + 1) * shape[1]]] for r in range(shape[0])]
            plt.matrix_plot(rows)
    plot.refresh()


class ChartGrid(VerticalScroll):
    """Shows one chart spec: a grid of plots and text panels."""

    DEFAULT_CSS = """
    ChartGrid { height: 1fr; }
    ChartGrid Horizontal { height: auto; }
    ChartGrid PlotextPlot { height: %d; width: 1fr; margin: 0 1; }
    ChartGrid .textpanel { width: 1fr; height: auto; min-height: 5; margin: 0 1; padding: 0 1; border: round $primary 30%%; }
    ChartGrid .head { text-style: bold; padding: 0 1; color: $accent; }
    """ % PLOT_ROWS

    async def show(self, spec: dict | None, message: str = "") -> None:
        await self.remove_children()
        if spec is None:
            await self.mount(Static(message or "Load a lap first.", classes="textpanel"))
            return
        widgets = []
        if spec.get("title"):
            widgets.append(Static(spec["title"], classes="head"))
        axes = [a for a in spec["axes"] if has_plot(a) or a["texts"] or a["title"]]   # map axes are drawn with the frame off
        # rows: axes whose tops are within 4% of each other share a row, left to right
        axes.sort(key=lambda a: (-round((a["box"][1] + a["box"][3]) / 0.04), a["box"][0]))
        rows, last = [], None
        for ax in axes:
            top = round((ax["box"][1] + ax["box"][3]) / 0.04)
            if top != last:
                rows.append([])
                last = top
            rows[-1].append(ax)
        pending = []
        for row in rows:
            kids = []
            for ax in row:
                if has_plot(ax):
                    plot = PlotextPlot()
                    kids.append(plot)
                    pending.append((plot, ax))
                else:
                    body = text_of(ax) or ax["title"] or ""
                    kids.append(Static(Text(body), classes="textpanel"))
            widgets.append(Horizontal(*kids))
        await self.mount_all(widgets)
        for plot, ax in pending:
            # draw once the widget is mounted so plotext knows its size
            self.call_after_refresh(draw_axes, plot, ax)
