"""Render any Lap or Race Analyst chart group without a window and hand back a plain-data spec."""

from __future__ import annotations

import sys

import matplotlib

if "matplotlib.pyplot" not in sys.modules:       # no Tk window around: draw to memory, never to a screen
    matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402

from . import figspec, registry  # noqa: E402


def _draw(fn, size, theme_bg, *args, **kw) -> dict:
    fig = Figure(figsize=size, facecolor=theme_bg)
    try:
        fn(fig, *args, **kw)
        return figspec.figure_spec(fig)
    finally:
        fig.clear()


def lap_chart(name, df, dfb=None, *, extra=None, consensus=None, pool=None) -> dict:
    """Spec for a Lap Analyst group. The keyword arguments mirror what the Tk window passes each group."""
    from .. import lap_charts
    fn, size = registry.lap_group(name)
    if name == "Consensus":
        return _draw(fn, size, lap_charts.BG, df, dfb, consensus)
    if name == "Theoretical":
        return _draw(fn, size, lap_charts.BG, df, dfb, pool)
    if name == "Inputs" and extra:
        return _draw(fn, size, lap_charts.BG, df, dfb, extra)
    return _draw(fn, size, lap_charts.BG, df, dfb)


def race_chart(name, df, dfb=None, *, baseline=None) -> dict:
    from .. import race_charts
    fn, size = registry.race_group(name)
    if name == "Corners":
        return _draw(fn, size, race_charts.BG, df, dfb, baseline)
    return _draw(fn, size, race_charts.BG, df, dfb)
