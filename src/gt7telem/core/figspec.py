"""Turn a drawn matplotlib Figure into plain data (a "chart spec") any front end can render.

The chart functions in lap_charts.py / race_charts.py are the single source of truth for what a chart shows. The Tk
window displays the real figure; the terminal UI renders the spec this module extracts from the same figure, so the
two can never describe different charts. Pure data out: lists, numbers, strings, hex colours.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
from matplotlib.colors import to_hex

MAX_POINTS = 600          # points kept per series in a spec (the terminal is ~200 columns wide)
_ROUND = 4                # significant digits kept, so two machines produce the same fingerprint


def _num(a):
    arr = np.asarray(a, dtype=float).ravel()
    return arr


def _thin(arr, n=MAX_POINTS):
    if len(arr) <= n:
        return arr
    idx = np.linspace(0, len(arr) - 1, n).round().astype(int)
    return arr[idx]


IMAGE_MAX = 6000          # heat maps keep every cell up to this many so the terminal can redraw the grid


def _clean(arr):
    """JSON-safe list: NaN/inf become None, floats rounded to _ROUND significant digits."""
    out = []
    for v in arr:
        if not np.isfinite(v):
            out.append(None)
        elif v == 0:
            out.append(0.0)
        else:
            mag = int(np.floor(np.log10(abs(v))))
            out.append(round(float(v), max(0, _ROUND - 1 - mag)))
    return out


def _hex(c):
    try:
        return to_hex(c, keep_alpha=False)
    except (ValueError, TypeError):
        return None


def _series(x, y):
    x, y = _num(x), _num(y)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    return _clean(_thin(x)), _clean(_thin(y))


def axes_spec(ax) -> dict:
    pos = ax.get_position()
    spec = {
        "box": [round(pos.x0, 3), round(pos.y0, 3), round(pos.width, 3), round(pos.height, 3)],
        "title": ax.get_title(),
        "xlabel": ax.get_xlabel(),
        "ylabel": ax.get_ylabel(),
        "xlim": _clean(_num(ax.get_xlim())),
        "ylim": _clean(_num(ax.get_ylim())),
        "visible": bool(ax.axison),
        "lines": [], "bars": [], "scatter": [], "segments": [], "images": [], "texts": [], "legend": [],
    }
    for ln in ax.lines:
        x, y = ln.get_xdata(orig=False), ln.get_ydata(orig=False)
        if len(x) < 1:
            continue
        xs, ys = _series(x, y)
        spec["lines"].append({"x": xs, "y": ys, "color": _hex(ln.get_color()), "label": ln.get_label()
                              if not str(ln.get_label()).startswith("_") else "",
                              "style": ln.get_linestyle(), "step": ln.get_drawstyle().startswith("steps")})
    bars = []
    for p in ax.patches:
        if hasattr(p, "get_width") and hasattr(p, "get_height") and hasattr(p, "get_x"):
            bars.append([p.get_x(), p.get_y(), p.get_width(), p.get_height(), _hex(p.get_facecolor())])
    if bars:
        cols = [b[4] for b in bars]
        arr = np.array([b[:4] for b in bars], dtype=float)
        spec["bars"] = [{"x": _clean(arr[:, 0]), "y": _clean(arr[:, 1]), "w": _clean(arr[:, 2]),
                         "h": _clean(arr[:, 3]), "color": cols}][0:1]
    for col in ax.collections:
        name = type(col).__name__
        if name == "PathCollection":
            off = np.asarray(col.get_offsets(), dtype=float)
            if off.ndim == 2 and len(off):
                arr = col.get_array()
                spec["scatter"].append({
                    "x": _clean(_thin(off[:, 0])), "y": _clean(_thin(off[:, 1])),
                    "values": None if arr is None else _clean(_thin(_num(arr))),
                    "color": None if arr is not None else _hex(col.get_facecolor()[0]) if len(col.get_facecolor()) else None,
                    "cmap": col.get_cmap().name if arr is not None else None})
        elif name == "LineCollection":
            segs = col.get_segments()
            arr = col.get_array()
            vals = None if arr is None else _num(arr)
            step = max(1, len(segs) // MAX_POINTS)
            spec["segments"].append({
                "pts": [[_clean(s[:, 0])[0], _clean(s[:, 1])[0], _clean(s[:, 0])[-1], _clean(s[:, 1])[-1]]
                        for s in segs[::step] if len(s) >= 2],
                "values": None if vals is None else _clean(vals[::step]),
                "cmap": col.get_cmap().name if arr is not None else None,
                "color": _hex(col.get_color()[0]) if arr is None and len(col.get_color()) else None})
        elif name in ("QuadMesh", "PolyCollection"):
            arr = col.get_array()
            if arr is not None:
                spec["images"].append({"shape": list(np.shape(arr)),
                                       "values": _clean(_thin(_num(arr), IMAGE_MAX))})
    for im in ax.images:
        a = np.asarray(im.get_array(), dtype=float)
        spec["images"].append({"shape": list(a.shape), "values": _clean(_thin(a.ravel(), IMAGE_MAX))})
    for t in ax.texts:
        s = t.get_text()
        if s:
            x, y = t.get_position()
            spec["texts"].append({"x": float(round(x, 3)), "y": float(round(y, 3)), "s": s,
                                  "color": _hex(t.get_color()), "axes_coords": t.get_transform() == ax.transAxes})
    leg = ax.get_legend()
    if leg is not None:
        spec["legend"] = [t.get_text() for t in leg.get_texts()]
    return spec


def figure_spec(fig) -> dict:
    """The whole figure: suptitle plus every axes in drawing order."""
    sup = fig._suptitle.get_text() if getattr(fig, "_suptitle", None) is not None else ""
    axes = []
    for ax in fig.axes:
        # colorbars are axes too; they carry no data of their own
        if ax.get_label() == "<colorbar>" or (not ax.lines and not ax.patches and not ax.collections
                                              and not ax.texts and not ax.images and not ax.get_title()):
            continue
        axes.append(axes_spec(ax))
    return {"title": sup, "axes": axes}


def fingerprint(spec: dict) -> str:
    """Short stable hash of a spec, for golden tests and parity checks."""
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
