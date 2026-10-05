"""share_card.py -- the shareable 1200x630 PNG behind the Lap Analyst's lap card
and the Race Analyst's race card.

Both cards have the same four blocks, filled differently:

    headline stats    car, track, big time, six small tiles
    chart             lap card: speed trace cut into sectors with their times
                      race card: every lap time, best one highlighted
    track map         the line coloured by speed
    pit stops + fuel  lap card: fuel used on the lap and the fuel curve
                      race card: stop laps and fuel burned per lap

The analysts turn their data into a plain `spec` dict (see render_card) and
this module only draws it, so the maths stays testable without any pixels.
Drawn on a bare Figure + Agg canvas -- no pyplot, no Tk -- with a fixed dark
palette, so the image looks the same for everyone whatever theme the app uses
and it works headless.
"""

from __future__ import annotations

import io
import math

import numpy as np

CARD = dict(
    bg="#07080f",
    panel="#0d0e1a",
    grid="#1c1e30",
    fg="#c8d3f5",
    dim="#6b7194",
    cyan="#00f0d4",
    green="#39ff85",
    red="#ff2255",
    yellow="#ffd500",
)

# block rectangles in figure coordinates: [left, bottom, width, height]
_HEADLINE = [0.03, 0.05, 0.31, 0.79]
_CHART = [0.41, 0.55, 0.56, 0.285]
_MAP = [0.37, 0.07, 0.28, 0.33]
_FUEL = [0.71, 0.10, 0.26, 0.29]

_MONO = dict(family="monospace")


def fmt_time(t) -> str:
    """m:ss.mmm, rounded to the millisecond first so 59.9996 s reads 1:00.000, not 0:60.000."""
    try:
        f = float(t)
    except (TypeError, ValueError):
        return "--:--.---"
    if not math.isfinite(f):
        return "--:--.---"
    total_ms = int(round(f * 1000))
    m, ms = divmod(total_ms, 60000)
    return f"{m}:{ms / 1000:06.3f}"


def _arr(v) -> np.ndarray:
    try:
        return np.asarray(v if v is not None else [], dtype=float)
    except (TypeError, ValueError):
        return np.array([], dtype=float)


def _style(ax):
    ax.set_facecolor(CARD["panel"])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=CARD["dim"], labelsize=7, length=0)
    ax.grid(True, color=CARD["grid"], lw=0.6)


def _blank(ax, msg: str):
    ax.set_facecolor(CARD["panel"])
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.text(0.5, 0.5, msg, color=CARD["dim"], fontsize=8, ha="center", va="center", transform=ax.transAxes, **_MONO)


def _block_title(fig, rect, text):
    fig.text(rect[0], rect[1] + rect[3] + 0.018, text, color=CARD["dim"], fontsize=7, va="center", **_MONO)


def _clock(v, _pos=None) -> str:
    return f"{int(v // 60)}:{v % 60:04.1f}"


# ── blocks ───────────────────────────────────────────────────────────────────
def _draw_headline(fig, spec):
    c = CARD
    ax = fig.add_axes(_HEADLINE)
    ax.set_facecolor(c["panel"])
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    h = spec.get("headline") or {}
    t = ax.transAxes
    ax.text(0.06, 0.93, str(spec.get("car") or "Unknown car")[:26], color=c["fg"], fontsize=14,
            fontweight="bold", va="center", transform=t, **_MONO)
    ax.text(0.06, 0.84, str(spec.get("track") or "Unknown track")[:30], color=c["cyan"], fontsize=11,
            va="center", transform=t, **_MONO)
    ax.text(0.06, 0.69, str(h.get("big_value") or "--:--.---"), color=c["green"], fontsize=31,
            fontweight="bold", va="center", transform=t, **_MONO)
    label = str(h.get("big_label") or "")
    if spec.get("era"):
        label += f"   [{spec['era']}]"
    ax.text(0.06, 0.585, label, color=c["dim"], fontsize=8, va="center", transform=t, **_MONO)
    for i, (name, value) in enumerate((h.get("tiles") or [])[:6]):
        x = 0.06 + (i % 2) * 0.49
        y = 0.45 - (i // 2) * 0.165
        ax.text(x, y + 0.045, str(name), color=c["dim"], fontsize=7, va="center", transform=t, **_MONO)
        ax.text(x, y - 0.025, str(value), color=c["fg"], fontsize=12, fontweight="bold", va="center",
                transform=t, **_MONO)


def _draw_chart(fig, spec):
    ch = spec.get("chart") or {}
    c = CARD
    _block_title(fig, _CHART, str(ch.get("title") or "").upper())
    ax = fig.add_axes(_CHART)
    x, y = _arr(ch.get("x")), _arr(ch.get("y"))
    if len(x) < 2 or len(x) != len(y):
        _blank(ax, "no data")
        return
    _style(ax)
    if ch.get("kind") == "bars":
        n = len(x)
        colors = [c["cyan"]] * n
        for i, dim in enumerate(ch.get("dim") or []):
            if dim and i < n:
                colors[i] = c["dim"]
        best = ch.get("best")
        if best is not None and 0 <= best < n:
            colors[best] = c["green"]
        ax.bar(x, y, color=colors, width=0.7)
        lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
        top = min(hi, lo * 1.12)  # a pit or spin lap must not squash the rest flat
        ax.set_ylim(lo * 0.985, top * 1.01 if top > lo else lo * 1.02)
        ax.yaxis.set_major_formatter(_FuncFormatter(_clock))
        step = max(1, int(math.ceil(n / 20)))
        ax.set_xticks(x[::step])
        ax.tick_params(axis="x", labelsize=6)
        if best is not None and 0 <= best < n:
            ax.text(x[best], min(y[best], top) * 1.0, fmt_time(y[best]), color=c["green"], fontsize=7,
                    ha="center", va="bottom", fontweight="bold", **_MONO)
        ax.set_xlim(x.min() - 0.8, x.max() + 0.8)
        return
    # trace: speed round the lap, cut into sectors with their times
    ax.plot(x, y, color=c["cyan"], lw=1.6)
    ax.fill_between(x, y, alpha=0.15, color=c["cyan"])
    ax.set_ylabel(str(ch.get("ylabel") or ""), color=c["dim"], fontsize=7)
    ax.set_xlim(float(x.min()), float(x.max()))
    ymax = float(np.nanmax(y)) or 1.0
    ax.set_ylim(0, ymax * 1.18)
    edges = _arr(ch.get("edges"))
    times = ch.get("sector_times") or []
    for e in edges[1:-1]:
        ax.axvline(e, color=c["dim"], lw=0.8, ls="--")
    for i, tm in enumerate(times):
        if i + 1 < len(edges):
            mid = (edges[i] + edges[i + 1]) / 2
            ax.text(mid, ymax * 1.12, f"S{i + 1}  {tm:.2f}s", color=c["yellow"], fontsize=8, ha="center",
                    va="center", fontweight="bold", **_MONO)


def _draw_map(fig, spec):
    from matplotlib.collections import LineCollection
    from matplotlib.colors import Normalize

    c = CARD
    _block_title(fig, _MAP, "TRACK MAP  -  COLOURED BY SPEED")
    m = spec.get("map")
    ax = fig.add_axes(_MAP)
    if not m:
        _blank(ax, "no GPS data in this file")
        return
    x, z, v = _arr(m.get("x")), _arr(m.get("z")), _arr(m.get("v"))
    if len(x) < 3 or not (len(x) == len(z) == len(v)):
        _blank(ax, "no GPS data in this file")
        return
    ax.set_facecolor(c["panel"])
    pts = np.array([x, z]).T.reshape(-1, 1, 2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    lc = LineCollection(segs, cmap="turbo", norm=Normalize(float(v.min()), float(v.max()) or 1.0), lw=3)
    lc.set_array(v[:-1])
    ax.add_collection(lc)
    ax.autoscale()
    ax.set_aspect("equal")
    ax.axis("off")
    ax.text(0.01, 0.0, f"{v.min():.0f} - {v.max():.0f} km/h", color=c["dim"], fontsize=6, ha="left",
            va="bottom", transform=ax.transAxes, **_MONO)


def _draw_fuel(fig, spec):
    c = CARD
    f = spec.get("fuel") or {}
    top = _FUEL[1] + _FUEL[3]
    fig.text(_FUEL[0], top + 0.078, str(f.get("title") or "PIT STOPS + FUEL").upper(), color=c["dim"], fontsize=7,
             va="center", **_MONO)
    fig.text(_FUEL[0], top + 0.046, str(f.get("headline") or ""), color=c["fg"], fontsize=10,
             fontweight="bold", va="center", **_MONO)
    if f.get("foot"):
        fig.text(_FUEL[0], top + 0.016, str(f["foot"])[:46], color=c["dim"], fontsize=6.5, va="center", **_MONO)
    ax = fig.add_axes(_FUEL)
    bars, trace = f.get("bars"), f.get("trace")
    if bars and len(_arr(bars.get("x"))) >= 1:
        x, y = _arr(bars["x"]), _arr(bars.get("y"))
        _style(ax)
        marks = list(bars.get("pit") or [])
        colors = [c["yellow"] if (i < len(marks) and marks[i]) else c["green"] for i in range(len(x))]
        ax.bar(x, y, color=colors, width=0.7)
        ax.set_xticks(x[:: max(1, int(math.ceil(len(x) / 14)))])
        ax.tick_params(axis="x", labelsize=6)
        ax.set_ylabel(str(bars.get("ylabel") or ""), color=c["dim"], fontsize=7)
    elif trace and len(_arr(trace.get("x"))) >= 2:
        x, y = _arr(trace["x"]), _arr(trace.get("y"))
        _style(ax)
        ax.plot(x, y, color=c["green"], lw=1.6)
        ax.fill_between(x, y, alpha=0.15, color=c["green"])
        ax.set_ylabel(str(trace.get("ylabel") or ""), color=c["dim"], fontsize=7)
        ax.set_xlim(float(x.min()), float(x.max()))
    else:
        _blank(ax, str(f.get("empty") or "no fuel data"))


def render_card(spec: dict, width: int = 1200, height: int = 630) -> bytes:
    """Draw a card from `spec` and return PNG bytes. Keys (all optional):

        kind      "LAP" or "RACE"
        when      date text, top right
        car, track, era
        headline  {"big_label", "big_value", "tiles": [(label, value), ...6]}
        chart     {"title", "kind": "bars" | "trace", "x", "y", ...} -- bars take
                  "best" (index) and "dim" (flags); a trace takes "edges",
                  "sector_times", "ylabel"
        map       {"x", "z", "v"} world coordinates and speed, or None
        fuel      {"title", "headline", "foot", "bars": {"x", "y", "pit"} or
                  "trace": {"x", "y"}}
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    c = CARD
    dpi = 100
    fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor=c["bg"])
    FigureCanvasAgg(fig)

    fig.text(0.03, 0.945, "TRACE", color=c["cyan"], fontsize=22, fontweight="bold", va="center", **_MONO)
    fig.text(0.03, 0.903, f"GT7 TELEMETRY  -  {spec.get('kind', 'LAP')} CARD", color=c["dim"], fontsize=8,
             va="center", **_MONO)
    fig.text(0.97, 0.945, str(spec.get("when") or ""), color=c["dim"], fontsize=10, ha="right", va="center",
             **_MONO)

    for draw in (_draw_headline, _draw_chart, _draw_map, _draw_fuel):
        try:
            draw(fig, spec)
        except Exception:  # one broken block must not cost the whole card
            continue

    fig.text(0.97, 0.035, "gt7trace.netlify.app  -  made with TRACE", color=c["dim"], fontsize=7, ha="right",
             va="center", **_MONO)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=c["bg"])
    return buf.getvalue()


def _FuncFormatter(fn):  # noqa: N802  (kept lazy so importing this module stays cheap)
    from matplotlib.ticker import FuncFormatter

    return FuncFormatter(fn)
