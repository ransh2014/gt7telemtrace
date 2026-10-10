"""share_card.py -- the shareable 1200x630 PNG behind the Lap Analyst's lap card
and the Race Analyst's race card.

Both cards have the same three blocks, filled differently:

    headline stats    car, track, big time, a few small tiles
    chart             lap card: speed round the lap
                      race card: every lap time, best one highlighted
    track map         the line coloured by speed

A card only shows numbers that are read straight from the recording (no
estimates), so it can't disagree with the game.

The analysts turn their data into a plain `spec` dict (see render_card) and
this module only draws it, so the maths stays testable without any pixels.
Drawn on a bare Figure + Agg canvas -- no pyplot, no Tk -- with a fixed dark
palette, so the image looks the same for everyone whatever theme the app uses
and it works headless.
"""

from __future__ import annotations

import io
import math
import textwrap

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
_HEADLINE = [0.03, 0.07, 0.31, 0.77]
_CHART = [0.41, 0.53, 0.56, 0.30]
_MAP = [0.41, 0.08, 0.56, 0.36]

_MONO = dict(family="monospace")

# car name: full size up to this many characters, then smaller, then two lines
_CAR_FULL_CHARS = 24
_CAR_MIN_FONT = 9.0
_CAR_LINE_CHARS = 44  # what fits on one line of the headline panel at the minimum font


def fmt_time(t, decimals: int = 3) -> str:
    """m:ss.mmm (or fewer decimals), rounded first so 59.9996 s reads 1:00.000, not 0:60.000."""
    blank = "--:--." + "-" * decimals
    try:
        f = float(t)
    except (TypeError, ValueError):
        return blank
    if not math.isfinite(f):
        return blank
    unit = 10**decimals
    total = int(round(f * unit))
    m, rest = divmod(total, 60 * unit)
    width = 3 + decimals if decimals else 2
    return f"{m}:{rest / unit:0{width}.{decimals}f}"


def car_title(name) -> tuple[str, float]:
    """(text, font size) for the car name: the whole name, never cut. Long names
    get a smaller font, and very long ones wrap onto a second line."""
    name = str(name or "Unknown car").strip() or "Unknown car"
    n = len(name)
    if n <= _CAR_FULL_CHARS:
        return name, 14.0
    size = 14.0 * _CAR_FULL_CHARS / n
    if size >= _CAR_MIN_FONT:
        return name, size
    return "\n".join(textwrap.wrap(name, _CAR_LINE_CHARS)[:2] or [name]), _CAR_MIN_FONT


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
    car, size = car_title(spec.get("car"))
    ax.text(0.06, 0.92, car, color=c["fg"], fontsize=size, fontweight="bold", va="center", linespacing=1.1,
            transform=t, **_MONO)
    track = str(spec.get("track") or "Unknown track")
    ax.text(0.06, 0.81, track, color=c["cyan"], fontsize=11 if len(track) <= 30 else 8, va="center",
            transform=t, **_MONO)
    ax.text(0.06, 0.64, str(h.get("big_value") or "--:--.---"), color=c["green"], fontsize=31,
            fontweight="bold", va="center", transform=t, **_MONO)
    label = str(h.get("big_label") or "")
    if spec.get("era"):
        label += f"   [{spec['era']}]"
    ax.text(0.06, 0.53, label, color=c["dim"], fontsize=8, va="center", transform=t, **_MONO)
    for i, (name, value) in enumerate((h.get("tiles") or [])[:4]):
        x = 0.06 + (i % 2) * 0.49
        y = 0.36 - (i // 2) * 0.2
        ax.text(x, y + 0.05, str(name), color=c["dim"], fontsize=7, va="center", transform=t, **_MONO)
        ax.text(x, y - 0.03, str(value), color=c["fg"], fontsize=13, fontweight="bold", va="center",
                transform=t, **_MONO)


def _draw_chart(fig, spec):
    ch = spec.get("chart") or {}
    c = CARD
    _block_title(fig, _CHART, str(ch.get("title") or "").upper())
    ax = fig.add_axes(_CHART)
    x, y = _arr(ch.get("x")), _arr(ch.get("y"))
    if ch.get("kind") == "bars":
        if len(x) < 1 or len(x) != len(y):
            _blank(ax, "no complete laps")
            return
        _style(ax)
        n = len(x)
        colors = [c["cyan"]] * n
        best = ch.get("best")
        if best is not None and 0 <= best < n:
            colors[best] = c["green"]
        ax.bar(x, y, color=colors, width=0.7)
        lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
        # every bar at its real height: the axis always reaches the slowest lap
        pad = max(hi - lo, lo * 0.01, 0.5)
        ax.set_ylim(max(0.0, lo - pad * 0.6), hi + pad * 0.25)
        ax.yaxis.set_major_formatter(_FuncFormatter(_clock))
        step = max(1, int(math.ceil(n / 20)))
        ax.set_xticks(x[::step])
        ax.tick_params(axis="x", labelsize=6)
        if best is not None and 0 <= best < n:
            ax.text(x[best], y[best], fmt_time(y[best], 2), color=c["green"], fontsize=7,
                    ha="center", va="bottom", fontweight="bold", **_MONO)
        ax.set_xlim(x.min() - 0.8, x.max() + 0.8)
        return
    if len(x) < 2 or len(x) != len(y):
        _blank(ax, "no data")
        return
    # trace: speed round the lap
    _style(ax)
    ax.plot(x, y, color=c["cyan"], lw=1.6)
    ax.fill_between(x, y, alpha=0.15, color=c["cyan"])
    ax.set_ylabel(str(ch.get("ylabel") or ""), color=c["dim"], fontsize=7)
    ax.set_xlim(float(x.min()), float(x.max()))
    ax.set_ylim(0, (float(np.nanmax(y)) or 1.0) * 1.08)


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


def render_card(spec: dict, width: int = 1200, height: int = 630) -> bytes:
    """Draw a card from `spec` and return PNG bytes. Keys (all optional):

        kind      "LAP" or "RACE"
        when      date text, top right
        car, track, era
        headline  {"big_label", "big_value", "tiles": [(label, value), ...up to 4]}
        chart     {"title", "kind": "bars" | "trace", "x", "y", ...} -- bars take
                  "best" (index); a trace takes "ylabel"
        map       {"x", "z", "v"} world coordinates and speed, or None
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

    for draw in (_draw_headline, _draw_chart, _draw_map):
        try:
            draw(fig, spec)
        except Exception:  # one broken block must not cost the whole card
            continue

    fig.text(0.97, 0.025, "gt7trace.netlify.app  -  made with TRACE", color=c["dim"], fontsize=7, ha="right",
             va="center", **_MONO)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=c["bg"])
    return buf.getvalue()


def _FuncFormatter(fn):  # noqa: N802  (kept lazy so importing this module stays cheap)
    from matplotlib.ticker import FuncFormatter

    return FuncFormatter(fn)
