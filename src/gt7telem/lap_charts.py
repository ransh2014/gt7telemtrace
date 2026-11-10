"""The Lap Analyst's data loading and chart groups.

No tkinter in here: the Tk window (lap_analyst.py) and the terminal UI both import this module, so a chart exists once.
"""
# ruff: noqa: E402
import json
import math
import os
import warnings
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection
from matplotlib.gridspec import GridSpec

from . import config as runtime_config
from . import (
    corners,
    dyno,
    leaderboard,
    share_card,
    theme,
    theoretical,
)

warnings.filterwarnings("ignore")

# ── Theme ─────────────────────────────────────────────────────────────────────
if runtime_config.THEME == "light":
    BG   = "#f4f2ee"
    PNL  = "#ffffff"
    PNL2 = "#ebe8e2"
    ACC  = "#c8102e"
    CYN  = "#1c5c58"
    GRN  = "#23743f"
    YLW  = "#8a6a1c"
    ORG  = "#8c0c22"
    PRP  = "#55535c"
    FG   = "#16161a"
    DIM  = "#7a7880"
    DIM2 = "#e2dfd8"
else:
    BG   = "#08080a"
    PNL  = "#0e0e11"
    PNL2 = "#17171b"
    ACC  = "#e2455a"
    CYN  = "#5fb3a8"
    GRN  = "#3c9a5f"
    YLW  = "#c9a24b"
    ORG  = "#c8102e"
    PRP  = "#96949c"
    FG   = "#f2f0ec"
    DIM  = "#605e68"
    DIM2 = "#202024"
C = dict(speed=CYN, throttle=GRN, brake=ACC, gear=YLW, rpm=ORG,
         steering=PRP, clutch="#96949c",
         fl=ACC, fr=YLW, rl=GRN, rr=CYN,
         col_a=CYN, col_b=ACC)

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": PNL2,
    "axes.edgecolor": DIM, "axes.labelcolor": DIM,
    "text.color": FG, "xtick.color": DIM, "ytick.color": DIM,
    "grid.color": DIM2, "grid.alpha": 1.0,
    "legend.facecolor": PNL, "legend.edgecolor": DIM,
    "font.family": theme.mpl_font_family(), "font.size": 8,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.titlepad": 6,
})

# ── Data ──────────────────────────────────────────────────────────────────────
_LAP_NUM_COLS = [
    "world_x", "world_y", "world_z", "speed_kmh", "throttle", "brake", "steering",
    "clutch", "clutch_engaged", "gear", "suggested_gear", "rpm", "max_rpm",
    "rpm_warning", "rpm_limiter", "rpm_after_clutch", "boost",
    "tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr",
    "tyre_slip_fl", "tyre_slip_fr", "tyre_slip_rl", "tyre_slip_rr",
    "susp_fl", "susp_fr", "susp_rl", "susp_rr", "ride_height_mm",
    "fuel_remaining", "fuel_capacity", "oil_temp", "water_temp", "oil_pressure",
    "ang_x", "ang_y", "ang_z", "vel_x", "vel_y", "vel_z",
    "heading", "track_position", "t",
    "wheel_rotation", "steering_angular_velocity", "sway", "heave", "surge",
    "energy_recovery", "current_lap_ms",
    "wheel_steering_angle_l", "wheel_steering_angle_r", "wheel_base",
]


def _num(v, default=0.0):
    """float(v) when it is a finite number, else `default` (None, text, NaN
    and infinity all fall back instead of raising)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def load_lap_data(data):
    """Build the analysis DataFrame from an already-parsed lap dict --
    shared by load_lap() (reads a local JSON file) and ghost-lap download
    (data comes from Supabase instead of disk, via leaderboard.py). A
    downloaded ghost's samples only carry the compact fields
    (track_position/speed_kmh/throttle/brake/steering/gear), so every other
    column below falls back to its 0.0/"" default -- meaning no GPS map or
    replay for a ghost, but the input-trace charts and A/B diffs work fine.

    A ghost has no timestamps, so `t` is rebuilt from distance and speed and
    scaled to the lap's real time; df.attrs["compact"] marks such a lap so
    charts that need full telemetry can say so instead of inventing numbers."""
    if not isinstance(data, dict):
        raise ValueError("This file is not a TRACE lap")
    samples = data.get("samples")
    samples = [s for s in samples if isinstance(s, dict)] if isinstance(samples, list) else []
    if not samples:
        raise ValueError("No samples in file")
    df = pd.DataFrame(samples)
    raw_cols = set(df.columns)
    for col in _LAP_NUM_COLS:
        if col not in df:
            df[col] = 0.0
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df[_LAP_NUM_COLS] = df[_LAP_NUM_COLS].replace([np.inf, -np.inf], np.nan)
    for col in ["surface_type", "car_category"]:
        if col not in df:
            df[col] = ""
    df = df.fillna(0)

    # Timestamps / distance: older or downloaded laps may lack them.
    compact = not ({"t", "rpm", "tyre_temp_fl"} & raw_cols)
    lap_s = _num(data.get("lap_time_s"))
    speed_ms = df["speed_kmh"] / 3.6
    has_t = "t" in raw_cols and len(df) > 1 and float(df["t"].max() - df["t"].min()) > 0
    has_pos = "track_position" in raw_cols and float(df["track_position"].max()) > 0
    if not has_t:
        if has_pos:
            dpos = df["track_position"].diff().clip(lower=0).fillna(0)
            tt = (dpos / speed_ms.clip(lower=1.0)).cumsum()
        else:
            tt = pd.Series(np.arange(len(df)) * 0.1, index=df.index)
        if lap_s > 0 and float(tt.iloc[-1]) > 0:
            tt = tt * (lap_s / float(tt.iloc[-1]))
        df["t"] = tt.to_numpy(dtype=float)
        df.attrs["t_derived"] = True
    if not has_pos:
        dtp = df["t"].diff().fillna(0).clip(lower=0)
        df["track_position"] = (speed_ms * dtp).cumsum().to_numpy(dtype=float)
        df.attrs["pos_derived"] = True
    df.attrs["compact"] = bool(compact)
    # Per-car constants for the Dyno & Tune tab (the lap file stores them once, not per sample).
    df.attrs["car"] = str(data.get("car_display") or data.get("car") or "")
    _gr = data.get("gear_ratios")
    df.attrs["gear_ratios"] = [_num(x) for x in _gr] if isinstance(_gr, list) else []
    _tr = _num(data.get("tyre_radius_m"))
    df.attrs["tyre_radius_m"] = _tr if _tr > 0 else None

    # GT7 sends suspension height in metres; every chart labels it mm.
    for _c in ("susp_fl", "susp_fr", "susp_rl", "susp_rr"):
        df[_c] = df[_c] * 1000.0
    dt = df["t"].diff()
    pos_dt = dt[dt > 1e-4]
    fallback = float(pos_dt.median()) if len(pos_dt) else 0.1
    dt = dt.where(dt > 1e-4, fallback).fillna(fallback)
    dv = df["speed_kmh"].diff().fillna(0) / 3.6
    df["long_g"]       = (dv / dt / 9.81).clip(-4, 4)
    df["lat_g"]        = (df["ang_y"] * df["speed_kmh"] / 3.6 / 9.81).clip(-4, 4)
    df["total_g"]      = np.sqrt(df["long_g"]**2 + df["lat_g"]**2)
    df["fuel_burn"]    = (-df["fuel_remaining"].diff() / dt).clip(0, 100).fillna(0)
    df["coasting"]     = ((df["throttle"] < 0.05) & (df["brake"] < 0.05)).astype(float)
    df["front_t_avg"]  = (df["tyre_temp_fl"] + df["tyre_temp_fr"]) / 2
    df["rear_t_avg"]   = (df["tyre_temp_rl"] + df["tyre_temp_rr"]) / 2
    df["lr_t_bal"]     = (df["tyre_temp_fl"] + df["tyre_temp_rl"]) / 2 \
                       - (df["tyre_temp_fr"] + df["tyre_temp_rr"]) / 2
    return data, df


def is_compact(df):
    """True for a leaderboard ghost (only the six uploaded input channels)."""
    return df is not None and bool(getattr(df, "attrs", {}).get("compact"))


def load_lap(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    return load_lap_data(data)


def export_csv(df, out_path):
    """Dump a lap's per-sample telemetry to CSV. distance_m is the lap's own
    track position (distance round the lap); laps without one fall back to
    integrating speed over each frame's dt. Written to a temp file first so a
    failure (file open in Excel, disk full) never truncates an existing file."""
    n = len(df)
    pos = df["track_position"].to_numpy(dtype=float) if "track_position" in df.columns else np.zeros(n)
    if n and float(np.nanmax(pos) - np.nanmin(pos)) > 0:
        dist = pos - pos[0]
    else:
        dt = df["t"].diff()
        pos_dt = dt[dt > 1e-4]
        fb = float(pos_dt.median()) if len(pos_dt) else 0.1
        dt = dt.where(dt > 1e-4, fb).fillna(fb)
        dist = (df["speed_kmh"] / 3.6 * dt).cumsum().to_numpy(dtype=float)
    out = pd.DataFrame({
        "distance_m": np.round(dist, 2),
        "speed_kmh":  df["speed_kmh"].round(2),
        "throttle":   df["throttle"].round(4),
        "brake":      df["brake"].round(4),
        "rpm":        df["rpm"].round(1),
        "gear":       df["gear"].fillna(0).astype(int),
        "steering":   df["steering"].round(4),
    })
    out_path = Path(out_path)
    tmp = out_path.with_name(out_path.name + ".tmp")
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            out.to_csv(f, index=False, lineterminator="\r\n")
        os.replace(tmp, out_path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _safe_name(text, default="lap"):
    """File-name-safe version of a label (no path separators or reserved characters)."""
    s = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in str(text or "").strip())
    return s.strip("._") or default


def _save_df_csv(df, path):
    """DataFrame -> CSV via a temp file, so a failure never truncates an existing file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise




_LAP_META_CACHE = {}


def _scan_laps(folder, limit=300):
    """Lap files under `folder` (the Dashboard saves them as <track>/<car>_<ts>.json),
    newest first, as picker rows. Parsed rows are cached by (path, mtime) so
    re-opening the picker is instant; non-lap JSON is skipped."""
    entries = []
    try:
        if not folder.exists():
            return entries
        files = sorted(folder.rglob("*.json"), key=lambda q: q.stat().st_mtime, reverse=True)
    except OSError:
        return entries
    # Race recordings (<track>/races/race_*.json) are not laps.
    files = [q for q in files if q.parent.name.lower() not in ("races", "_session")
             and not q.name.lower().startswith("race_")]
    for f in files[:limit]:
        try:
            st = f.stat()
        except OSError:
            continue
        key = (str(f), st.st_mtime_ns)
        e = _LAP_META_CACHE.get(key)
        if e is None:
            try:
                with open(f, encoding="utf-8-sig") as fh:
                    d = json.load(fh)
                if not isinstance(d, dict) or not isinstance(d.get("samples"), list):
                    continue
                stamp = str(d.get("recorded_at") or "")
                try:
                    date_s = datetime.strptime(stamp[:15], "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    date_s = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                lt = _num(d.get("lap_time_s"))
                e = {"path": str(f),
                     "car": str(d.get("car_display") or d.get("car") or "Unknown car"),
                     "track": str(d.get("track_display") or d.get("track") or "Unknown track"),
                     "date": date_s, "lap_time": _fmt_laptime(lt) if lt > 0 else ""}
            except Exception:
                continue
            _LAP_META_CACHE[key] = e
        entries.append(e)
    return entries


def _lap_xy(df):
    """(position, time) arrays for a lap, both non-decreasing. Position is the
    lap's own track_position (m); a stray post-finish sample that wrapped back
    to ~0 is flattened rather than sorted to the start, and ties are harmless
    to np.interp."""
    pos = np.maximum.accumulate(np.nan_to_num(df["track_position"].to_numpy(dtype=float)))
    t = np.maximum.accumulate(np.nan_to_num(df["t"].to_numpy(dtype=float)))
    return pos, t


def lap_sectors(df, n):
    """Split a lap into `n` equal-distance sectors by TRACK POSITION (not by
    sample count, which only matches distance when the car moves at constant
    speed). Returns (segments, times): the sample slices per sector (an
    empty slice if no sample fell in it) and each sector's time in seconds, read off the lap's
    own position->time curve so the times always add up to the lap time."""
    pos, t = _lap_xy(df)
    length = float(pos[-1]) if len(pos) else 0.0
    if len(df) < 2 or length <= 0:
        return [], []
    edges = np.linspace(0.0, length, n + 1)
    t_edges = np.interp(edges, pos, t)
    idx = np.clip(np.searchsorted(edges, pos, side="right") - 1, 0, n - 1)
    segs, times = [], []
    for k in range(n):
        segs.append(df.iloc[np.where(idx == k)[0]])
        times.append(float(t_edges[k + 1] - t_edges[k]))
    return segs, times


def compute_sector_deltas(df_a, df_b, sector_length_m=25):
    """Per-point time-delta-to-reference, bucketed into ~fixed-distance
    sectors, for coloring the race-line map as a micro-sector heatmap.
    Both laps are cut by track position (B aligned by its fraction of the lap,
    which absorbs the small length drift between recorded laps) and each
    sector's time comes from that lap's position->time curve. Returns an
    array the same length as df_a -- positive = slower than reference in that
    sector, negative = faster."""
    pa, ta = _lap_xy(df_a)
    pb, tb = _lap_xy(df_b)
    la = max(float(pa[-1]), 1.0)
    lb = max(float(pb[-1]), 1.0)
    n_sec = max(1, int(math.ceil(la / sector_length_m)))
    edges_a = np.linspace(0.0, la, n_sec + 1)
    edges_b = edges_a / la * lb
    d_a = np.diff(np.interp(edges_a, pa, ta))
    d_b = np.diff(np.interp(edges_b, pb, tb))
    sec = np.clip(np.searchsorted(edges_a, pa, side="right") - 1, 0, n_sec - 1)
    return (d_a - d_b)[sec]

def lap_label(data, short=False):
    t = _num(data.get("lap_time_s"))
    time_str = _fmt_laptime(t)
    car   = str(data.get("car") or "?")
    track = str(data.get("track") or "?").replace("_"," ").title()
    if not leaderboard.is_current_era(data):
        time_str += f" [pre-{leaderboard.PHYSICS_ERA}]"
    if short: return f"{car}  {time_str}"
    return f"{car} @ {track}  {time_str}"

def build_stats(data, df):
    t = _num(data.get("lap_time_s"))
    fuel_used = df["fuel_remaining"].iloc[0] - df["fuel_remaining"].iloc[-1]
    return {
        "Lap Time":     _fmt_laptime(t),
        "Samples":      f"{len(df)}",
        "Top Speed":    f"{df['speed_kmh'].max():.1f} km/h",
        "Avg Speed":    f"{df['speed_kmh'].mean():.1f} km/h",
        "Full Thr%":    f"{(df['throttle']>0.95).mean()*100:.1f}%",
        "Coasting%":    f"{df['coasting'].mean()*100:.1f}%",
        "Max Lat G":    f"{df['lat_g'].abs().max():.2f}g",
        "Max Long G":   f"{df['long_g'].abs().max():.2f}g",
        "Fuel Used":    f"{fuel_used:.2f}",
        "Peak RPM":     f"{df['rpm'].max():.0f}",
        "Gear Changes": f"{int((df['gear'].diff().fillna(0)!=0).sum())}",
        "Avg Tyre °C":  f"{df[['tyre_temp_fl','tyre_temp_fr','tyre_temp_rl','tyre_temp_rr']].mean().mean():.1f}",
    }

def build_sector_stats(df):
    """Thirds of the lap by track position: (name, time, avg speed, top speed)."""
    segs, times = lap_sectors(df, 3)
    out = []
    for i, (seg, t_seg) in enumerate(zip(segs, times), 1):
        if len(seg) == 0:
            continue
        out.append((f"S{i}", f"{t_seg:.2f}s", f"{seg['speed_kmh'].mean():.0f}", f"{seg['speed_kmh'].max():.0f}"))
    return out

# ── Shareable lap card (PNG) ──────────────────────────────────────────────────
# The drawing lives in share_card.py (shared with the Race Analyst's race card);
# this builds the lap's side of the spec. Only values read straight from the
# recording go on the card: GT7's lap time, top speed, fuel used, the speed
# trace and the speed-coloured track map.
def _fmt_laptime(t):
    """m:ss.mmm, rounded to the millisecond first so 59.9996 s reads 1:00.000, not 0:60.000."""
    return share_card.fmt_time(_num(t))

def _lap_fuel_used(df):
    """Fuel used over the lap as card text in the gauge's own unit, or None when
    the file has no usable fuel data. GT7 reports fuel against a capacity of 100
    for nearly every car (a percentage), 0 for EVs (battery %), and something
    else for a few cars such as karts (shown as "x of <capacity>")."""
    if "fuel_remaining" not in df.columns or len(df) < 2:
        return None
    used = float(df["fuel_remaining"].iloc[0] - df["fuel_remaining"].iloc[-1])
    if not math.isfinite(used) or used <= 0:
        return None
    cap = float(df["fuel_capacity"].max()) if "fuel_capacity" in df.columns else 100.0
    if cap == 100.0:
        return f"{used:.1f}%"
    if cap == 0.0:
        return f"{used:.1f}% battery"
    return f"{used:.2f} of {cap:g}"

def lap_card_spec(data, df):
    """Everything share_card.render_card needs for a lap card, as a plain dict."""
    lap_s = _num(data.get("lap_time_s"))
    if data.get("incomplete"):
        big = "INCOMPLETE"
    else:
        big = _fmt_laptime(lap_s) if lap_s > 0 else "--:--.---"
    when = ""
    try:
        when = datetime.strptime(str(data.get("recorded_at"))[:15], "%Y%m%d_%H%M%S").strftime("%d %b %Y")
    except (TypeError, ValueError):
        pass
    top = float(df["speed_kmh"].max()) if "speed_kmh" in df.columns and len(df) else 0.0
    spec = {
        "kind": "LAP",
        "when": when,
        "car": str(data.get("car_display") or data.get("car") or "Unknown car"),
        "track": str(data.get("track_display") or str(data.get("track") or "unknown track").replace("_", " ")),
        "era": "" if leaderboard.is_current_era(data) else f"pre-{leaderboard.PHYSICS_ERA} physics",
        "headline": {"big_label": "LAP TIME", "big_value": big,
                     "tiles": [("TOP SPEED", f"{top:.0f} km/h" if top > 0 else "--"),
                               ("FUEL USED", _lap_fuel_used(df) or "--")]},
        "chart": {"title": "speed round the lap", "kind": "trace", "ylabel": "km/h", "x": [], "y": []},
        "map": None,
    }

    # speed trace against distance round the lap (sample index when there is no distance)
    has_pos = "track_position" in df.columns and float(df["track_position"].max() - df["track_position"].min()) > 1
    xs = df["track_position"].to_numpy(dtype=float) if has_pos else np.arange(len(df), dtype=float)
    order = np.argsort(xs, kind="stable")
    spec["chart"]["x"] = xs[order].tolist()
    spec["chart"]["y"] = df["speed_kmh"].to_numpy(dtype=float)[order].tolist()

    # a downloaded ghost has no world coordinates, so it gets no map
    if (not is_compact(df) and "world_x" in df.columns and "world_z" in df.columns and len(df) > 2
            and float(df["world_x"].std()) > 1 and float(df["world_z"].std()) > 1):
        spec["map"] = {"x": df["world_x"].tolist(), "z": df["world_z"].tolist(), "v": df["speed_kmh"].tolist()}
    return spec

def lap_card_png(data, df, width=1200, height=630):
    """Render a shareable summary card for a lap and return it as PNG bytes:
    lap time, top speed, fuel used, the speed trace and a speed-coloured track
    map. See share_card.py."""
    return share_card.render_card(lap_card_spec(data, df), width, height)
def theory_candidates(rows, lap_data, path_a, limit):
    """The saved laps worth stitching into the theoretical best: same car and
    track as Lap A, complete, from the same physics era, fastest first, and not
    Lap A's own file (it is added separately). `rows` come from records.scan_laps."""
    car = str(lap_data.get("car_display") or lap_data.get("car") or "").casefold()
    track = str(lap_data.get("track_display") or lap_data.get("track") or "").casefold()
    current = leaderboard.is_current_era(lap_data)
    keep = [r for r in rows
            if r["car"].casefold() == car and r["track"].casefold() == track
            and not r["incomplete"] and r["current_era"] == current
            and os.path.normcase(r["path"]) != os.path.normcase(str(path_a or ""))]
    return sorted(keep, key=lambda r: r["time_s"])[:limit]

# ── Chart helpers ─────────────────────────────────────────────────────────────
def _ax(ax, title, xl="Track Pos (m)", yl=""):
    ax.set_title(title, color=FG, fontsize=9)
    if xl: ax.set_xlabel(xl, fontsize=7)
    if yl: ax.set_ylabel(yl, fontsize=7)
    ax.grid(True, alpha=0.4)

def _L(ax, df, x, y, col, lbl=None, lw=1.5, fill=False, step=False):
    if step:
        ax.step(df[x], df[y], color=col, lw=lw, label=lbl, where="post")
    else:
        ax.plot(df[x], df[y], color=col, lw=lw, label=lbl)
    if fill:
        ax.fill_between(df[x], df[y], alpha=0.12, color=col, step="post" if step else None)

def _Lb(ax, df, x, y, lw=1.2, alpha=0.75):
    """Overlay lap B — dashed ACC line."""
    if df is None or x not in df.columns or y not in df.columns: return
    ax.plot(df[x], df[y], color=ACC, lw=lw, alpha=alpha, ls="--")

def _Lb_step(ax, df, x, y, lw=1.2, alpha=0.75):
    if df is None or x not in df.columns or y not in df.columns: return
    ax.step(df[x], df[y], color=ACC, lw=lw, alpha=alpha, ls="--", where="post")

def _track_map(ax, df, col, cmap="turbo", title=""):
    x, z = df["world_x"].values, df["world_z"].values
    v = df[col].values
    pts  = np.array([x, z]).T.reshape(-1,1,2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    norm = plt.Normalize(v.min(), v.max())
    lc   = LineCollection(segs, cmap=cmap, norm=norm, lw=2.2, alpha=0.9)
    lc.set_array(v)
    ax.add_collection(lc); ax.autoscale(); ax.set_aspect("equal")
    ax.set_title(title, color=FG, fontsize=9); ax.axis("off")
    cb = plt.colorbar(lc, ax=ax, pad=0.02, fraction=0.04)
    cb.ax.tick_params(labelsize=6, colors=DIM)

# ── Chart groups ──────────────────────────────────────────────────────────────
def draw_inputs(fig, df, dfb=None, extra=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = "track_position"
    _L(axs[0,0], df, x, "speed_kmh",  C["speed"],    fill=True); _ax(axs[0,0], "Speed",    yl="km/h")
    _L(axs[0,1], df, x, "throttle",   C["throttle"], fill=True); _ax(axs[0,1], "Throttle", yl="0–1")
    _L(axs[0,2], df, x, "brake",      C["brake"],    fill=True); _ax(axs[0,2], "Brake",    yl="0–1")
    axs[1,0].plot(df[x], df["throttle"]*100, color=C["throttle"], lw=1.5, label="Thr A")
    axs[1,0].plot(df[x], df["brake"]*100,    color=C["brake"],    lw=1.5, label="Brk A")
    _ax(axs[1,0], "Throttle + Brake", yl="%"); axs[1,0].legend(fontsize=7)
    _L(axs[1,1], df, x, "gear",     C["gear"],    step=True, fill=True); _ax(axs[1,1], "Gear",    yl="Gear")
    _L(axs[1,2], df, x, "steering", C["steering"]);                       _ax(axs[1,2], "Steering")
    axs[1,2].axhline(0, color=DIM, lw=0.8, ls="--")
    _L(axs[2,0], df, x, "clutch",   C["clutch"],  fill=True); _ax(axs[2,0], "Clutch", yl="0–1")
    axs[2,1].hist(df["speed_kmh"], bins=40, color=C["speed"], alpha=0.85, edgecolor="none", label="A")
    _ax(axs[2,1], "Speed Distribution", xl="km/h", yl="Count"); axs[2,1].grid(False)
    axs[2,2].scatter(df["throttle"]*100, df["speed_kmh"], c=C["throttle"], s=1, alpha=0.35)
    _ax(axs[2,2], "Speed vs Throttle", xl="Throttle %", yl="km/h"); axs[2,2].grid(False)
    if dfb is not None:
        _Lb(axs[0,0], dfb, x, "speed_kmh"); _Lb(axs[0,1], dfb, x, "throttle")
        _Lb(axs[0,2], dfb, x, "brake")
        axs[1,0].plot(dfb[x], dfb["throttle"]*100, color=C["throttle"], lw=1.2, alpha=0.65, ls="--", label="Thr B")
        axs[1,0].plot(dfb[x], dfb["brake"]*100,    color=C["brake"],    lw=1.2, alpha=0.65, ls="--", label="Brk B")
        axs[1,0].legend(fontsize=6)
        _Lb_step(axs[1,1], dfb, x, "gear"); _Lb(axs[1,2], dfb, x, "steering")
        _Lb(axs[2,0], dfb, x, "clutch")
        axs[2,1].hist(dfb["speed_kmh"], bins=40, color=ACC, alpha=0.55, edgecolor="none",
                      label="B", histtype="step", lw=1.5)
        axs[2,1].legend(fontsize=7)
        axs[2,2].scatter(dfb["throttle"]*100, dfb["speed_kmh"], c=ACC, s=1, alpha=0.22)
    if extra:
        # 3rd-and-beyond overlay laps -- one dotted line per lap, speed/
        # throttle/brake/steering only (the other panels stay A/B-focused
        # so this doesn't turn into visual soup past a couple of extras).
        for elbl, edf, ecol in extra:
            if edf is None or "speed_kmh" not in edf.columns: continue
            axs[0,0].plot(edf[x], edf["speed_kmh"], color=ecol, lw=1.1, alpha=0.75, ls=":", label=elbl)
            axs[0,1].plot(edf[x], edf["throttle"],  color=ecol, lw=1.1, alpha=0.75, ls=":")
            axs[0,2].plot(edf[x], edf["brake"],     color=ecol, lw=1.1, alpha=0.75, ls=":")
            axs[1,2].plot(edf[x], edf["steering"],  color=ecol, lw=1.1, alpha=0.75, ls=":")
        axs[0,0].legend(fontsize=6)

def draw_engine(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = "track_position"
    _L(axs[0,0], df, x, "rpm", C["rpm"], fill=True); _ax(axs[0,0], "RPM", yl="RPM")
    ax2 = axs[0,1].twinx()
    axs[0,1].plot(df[x], df["gear"], color=C["gear"], lw=1.5, label="Gear")
    ax2.plot(df[x], df["rpm"],       color=C["rpm"],  lw=1,   alpha=0.7)
    axs[0,1].set_ylabel("Gear", color=C["gear"], fontsize=7)
    ax2.set_ylabel("RPM", color=C["rpm"], fontsize=7)
    ax2.tick_params(colors=DIM, labelsize=7); _ax(axs[0,1], "RPM + Gear", yl="")
    _L(axs[0,2], df, x, "boost",            ORG, fill=True); _ax(axs[0,2], "Boost",         yl="bar")
    _L(axs[1,0], df, x, "oil_temp",         ACC);            _ax(axs[1,0], "Oil Temp (GT7 constant)", yl="°C")
    _L(axs[1,1], df, x, "water_temp",       CYN);            _ax(axs[1,1], "Water Temp (GT7 constant)", yl="°C")
    _L(axs[1,2], df, x, "oil_pressure",     GRN);            _ax(axs[1,2], "Oil Pressure",   yl="bar")
    _L(axs[2,0], df, x, "rpm_after_clutch", PRP);            _ax(axs[2,0], "RPM @ Clutch")
    axs[2,1].hist(df["rpm"], bins=40, color=C["rpm"], alpha=0.85, edgecolor="none", label="A")
    _ax(axs[2,1], "RPM Distribution", xl="RPM", yl="Count"); axs[2,1].grid(False)
    axs[2,2].hist(df["gear"].clip(1,8), bins=range(1,10), color=C["gear"],
                  alpha=0.85, edgecolor="none", rwidth=0.7, label="A")
    _ax(axs[2,2], "Gear Usage", xl="Gear", yl="Count"); axs[2,2].grid(False)
    if dfb is not None:
        _Lb(axs[0,0], dfb, x, "rpm")
        axs[0,1].plot(dfb[x], dfb["gear"], color=ACC, lw=1.2, alpha=0.65, ls="--")
        ax2.plot(dfb[x], dfb["rpm"], color=ORG, lw=1, alpha=0.55, ls="--")
        _Lb(axs[0,2], dfb, x, "boost");            _Lb(axs[1,0], dfb, x, "oil_temp")
        _Lb(axs[1,1], dfb, x, "water_temp");       _Lb(axs[1,2], dfb, x, "oil_pressure")
        _Lb(axs[2,0], dfb, x, "rpm_after_clutch")
        axs[2,1].hist(dfb["rpm"], bins=40, color=ACC, alpha=0.5, edgecolor="none",
                      label="B", histtype="step", lw=1.5)
        axs[2,1].legend(fontsize=7)
        axs[2,2].hist(dfb["gear"].clip(1,8), bins=range(1,10), color=ACC,
                      alpha=0.55, rwidth=0.35, label="B")
        axs[2,2].legend(fontsize=7)

def draw_tyres(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = "track_position"
    for col,lbl,c in [("tyre_temp_fl","FL",C["fl"]),("tyre_temp_fr","FR",C["fr"]),
                       ("tyre_temp_rl","RL",C["rl"]),("tyre_temp_rr","RR",C["rr"])]:
        axs[0,0].plot(df[x], df[col], color=c, lw=1.4, label=lbl)
    _ax(axs[0,0], "All Tyre Temps", yl="°C"); axs[0,0].legend(fontsize=7)
    axs[0,0].axhspan(80, 100, alpha=0.07, color=GRN)
    axs[0,1].plot(df[x], df["tyre_temp_fl"], color=C["fl"], lw=1.5, label="FL")
    axs[0,1].plot(df[x], df["tyre_temp_fr"], color=C["fr"], lw=1.5, label="FR")
    _ax(axs[0,1], "Front Temps °C"); axs[0,1].legend(fontsize=7)
    axs[0,1].axhspan(80, 100, alpha=0.07, color=GRN)
    axs[0,2].plot(df[x], df["tyre_temp_rl"], color=C["rl"], lw=1.5, label="RL")
    axs[0,2].plot(df[x], df["tyre_temp_rr"], color=C["rr"], lw=1.5, label="RR")
    _ax(axs[0,2], "Rear Temps °C"); axs[0,2].legend(fontsize=7)
    axs[0,2].axhspan(80, 100, alpha=0.07, color=GRN)
    axs[1,0].plot(df[x], df["front_t_avg"], color=C["fl"], lw=1.5, label="Front avg")
    axs[1,0].plot(df[x], df["rear_t_avg"],  color=C["rl"], lw=1.5, label="Rear avg")
    _ax(axs[1,0], "Front vs Rear Avg °C"); axs[1,0].legend(fontsize=7)
    for col,lbl,c in [("tyre_slip_fl","FL",C["fl"]),("tyre_slip_fr","FR",C["fr"]),
                       ("tyre_slip_rl","RL",C["rl"]),("tyre_slip_rr","RR",C["rr"])]:
        axs[1,1].plot(df[x], df[col], color=c, lw=1.2, label=lbl)
    axs[1,1].axhline(1.0, color=DIM, lw=0.8, ls="--")
    _ax(axs[1,1], "All Slip Ratios", yl="Slip"); axs[1,1].legend(fontsize=7)
    axs[1,2].plot(df[x], df["tyre_slip_fl"], color=C["fl"], lw=1.5, label="FL")
    axs[1,2].plot(df[x], df["tyre_slip_fr"], color=C["fr"], lw=1.5, label="FR")
    axs[1,2].axhline(1.0, color=DIM, lw=0.8, ls="--")
    _ax(axs[1,2], "Front Slip"); axs[1,2].legend(fontsize=7)
    axs[2,0].plot(df[x], df["tyre_slip_rl"], color=C["rl"], lw=1.5, label="RL")
    axs[2,0].plot(df[x], df["tyre_slip_rr"], color=C["rr"], lw=1.5, label="RR")
    axs[2,0].axhline(1.0, color=DIM, lw=0.8, ls="--")
    _ax(axs[2,0], "Rear Slip"); axs[2,0].legend(fontsize=7)
    _L(axs[2,1], df, x, "lr_t_bal", YLW); axs[2,1].axhline(0, color=DIM, lw=0.8, ls="--")
    _ax(axs[2,1], "L−R Balance °C", yl="Left − Right")
    axs[2,2].scatter(df["speed_kmh"], df["tyre_slip_rr"], s=1.5, alpha=0.3, color=C["rr"])
    axs[2,2].axhline(1.0, color=DIM, lw=0.8, ls="--")
    _ax(axs[2,2], "Rear-R Slip vs Speed", xl="km/h", yl="Slip"); axs[2,2].grid(False)
    if dfb is not None:
        for col in ["tyre_temp_fl","tyre_temp_fr","tyre_temp_rl","tyre_temp_rr"]:
            axs[0,0].plot(dfb[x], dfb[col], lw=1.0, alpha=0.45, ls="--", color=DIM)
        _Lb(axs[0,1], dfb, x, "tyre_temp_fl"); _Lb(axs[0,1], dfb, x, "tyre_temp_fr")
        _Lb(axs[0,2], dfb, x, "tyre_temp_rl"); _Lb(axs[0,2], dfb, x, "tyre_temp_rr")
        _Lb(axs[1,0], dfb, x, "front_t_avg"); _Lb(axs[1,0], dfb, x, "rear_t_avg")
        for col in ["tyre_slip_fl","tyre_slip_fr","tyre_slip_rl","tyre_slip_rr"]:
            axs[1,1].plot(dfb[x], dfb[col], lw=1.0, alpha=0.35, ls="--", color=DIM)
        _Lb(axs[1,2], dfb, x, "tyre_slip_fl"); _Lb(axs[1,2], dfb, x, "tyre_slip_fr")
        _Lb(axs[2,0], dfb, x, "tyre_slip_rl"); _Lb(axs[2,0], dfb, x, "tyre_slip_rr")
        _Lb(axs[2,1], dfb, x, "lr_t_bal")
        axs[2,2].scatter(dfb["speed_kmh"], dfb["tyre_slip_rr"], s=1.5, alpha=0.2, color=ACC)

def draw_dynamics(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = "track_position"
    for col,lbl,c in [("susp_fl","FL",C["fl"]),("susp_fr","FR",C["fr"]),
                       ("susp_rl","RL",C["rl"]),("susp_rr","RR",C["rr"])]:
        axs[0,0].plot(df[x], df[col], color=c, lw=1.2, label=lbl)
    _ax(axs[0,0], "All Suspension mm"); axs[0,0].legend(fontsize=7)
    axs[0,1].plot(df[x], df["susp_fl"], color=C["fl"], lw=1.5, label="FL")
    axs[0,1].plot(df[x], df["susp_fr"], color=C["fr"], lw=1.5, label="FR")
    _ax(axs[0,1], "Front Suspension mm"); axs[0,1].legend(fontsize=7)
    axs[0,2].plot(df[x], df["susp_rl"], color=C["rl"], lw=1.5, label="RL")
    axs[0,2].plot(df[x], df["susp_rr"], color=C["rr"], lw=1.5, label="RR")
    _ax(axs[0,2], "Rear Suspension mm"); axs[0,2].legend(fontsize=7)
    _L(axs[1,0], df, x, "ride_height_mm", ORG);  _ax(axs[1,0], "Ride Height mm")
    _L(axs[1,1], df, x, "ang_y",  PRP);  axs[1,1].axhline(0,color=DIM,lw=0.8,ls="--"); _ax(axs[1,1], "Yaw Rate (ang_y)", yl="rad/s")
    _L(axs[1,2], df, x, "vel_x",  CYN);  axs[1,2].axhline(0,color=DIM,lw=0.8,ls="--"); _ax(axs[1,2], "Lateral Vel X",   yl="m/s")
    _L(axs[2,0], df, x, "vel_y",  GRN);  axs[2,0].axhline(0,color=DIM,lw=0.8,ls="--"); _ax(axs[2,0], "Vertical Vel Y",  yl="m/s")
    _L(axs[2,1], df, x, "long_g", YLW);  axs[2,1].axhline(0,color=DIM,lw=0.8,ls="--"); _ax(axs[2,1], "Long G",          yl="g")
    axs[2,1].fill_between(df[x], df["long_g"], 0, where=df["long_g"]>0, alpha=0.12, color=GRN)
    axs[2,1].fill_between(df[x], df["long_g"], 0, where=df["long_g"]<0, alpha=0.12, color=ACC)
    _L(axs[2,2], df, x, "lat_g",  PRP);  axs[2,2].axhline(0,color=DIM,lw=0.8,ls="--"); _ax(axs[2,2], "Lat G", yl="g")
    if dfb is not None:
        for col in ["susp_fl","susp_fr","susp_rl","susp_rr"]:
            axs[0,0].plot(dfb[x], dfb[col], lw=1.0, alpha=0.4, ls="--", color=DIM)
        _Lb(axs[0,1], dfb, x, "susp_fl"); _Lb(axs[0,1], dfb, x, "susp_fr")
        _Lb(axs[0,2], dfb, x, "susp_rl"); _Lb(axs[0,2], dfb, x, "susp_rr")
        _Lb(axs[1,0], dfb, x, "ride_height_mm")
        _Lb(axs[1,1], dfb, x, "ang_y"); _Lb(axs[1,2], dfb, x, "vel_x")
        _Lb(axs[2,0], dfb, x, "vel_y"); _Lb(axs[2,1], dfb, x, "long_g")
        _Lb(axs[2,2], dfb, x, "lat_g")

def draw_maps(fig, df, dfb=None):
    # dfb not overlaid here — see Heat Maps tab for multi-metric views
    axs = fig.subplots(2, 2); fig.subplots_adjust(hspace=0.3, wspace=0.25)
    has = df["world_x"].abs().max() > 1
    if not has:
        for ax in axs.flat:
            ax.text(0.5,0.5,"No GPS data in this lap",ha="center",va="center",color=DIM,fontsize=11)
            ax.axis("off")
        return
    _track_map(axs[0,0], df, "speed_kmh",     "turbo",  "Speed (km/h)")
    _track_map(axs[0,1], df, "throttle",       "Greens", "Throttle")
    _track_map(axs[1,0], df, "brake",          "Reds",   "Brake")
    gf = df.copy(); gf["gear_f"] = df["gear"].astype(float)
    _track_map(axs[1,1], gf, "gear_f",         "plasma", "Gear")

def draw_gforce(fig, df, dfb=None):
    axs = fig.subplots(2, 2); fig.subplots_adjust(hspace=0.42, wspace=0.35)
    x = "track_position"
    _L(axs[0,0], df, x, "long_g", YLW); axs[0,0].axhline(0,color=DIM,lw=0.8,ls="--")
    axs[0,0].fill_between(df[x],df["long_g"],0,where=df["long_g"]>0,alpha=0.15,color=GRN)
    axs[0,0].fill_between(df[x],df["long_g"],0,where=df["long_g"]<0,alpha=0.15,color=ACC)
    _ax(axs[0,0], "Longitudinal G", yl="g")
    _L(axs[0,1], df, x, "lat_g", PRP); axs[0,1].axhline(0,color=DIM,lw=0.8,ls="--")
    _ax(axs[0,1], "Lateral G", yl="g")
    sc = axs[1,0].scatter(df["lat_g"], df["long_g"], c=df["speed_kmh"],
                           cmap="turbo", s=2, alpha=0.4)
    axs[1,0].axhline(0,color=DIM,lw=0.5); axs[1,0].axvline(0,color=DIM,lw=0.5)
    th = np.linspace(0, 2*np.pi, 100)
    for r in [1.0, 2.0]: axs[1,0].plot(np.cos(th)*r, np.sin(th)*r, color=DIM, lw=0.6, ls="--")
    _ax(axs[1,0], "G-G Diagram", xl="Lat G", yl="Long G"); axs[1,0].grid(False)
    plt.colorbar(sc, ax=axs[1,0], fraction=0.04, pad=0.02).ax.tick_params(labelsize=6, colors=DIM)
    _L(axs[1,1], df, x, "total_g", CYN, fill=True); _ax(axs[1,1], "Total G", yl="g")
    if dfb is not None:
        _Lb(axs[0,0], dfb, x, "long_g"); _Lb(axs[0,1], dfb, x, "lat_g")
        axs[1,0].scatter(dfb["lat_g"], dfb["long_g"], c=ACC, s=1.5, alpha=0.22)
        _Lb(axs[1,1], dfb, x, "total_g")

def draw_fuel(fig, df, dfb=None):
    axs = fig.subplots(2, 3); fig.subplots_adjust(hspace=0.52, wspace=0.4)
    x = "track_position"
    _L(axs[0,0], df, x, "fuel_remaining", GRN, fill=True); _ax(axs[0,0], "Fuel Remaining",  yl="% (GT7)")
    _L(axs[0,1], df, x, "fuel_burn",      ORG);            _ax(axs[0,1], "Fuel Burn Rate",   yl="% per s (GT7 gauge)")
    axs[0,2].fill_between(df[x], df["coasting"], alpha=0.6, color=DIM2, step="post")
    _ax(axs[0,2], "Coasting Zones", yl="1=coasting")
    axs[1,0].hist(df["throttle"]*100, bins=30, color=C["throttle"], alpha=0.85, edgecolor="none", label="A")
    _ax(axs[1,0], "Throttle Distribution", xl="%", yl="Count"); axs[1,0].grid(False)
    axs[1,1].hist(df["brake"]*100, bins=30, color=C["brake"], alpha=0.85, edgecolor="none", label="A")
    _ax(axs[1,1], "Brake Distribution", xl="%", yl="Count"); axs[1,1].grid(False)
    axs[1,2].scatter(df["brake"]*100, df["speed_kmh"], s=1.5, alpha=0.3, color=C["brake"])
    _ax(axs[1,2], "Speed vs Brake", xl="Brake %", yl="km/h"); axs[1,2].grid(False)
    if dfb is not None:
        _Lb(axs[0,0], dfb, x, "fuel_remaining"); _Lb(axs[0,1], dfb, x, "fuel_burn")
        axs[0,2].fill_between(dfb[x], dfb["coasting"], alpha=0.3, color=ACC, step="post")
        axs[1,0].hist(dfb["throttle"]*100, bins=30, color=ACC, alpha=0.5, edgecolor="none",
                      label="B", histtype="step", lw=1.5)
        axs[1,0].legend(fontsize=7)
        axs[1,1].hist(dfb["brake"]*100, bins=30, color=ACC, alpha=0.5, edgecolor="none",
                      label="B", histtype="step", lw=1.5)
        axs[1,1].legend(fontsize=7)
        axs[1,2].scatter(dfb["brake"]*100, dfb["speed_kmh"], s=1.5, alpha=0.2, color=ACC)

def _brake_zone_durations(df):
    """Duration (s) of each braking zone (brake > 0.1). A zone still open at
    the end of the lap is closed at the last sample instead of being dropped."""
    b = (df["brake"].to_numpy(dtype=float) > 0.1).astype(int)
    if not len(b):
        return []
    t = df["t"].to_numpy(dtype=float)
    edges = np.diff(b, prepend=0, append=0)
    starts = np.where(edges == 1)[0]
    ends = np.minimum(np.where(edges == -1)[0], len(b) - 1)
    return [float(t[e] - t[s]) for s, e in zip(starts, ends)]

def draw_braking(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = "track_position"
    braking = df[df["brake"] > 0.05]

    _L(axs[0,0], df, x, "brake", C["brake"], fill=True); _ax(axs[0,0], "Brake Trace", yl="0–1")

    axs[0,1].hist(braking["brake"]*100 if len(braking) else [0],
                  bins=30, color=C["brake"], alpha=0.85, edgecolor="none", label="A")
    _ax(axs[0,1], "Brake Pressure Dist", xl="Brake %", yl="Count"); axs[0,1].grid(False)

    axs[0,2].plot(df[x], df["throttle"]*100, color=GRN, lw=1.2, alpha=0.8, label="Thr")
    axs[0,2].plot(df[x], df["brake"]*100,    color=ACC, lw=1.5,             label="Brk")
    overlap = (df["throttle"] > 0.05) & (df["brake"] > 0.05)
    if overlap.any():
        axs[0,2].fill_between(df[x], df["throttle"]*100, df["brake"]*100,
                               where=overlap, alpha=0.3, color=YLW, label="Overlap")
    _ax(axs[0,2], "Trail Braking Overlap", yl="%"); axs[0,2].legend(fontsize=7)

    has = df["world_x"].abs().max() > 1
    if has:
        _track_map(axs[1,0], df, "brake", "Reds", "Brake Intensity (Map)")
    else:
        axs[1,0].text(0.5,0.5,"No GPS data",ha="center",va="center",color=DIM,fontsize=10)
        axs[1,0].axis("off")

    _L(axs[1,1], df, x, "long_g", YLW); axs[1,1].axhline(0, color=DIM, lw=0.8, ls="--")
    axs[1,1].fill_between(df[x], df["long_g"], 0, where=df["long_g"]<0, alpha=0.2, color=ACC)
    _ax(axs[1,1], "Long G (Deceleration)", yl="g")

    brake_starts = df[(df["brake"].diff() > 0.1) & (df["brake"] > 0.1)]
    axs[1,2].plot(df[x], df["speed_kmh"], color=CYN, lw=0.8, alpha=0.3)
    if len(brake_starts):
        axs[1,2].scatter(brake_starts[x], brake_starts["speed_kmh"], c=ACC, s=18, alpha=0.75, zorder=3)
    _ax(axs[1,2], "Speed at Brake Points", yl="km/h")

    zones = _brake_zone_durations(df)
    if zones:
        axs[2,0].hist(zones, bins=min(25, len(zones)), color=ACC, alpha=0.85, edgecolor="none")
    _ax(axs[2,0], "Brake Zone Duration", xl="s", yl="Count"); axs[2,0].grid(False)

    dt2 = df["t"].diff().replace(0, 0.01).fillna(0.01)
    release_rate = -(df["brake"].diff() / dt2)
    release_rate = release_rate[release_rate > 1.0]
    if len(release_rate):
        axs[2,1].scatter(df.loc[release_rate.index, x], release_rate, s=4, color=ORG, alpha=0.6)
    _ax(axs[2,1], "Brake Release Rate", xl="Track Pos", yl="Δbrake/s")

    if df["tyre_slip_rl"].abs().max() > 0.01 and len(braking) > 10:
        avg_rear = (braking["tyre_slip_rl"] + braking["tyre_slip_rr"]) / 2
        axs[2,2].scatter(braking["brake"]*100, avg_rear, s=2, alpha=0.3, color=PRP)
        axs[2,2].axhline(1.0, color=DIM, lw=0.8, ls="--")
        _ax(axs[2,2], "Rear Slip vs Brake %", xl="Brake %", yl="Slip"); axs[2,2].grid(False)
    else:
        axs[2,2].text(0.5,0.5,"No slip data",ha="center",va="center",color=DIM)
        axs[2,2].axis("off")

    if dfb is not None:
        _Lb(axs[0,0], dfb, x, "brake")
        _Lb(axs[1,1], dfb, x, "long_g")
        axs[1,2].plot(dfb[x], dfb["speed_kmh"], color=ACC, lw=0.7, alpha=0.22)
        brk_b = dfb[dfb["brake"] > 0.05]
        if len(brk_b):
            axs[0,1].hist(brk_b["brake"]*100, bins=30, color=ACC, alpha=0.5,
                          edgecolor="none", label="B", histtype="step", lw=1.5)
            axs[0,1].legend(fontsize=7)

# ── NEW: Sectors ───────────────────────────────────────────────────────────────
def draw_sectors(fig, df, dfb=None):
    N = 10

    def sector_stats(d):
        segs, times = lap_sectors(d, N)
        def agg(col, fn, scale=1.0):
            return [float(fn(s[col])) * scale if len(s) else float("nan") for s in segs]
        return (segs, times, agg("speed_kmh", np.mean), agg("speed_kmh", np.max),
                agg("brake", np.mean, 100), agg("throttle", np.mean, 100),
                [float(s["lat_g"].abs().max()) if len(s) else float("nan") for s in segs])

    segs_a, t_a, avg_a, top_a, brk_a, thr_a, latg_a = sector_stats(df)
    na = len(t_a)
    if sum(1 for s in segs_a if len(s)) < 2:
        ax = fig.subplots(1, 1)
        ax.text(0.5, 0.5, "This lap has too few samples to split into sectors",
                ha="center", va="center", color=DIM, fontsize=11)
        ax.axis("off")
        return

    t_b = avg_b = top_b = brk_b = thr_b = latg_b = segs_b = None
    if dfb is not None:
        segs_b, t_b, avg_b, top_b, brk_b, thr_b, latg_b = sector_stats(dfb)
        if sum(1 for s in segs_b if len(s)) < 2:
            t_b = avg_b = top_b = brk_b = thr_b = latg_b = segs_b = None

    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.42)

    def bars2(ax, vals_a, vals_b, title, yl="", col_a=CYN, col_b=ACC):
        nb_ = min(len(vals_a), len(vals_b)) if vals_b else len(vals_a)
        xs = list(range(1, nb_+1))
        bw = 0.37 if vals_b else 0.6
        offs_a = -bw/2 if vals_b else 0
        ax.bar([v + offs_a for v in xs], vals_a[:nb_], width=bw, color=col_a, alpha=0.88, label="A")
        if vals_b:
            ax.bar([v + bw/2 for v in xs], vals_b[:nb_], width=bw, color=col_b, alpha=0.78, label="B")
            ax.legend(fontsize=7)
        _ax(ax, title, xl="Sector", yl=yl); ax.set_xticks(xs)

    bars2(axs[0,0], t_a,    t_b,    "Sector Times",      yl="s")
    bars2(axs[0,1], avg_a,  avg_b,  "Avg Speed/Sector",  yl="km/h", col_a=CYN)
    bars2(axs[0,2], top_a,  top_b,  "Top Speed/Sector",  yl="km/h", col_a=GRN)

    if t_b is not None:
        nb_ = min(len(t_a), len(t_b))
        delta = [tb - ta for ta, tb in zip(t_a[:nb_], t_b[:nb_])]
        cols_d = [GRN if d < 0 else ACC for d in delta]
        xs = list(range(1, nb_+1))
        axs[1,0].bar(xs, delta, color=cols_d, alpha=0.88, width=0.65)
        axs[1,0].axhline(0, color=DIM, lw=0.8, ls="--")
        theo = sum(min(a, b) for a, b in zip(t_a[:nb_], t_b[:nb_]))
        axs[1,0].set_title(f"Sector Δ B−A  |  Theo best: {theo:.3f}s", color=FG, fontsize=8)
        axs[1,0].set_xlabel("Sector", fontsize=7, color=DIM)
        axs[1,0].set_ylabel("Δs", fontsize=7, color=DIM)
        axs[1,0].grid(True, alpha=0.4); axs[1,0].set_xticks(xs)
    else:
        cum = list(np.cumsum(t_a))
        snums = list(range(1, na+1))
        axs[1,0].plot(snums, cum, color=CYN, marker="o", ms=5, lw=1.5)
        _ax(axs[1,0], "Cumulative Time", xl="Sector", yl="s"); axs[1,0].set_xticks(snums)

    heat = np.array(avg_a[:na]).reshape(1, -1)
    im = axs[1,1].imshow(heat, aspect="auto", cmap="turbo", extent=[0.5, na+0.5, 0, 1])
    for i, v in enumerate(avg_a):
        axs[1,1].text(i+1, 0.5, f"S{i+1}\n{v:.0f}", ha="center", va="center",
                      color="white", fontsize=6, fontweight="bold")
    axs[1,1].set_yticks([]); axs[1,1].set_xticks([])
    axs[1,1].set_title("Speed Heatmap by Sector", color=FG, fontsize=9)
    plt.colorbar(im, ax=axs[1,1], fraction=0.04).ax.tick_params(labelsize=5, colors=DIM)

    bars2(axs[1,2], brk_a, brk_b, "Avg Brake%/Sector",   yl="%", col_a=ACC, col_b=PRP)

    has = df["world_x"].abs().max() > 1
    if has:
        seg_colors = [plt.cm.tab10(i / max(na-1, 1)) for i in range(na)]
        for i, seg in enumerate(segs_a):
            xi = seg["world_x"].values; zi = seg["world_z"].values
            axs[2,0].plot(xi, zi, color=seg_colors[i], lw=2.2, alpha=0.9)
            mid = len(xi) // 2
            if mid < len(xi):
                axs[2,0].text(xi[mid], zi[mid], str(i+1), fontsize=6, color="white",
                              ha="center", va="center",
                              bbox=dict(boxstyle="round,pad=0.15", fc=seg_colors[i], alpha=0.85))
        axs[2,0].set_aspect("equal"); axs[2,0].axis("off")
        axs[2,0].set_title("Track Sector Map", color=FG, fontsize=9)
    else:
        axs[2,0].text(0.5,0.5,"No GPS data",ha="center",va="center",color=DIM,fontsize=10)
        axs[2,0].axis("off")

    bars2(axs[2,1], thr_a,  thr_b,  "Avg Throttle%/Sector", yl="%", col_a=GRN, col_b=YLW)
    bars2(axs[2,2], latg_a, latg_b, "Max |Lat G|/Sector",   yl="g", col_a=PRP, col_b=ORG)

# ── NEW: Traction Circle ───────────────────────────────────────────────────────
def draw_traction(fig, df, dfb=None):
    axs = fig.subplots(2, 3); fig.subplots_adjust(hspace=0.5, wspace=0.42)
    x = "track_position"
    th = np.linspace(0, 2*np.pi, 200)

    # G-G traction circle
    sc = axs[0,0].scatter(df["lat_g"], df["long_g"], c=df["speed_kmh"],
                           cmap="turbo", s=2, alpha=0.42)
    if dfb is not None:
        axs[0,0].scatter(dfb["lat_g"], dfb["long_g"], c=ACC, s=1.5, alpha=0.2, label="B")
        axs[0,0].legend(fontsize=7, markerscale=3)
    axs[0,0].axhline(0,color=DIM,lw=0.5); axs[0,0].axvline(0,color=DIM,lw=0.5)
    for r in [1.0, 2.0, 3.0]:
        axs[0,0].plot(np.cos(th)*r, np.sin(th)*r, color=DIM, lw=0.5, ls="--", alpha=0.7)
    axs[0,0].set_xlim(-3.5, 3.5); axs[0,0].set_ylim(-3.5, 3.5)
    _ax(axs[0,0], "G-G Traction Circle", xl="Lat G", yl="Long G"); axs[0,0].grid(False)
    plt.colorbar(sc, ax=axs[0,0], fraction=0.04, pad=0.02).ax.tick_params(labelsize=6, colors=DIM)

    # Lateral G trace
    _L(axs[0,1], df, x, "lat_g", PRP); axs[0,1].axhline(0,color=DIM,lw=0.8,ls="--")
    if dfb is not None: _Lb(axs[0,1], dfb, x, "lat_g")
    _ax(axs[0,1], "Lateral G", yl="g")

    # Total G trace
    _L(axs[0,2], df, x, "total_g", CYN, fill=True)
    if dfb is not None: _Lb(axs[0,2], dfb, x, "total_g")
    _ax(axs[0,2], "Total G", yl="g")

    # Speed vs |Lat G| coloured by throttle
    sc2 = axs[1,0].scatter(df["lat_g"].abs(), df["speed_kmh"],
                            c=df["throttle"], cmap="RdYlGn", s=2, alpha=0.35, vmin=0, vmax=1)
    if dfb is not None:
        axs[1,0].scatter(dfb["lat_g"].abs(), dfb["speed_kmh"], c=ACC, s=1.5, alpha=0.18)
    _ax(axs[1,0], "|Lat G| vs Speed", xl="|Lat G|", yl="km/h"); axs[1,0].grid(False)
    plt.colorbar(sc2, ax=axs[1,0], fraction=0.04, pad=0.02).ax.tick_params(labelsize=6, colors=DIM)

    # Entry / mid / exit |Lat G| distribution
    n = len(df); e = n // 3
    axs[1,1].hist(df.iloc[:e]["lat_g"].abs(),    bins=25, color=GRN, alpha=0.65, label="Entry", density=True)
    axs[1,1].hist(df.iloc[e:2*e]["lat_g"].abs(), bins=25, color=YLW, alpha=0.65, label="Mid",   density=True)
    axs[1,1].hist(df.iloc[2*e:]["lat_g"].abs(),  bins=25, color=ACC, alpha=0.65, label="Exit",  density=True)
    _ax(axs[1,1], "|Lat G| Entry/Mid/Exit", xl="|Lat G|", yl="Density")
    axs[1,1].legend(fontsize=7); axs[1,1].grid(False)

    # Long G distribution — accel vs brake
    pos_g = df["long_g"][df["long_g"] > 0]
    neg_g = df["long_g"][df["long_g"] < 0].abs()
    axs[1,2].hist(pos_g, bins=25, color=GRN, alpha=0.75, label="Accel A", density=True)
    axs[1,2].hist(neg_g, bins=25, color=ACC, alpha=0.75, label="Brake A", density=True)
    if dfb is not None:
        pg_b = dfb["long_g"][dfb["long_g"] > 0]
        ng_b = dfb["long_g"][dfb["long_g"] < 0].abs()
        axs[1,2].hist(pg_b, bins=25, color=GRN, alpha=0.35, histtype="step", lw=1.5, density=True, label="Accel B")
        axs[1,2].hist(ng_b, bins=25, color=ACC, alpha=0.35, histtype="step", lw=1.5, density=True, label="Brake B")
    _ax(axs[1,2], "Long G Distribution", xl="|Long G|", yl="Density")
    axs[1,2].legend(fontsize=7); axs[1,2].grid(False)

# ── NEW: Telemetry Diff ────────────────────────────────────────────────────────
def draw_telediff(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)

    if dfb is None:
        for ax in axs.flat:
            ax.text(0.5, 0.5, "Load Lap B &\nEnable Compare Mode\nto see telemetry diffs",
                    ha="center", va="center", color=DIM, fontsize=11)
            ax.axis("off")
        return

    ta = (df["track_position"]  / max(df["track_position"].max(),  1)).values
    tb = (dfb["track_position"] / max(dfb["track_position"].max(), 1)).values
    ta_s = np.sort(ta)
    tb_s = np.sort(tb)

    def va(col): return np.interp(ta_s, ta_s, df.sort_values("track_position")[col].values)
    def vb(col): return np.interp(ta_s, tb_s, dfb.sort_values("track_position")[col].values)
    def diff(col): return vb(col) - va(col)

    def dp(ax, col, title, yl="Δ"):
        d = diff(col)
        ax.plot(ta_s, d, color=YLW, lw=1.2)
        ax.axhline(0, color=DIM, lw=0.8, ls="--")
        ax.fill_between(ta_s, d, 0, where=d>0, alpha=0.15, color=ACC)
        ax.fill_between(ta_s, d, 0, where=d<0, alpha=0.15, color=GRN)
        _ax(ax, title, xl="Lap %", yl=yl)

    dp(axs[0,0], "speed_kmh", "Δ Speed (B−A)",    yl="Δkm/h")
    dp(axs[0,1], "throttle",  "Δ Throttle (B−A)", yl="Δ")
    dp(axs[0,2], "brake",     "Δ Brake (B−A)",    yl="Δ")
    dp(axs[1,0], "rpm",       "Δ RPM (B−A)",      yl="ΔRPM")
    dp(axs[1,1], "steering",  "Δ Steering (B−A)", yl="Δ (−1..1)")
    dp(axs[1,2], "lat_g",     "Δ Lat G (B−A)",    yl="Δg")

    dt = diff("t")
    axs[2,0].plot(ta_s, dt, color=YLW, lw=1.5)
    axs[2,0].axhline(0, color=DIM, lw=0.8, ls="--")
    axs[2,0].fill_between(ta_s, dt, 0, where=dt>0, alpha=0.18, color=ACC)
    axs[2,0].fill_between(ta_s, dt, 0, where=dt<0, alpha=0.18, color=GRN)
    final = dt[-1] if len(dt) else 0
    _ax(axs[2,0], f"Δ Time  (final {final:+.3f}s)", xl="Lap %", yl="Δs")

    dp(axs[2,1], "long_g", "Δ Long G (B−A)", yl="Δg")

    axs[2,2].plot(ta_s, va("speed_kmh"), color=CYN, lw=1.5, label="A")
    axs[2,2].plot(ta_s, vb("speed_kmh"), color=ACC, lw=1.5, ls="--", label="B")
    _ax(axs[2,2], "Speed Overlay", xl="Lap %", yl="km/h"); axs[2,2].legend(fontsize=7)

# ── NEW: Driver Ratings ────────────────────────────────────────────────────────
def draw_ratings(fig, df, dfb=None):
    gs  = GridSpec(2, 2, figure=fig, hspace=0.5, wspace=0.45)
    ax1 = fig.add_subplot(gs[0, 0])
    ax_r= fig.add_subplot(gs[0, 1], projection="polar")
    ax3 = fig.add_subplot(gs[1, 0])
    ax4 = fig.add_subplot(gs[1, 1])

    def compute_scores(d):
        # per-sample change shrinks as the recording rate rises: scale to a 10 Hz equivalent
        _st = d["t"].diff()
        _st = _st[_st > 1e-4]
        _k = 0.1 / float(_st.median()) if len(_st) else 1.0
        steer_smooth = max(0.0, 1.0 - min(1.0, _num(d["steering"].diff().fillna(0).std() * _k) * 5)) * 100
        braking = d[d["brake"] > 0.1]
        brk_eff = (min(100.0, braking["speed_kmh"].mean() / max(1.0, d["speed_kmh"].mean()) * 100)
                   if len(braking) > 10 else 50.0)
        not_brk = d[d["brake"] < 0.05]
        thr_app = float((not_brk["throttle"] > 0.85).mean() * 100) if len(not_brk) > 0 else 0.0
        cornering = d[d["lat_g"].abs() > 0.5]
        crn_spd = (min(100.0, cornering["speed_kmh"].mean() / max(1.0, d["speed_kmh"].max()) * 120)
                   if len(cornering) > 10 else 50.0)
        has_sg = d["suggested_gear"].abs().max() > 0
        gear_m = float((d["gear"] == d["suggested_gear"]).mean() * 100) if has_sg else 60.0
        sec_avgs = [s["speed_kmh"].mean() for s in lap_sectors(d, 10)[0] if len(s)]
        consist = (max(0.0, min(100.0, 100.0 - np.std(sec_avgs) / max(1.0, np.mean(sec_avgs)) * 100))
                   if len(sec_avgs) >= 3 else 50.0)
        slip_var = float(d[["tyre_slip_rl", "tyre_slip_rr"]].std().mean())
        traction = max(0.0, 100.0 - slip_var * 20)
        return {
            "Steering\nSmooth":   steer_smooth,
            "Braking\nEffic.":    brk_eff,
            "Throttle\nApply":    thr_app,
            "Corner\nSpeed":      crn_spd,
            "Gear\nEffic.":       gear_m,
            "Lap\nConsist.":      consist,
            "Traction\nCtrl":     traction,
        }

    sc_a = compute_scores(df)
    # A leaderboard ghost only carries pedal/steering/gear inputs: scoring it
    # would mean inventing fallback numbers for everything else.
    ghost_b = dfb is not None and is_compact(dfb)
    sc_b = compute_scores(dfb) if (dfb is not None and not ghost_b) else None
    if ghost_b:
        fig.text(0.5, 0.01, "Lap B is a leaderboard ghost (inputs only), so it isn't rated.",
                 ha="center", color=DIM, fontsize=7)
    labels = list(sc_a.keys())
    va = [sc_a[k] for k in labels]
    overall_a = float(np.mean(va))

    # ── Horizontal bar chart ──
    y_pos = np.arange(len(labels))
    bh = 0.34 if sc_b else 0.55
    bar_col_a = [GRN if v >= 70 else YLW if v >= 45 else ACC for v in va]
    ax1.barh([y + (bh/2 if sc_b else 0) for y in y_pos], va,
              height=bh, color=bar_col_a, alpha=0.9, label="A")
    if sc_b:
        vb2 = [sc_b[k] for k in labels]
        bar_col_b = [GRN if v >= 70 else YLW if v >= 45 else ACC for v in vb2]
        ax1.barh([y - bh/2 for y in y_pos], vb2,
                  height=bh, color=bar_col_b, alpha=0.65, hatch="//", label="B")
        ax1.legend(fontsize=7)
    for y, v in zip(y_pos, va):
        ax1.text(v + 1, y + (bh/2 if sc_b else 0), f"{v:.0f}",
                 va="center", color=FG, fontsize=7)
    ax1.set_yticks(y_pos); ax1.set_yticklabels(labels, fontsize=7)
    ax1.set_xlim(0, 115)
    ax1.axvline(70, color=GRN, lw=0.7, ls="--", alpha=0.5)
    ax1.set_title("Driver Ratings", color=FG, fontsize=9)
    ax1.set_xlabel("Score (0–100)", fontsize=7, color=DIM)
    ax1.grid(axis="y", alpha=0.3); ax1.set_facecolor(PNL2)
    ax1.tick_params(colors=DIM, labelsize=7)

    # ── Radar chart ──
    cats   = labels
    N_c    = len(cats)
    angles = np.linspace(0, 2*np.pi, N_c, endpoint=False).tolist()
    va_r   = [v/100 for v in va] + [va[0]/100]
    angs_c = angles + [angles[0]]
    ax_r.set_theta_offset(np.pi/2); ax_r.set_theta_direction(-1)
    ax_r.plot(angs_c, va_r, color=CYN, lw=1.5)
    ax_r.fill(angs_c, va_r, color=CYN, alpha=0.18)
    if sc_b:
        vb_r = [sc_b[k]/100 for k in cats] + [sc_b[cats[0]]/100]
        ax_r.plot(angs_c, vb_r, color=ACC, lw=1.5, ls="--")
        ax_r.fill(angs_c, vb_r, color=ACC, alpha=0.1)
    ax_r.set_thetagrids(np.degrees(angles), cats, fontsize=6, color=FG)
    ax_r.set_ylim(0, 1); ax_r.set_facecolor(PNL2)
    ax_r.tick_params(colors=DIM, labelsize=5)
    ax_r.set_title("Performance Radar", color=FG, fontsize=9, pad=20)
    ax_r.grid(color=DIM, alpha=0.5)

    # ── Rolling throttle/brake trend ──
    win = max(1, len(df) // 20)
    ax3.plot(df["track_position"], df["throttle"].rolling(win, min_periods=1).mean()*100,
             color=GRN, lw=1.5, label="Throttle A")
    ax3.plot(df["track_position"], df["brake"].rolling(win, min_periods=1).mean()*100,
             color=ACC, lw=1.5, label="Brake A")
    if dfb is not None:
        win_b = max(1, len(dfb) // 20)
        ax3.plot(dfb["track_position"], dfb["throttle"].rolling(win_b, min_periods=1).mean()*100,
                 color=YLW, lw=1.2, ls="--", alpha=0.75, label="Throttle B")
        ax3.plot(dfb["track_position"], dfb["brake"].rolling(win_b, min_periods=1).mean()*100,
                 color=PRP, lw=1.2, ls="--", alpha=0.75, label="Brake B")
    ax3.set_title("Rolling Throttle / Brake", color=FG, fontsize=9)
    ax3.set_xlabel("Track Position (m)", fontsize=7, color=DIM)
    ax3.set_ylabel("%", fontsize=7, color=DIM)
    ax3.legend(fontsize=7); ax3.grid(True, alpha=0.4)
    ax3.set_facecolor(PNL2); ax3.tick_params(colors=DIM, labelsize=7)

    # ── Grade box ──
    grade = ("S"  if overall_a >= 90 else "A"  if overall_a >= 80 else
             "B+" if overall_a >= 75 else "B"  if overall_a >= 70 else
             "C"  if overall_a >= 60 else "D")
    gcol  = (GRN if grade in ("S","A","B+") else YLW if grade == "B" else
              ORG if grade == "C" else ACC)
    ax4.set_facecolor(PNL2); ax4.axis("off")
    ax4.text(0.5, 0.70, grade,  ha="center", va="center", color=gcol,
             fontsize=58, fontweight="bold", transform=ax4.transAxes)
    ax4.text(0.5, 0.42, f"{overall_a:.1f} / 100", ha="center", va="center",
             color=FG, fontsize=13, transform=ax4.transAxes)
    if sc_b:
        overall_b = float(np.mean([sc_b[k] for k in labels]))
        diff_ov   = overall_a - overall_b
        dc = GRN if diff_ov > 0 else ACC
        ax4.text(0.5, 0.24, f"B: {overall_b:.1f}   (Δ {diff_ov:+.1f})",
                 ha="center", va="center", color=dc, fontsize=9, transform=ax4.transAxes)
    ax4.set_title("Grade", color=FG, fontsize=9)

# ── NEW: Mini Map Heatmap ──────────────────────────────────────────────────────
def draw_minimap_heatmap(fig, df, dfb=None):
    has_a = df["world_x"].abs().max() > 1
    has_b = dfb is not None and dfb["world_x"].abs().max() > 1

    METRICS_9 = [
        ("speed_kmh",    "turbo",    "Speed (km/h)"),
        ("throttle",     "Greens",   "Throttle"),
        ("brake",        "Reds",     "Brake"),
        ("lat_g",        "RdBu_r",   "Lat G"),
        ("long_g",       "RdYlGn",   "Long G"),
        ("total_g",      "hot",      "Total G"),
        ("tyre_temp_fl", "YlOrRd",   "Tyre Temp FL"),
        ("rpm",          "plasma",   "RPM"),
        ("steering",     "coolwarm", "Steering"),
    ]
    METRICS_4 = [
        ("speed_kmh", "turbo",  "Speed"),
        ("throttle",  "Greens", "Throttle"),
        ("brake",     "Reds",   "Brake"),
        ("lat_g",     "RdBu_r", "Lat G"),
    ]

    if not has_b:
        axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.3, wspace=0.28)
        if not has_a:
            for ax in axs.flat:
                ax.text(0.5,0.5,"No GPS data",ha="center",va="center",color=DIM,fontsize=11)
                ax.axis("off")
            return
        for ax, (col, cmap, title) in zip(axs.flat, METRICS_9):
            _track_map(ax, df, col, cmap, title)
    else:
        # A vs B side-by-side for 4 metrics  (4 rows × 2 cols)
        axs = fig.subplots(4, 2); fig.subplots_adjust(hspace=0.28, wspace=0.18)
        for i, (col, cmap, title) in enumerate(METRICS_4):
            _track_map(axs[i, 0], df,  col, cmap, f"A — {title}")
            _track_map(axs[i, 1], dfb, col, cmap, f"B — {title}")

# ── NEW: Lap Timeline ──────────────────────────────────────────────────────────
def draw_timeline(fig, df, dfb=None):
    fig.subplots_adjust(hspace=0.55)

    def make_band(ax, d, label="A", col=CYN):
        xp  = d["track_position"].values
        ft  = d["throttle"].values > 0.95
        brk = d["brake"].values    > 0.05
        cst = (d["throttle"].values < 0.05) & (d["brake"].values < 0.05)
        ax.fill_between(xp, 2, 3, where=ft,  color=GRN, alpha=0.85, step="post")
        ax.fill_between(xp, 1, 2, where=brk, color=ACC, alpha=0.85, step="post")
        ax.fill_between(xp, 0, 1, where=cst, color=DIM, alpha=0.75, step="post")
        # gear annotations at changes
        gc = np.where(np.diff(d["gear"].values.astype(int)) != 0)[0]
        step = max(1, len(gc) // 30)
        for i in gc[::step]:
            if i + 1 < len(d):
                ax.text(xp[i], 3.12, str(int(d["gear"].values[i+1])),
                        ha="center", fontsize=5, color=YLW, clip_on=True)
        ax.set_yticks([0.5, 1.5, 2.5])
        ax.set_yticklabels(["Coast", "Brake", "Full Thr"], fontsize=7, color=DIM)
        ax.set_ylim(-0.1, 3.5); ax.set_xlim(xp.min(), xp.max())
        ax.set_title(f"Lap Timeline — {label}", color=col, fontsize=9)
        ax.set_xlabel("Track Position (m)", fontsize=7, color=DIM)
        ax.grid(True, axis="x", alpha=0.3); ax.set_facecolor(PNL2)
        ax.tick_params(colors=DIM, labelsize=7)

    if dfb is None:
        ax1 = fig.add_subplot(3, 1, 1); make_band(ax1, df, "A", CYN)
        ax2 = fig.add_subplot(3, 1, 2)
        ax2.plot(df["track_position"], df["speed_kmh"], color=CYN, lw=1.5)
        ax2.fill_between(df["track_position"], df["speed_kmh"], alpha=0.12, color=CYN)
        _ax(ax2, "Speed Trace", yl="km/h")
        ax3 = fig.add_subplot(3, 1, 3)
        ax3.step(df["track_position"], df["gear"], color=YLW, lw=1.5, where="post")
        ax3.fill_between(df["track_position"], df["gear"], alpha=0.15, color=YLW, step="post")
        _ax(ax3, "Gear", yl="Gear")
    else:
        ax1 = fig.add_subplot(2, 1, 1); make_band(ax1, df,  "A", CYN)
        ax2 = fig.add_subplot(2, 1, 2); make_band(ax2, dfb, "B", ACC)

# ── NEW: Extended (Packet B/~/C) ────────────────────────────────────────────────
def draw_extended(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = "track_position"

    has_ext = df["wheel_base"].abs().max() > 0 or df["sway"].abs().max() > 0 \
              or df["current_lap_ms"].abs().max() > 0
    if not has_ext:
        for ax in axs.flat:
            ax.text(0.5, 0.5, "No extended (B/~/C) data\nin this lap — recorded with\nheartbeat 'A' only",
                    ha="center", va="center", color=DIM, fontsize=10)
            ax.axis("off")
        return

    _L(axs[0,0], df, x, "sway",  PRP); _ax(axs[0,0], "Sway",  yl="m/s²")
    _L(axs[0,1], df, x, "heave", CYN); _ax(axs[0,1], "Heave", yl="m/s²")
    _L(axs[0,2], df, x, "surge", YLW); _ax(axs[0,2], "Surge", yl="m/s²")

    axs[1,0].plot(df[x], df["wheel_steering_angle_l"], color=C["fl"], lw=1.3, label="Front L")
    axs[1,0].plot(df[x], df["wheel_steering_angle_r"], color=C["fr"], lw=1.3, label="Front R")
    axs[1,0].axhline(0, color=DIM, lw=0.8, ls="--")
    _ax(axs[1,0], "Wheel Steering Angle", yl="rad"); axs[1,0].legend(fontsize=7)

    _L(axs[1,1], df, x, "current_lap_ms", GRN); _ax(axs[1,1], "Live Current Lap", yl="ms")

    _L(axs[1,2], df, x, "energy_recovery", ORG); _ax(axs[1,2], "Energy Recovery")

    has = df["world_x"].abs().max() > 1
    if has and df["surface_type"].astype(str).str.len().max() > 0:
        surf_map = {"T": 0, "C": 1, "D": 2, "G": 3, "S": 4, "s": 5}
        code = df["surface_type"].astype(str).str[0].map(surf_map).fillna(0)
        gf = df.copy(); gf["surf_code"] = code.astype(float)
        _track_map(axs[2,0], gf, "surf_code", "tab10", "Surface (FL) — 0=Tarmac 1=Kerb 2=Dirt 3=Grass 4=Sand 5=Snow")
    else:
        axs[2,0].text(0.5,0.5,"No surface/GPS data",ha="center",va="center",color=DIM,fontsize=9)
        axs[2,0].axis("off")

    cats = df["car_category"].astype(str)
    cats = cats[cats != ""]
    if len(cats):
        vc = cats.value_counts()
        axs[2,1].bar(vc.index.astype(str), vc.values, color=CYN, alpha=0.85)
        _ax(axs[2,1], "Car Category (samples)", xl="", yl="Count"); axs[2,1].grid(False)
    else:
        axs[2,1].text(0.5,0.5,"No category data",ha="center",va="center",color=DIM,fontsize=9)
        axs[2,1].axis("off")

    wb = df["wheel_base"][df["wheel_base"] > 0]
    axs[2,2].text(0.5, 0.5, f"Wheelbase:\n{wb.mean():.3f} m" if len(wb) else "No wheelbase data",
                  ha="center", va="center", color=FG, fontsize=12)
    axs[2,2].axis("off")

    if dfb is not None:
        _Lb(axs[0,0], dfb, x, "sway"); _Lb(axs[0,1], dfb, x, "heave"); _Lb(axs[0,2], dfb, x, "surge")
        _Lb(axs[1,0], dfb, x, "wheel_steering_angle_l"); _Lb(axs[1,0], dfb, x, "wheel_steering_angle_r")
        _Lb(axs[1,1], dfb, x, "current_lap_ms")
        _Lb(axs[1,2], dfb, x, "energy_recovery")

# ── NEW: Consensus Racing Line ──────────────────────────────────────────────
def draw_consensus(fig, df, dfb=None, consensus=None):
    """Community-average speed/throttle/brake vs track_position, bucketed
    server-side (Phase 4) -- overlaid against Lap A. No GPS coordinates in
    leaderboard data, so this is track-position-based rather than a
    _track_map-style GPS heatmap."""
    axs = fig.subplots(2, 2); fig.subplots_adjust(hspace=0.5, wspace=0.35)
    x = "track_position"
    if not consensus:
        for ax in axs.flat:
            ax.text(0.5, 0.5, "No community line loaded yet\n(LEADERBOARD panel → Load Community Line)",
                    ha="center", va="center", color=DIM, fontsize=10)
            ax.axis("off")
        return
    bx = [c["bucket_start"] for c in consensus]
    def cy(k): return [c.get(k, 0) for c in consensus]

    _L(axs[0,0], df, x, "speed_kmh", C["speed"], lbl="You")
    axs[0,0].plot(bx, cy("avg_speed"), color=YLW, lw=1.8, ls="--", label="Community")
    axs[0,0].legend(fontsize=7); _ax(axs[0,0], "Speed vs Community", yl="km/h")

    _L(axs[0,1], df, x, "throttle", C["throttle"], lbl="You")
    axs[0,1].plot(bx, cy("avg_throttle"), color=YLW, lw=1.8, ls="--", label="Community")
    axs[0,1].legend(fontsize=7); _ax(axs[0,1], "Throttle vs Community", yl="0–1")

    _L(axs[1,0], df, x, "brake", C["brake"], lbl="You")
    axs[1,0].plot(bx, cy("avg_brake"), color=YLW, lw=1.8, ls="--", label="Community")
    axs[1,0].legend(fontsize=7); _ax(axs[1,0], "Brake vs Community", yl="0–1")

    n_laps = consensus[0].get("lap_count", 0) if consensus else 0
    n_buckets = len(consensus)
    axs[1,1].text(0.5, 0.6, f"Consensus from\n{n_laps} top laps", ha="center", va="center",
                  color=FG, fontsize=13, transform=axs[1,1].transAxes)
    axs[1,1].text(0.5, 0.3, f"{n_buckets} track-position buckets (10m each)", ha="center", va="center",
                  color=DIM, fontsize=9, transform=axs[1,1].transAxes)
    axs[1,1].axis("off")

def draw_corners(fig, df, dfb=None):
    """Per-corner breakdown. With Compare on, lap B is the reference (load your
    PB there) and every corner is scored against it: brake point, minimum speed,
    throttle-on point and time gained/lost. With Compare off it just lists the
    corners found on this lap. The label is a heuristic read on style, not a
    lap-time verdict."""
    ax1, ax2 = fig.subplots(2, 1, gridspec_kw={"height_ratios": [1, 1.4]})
    fig.subplots_adjust(hspace=0.3, left=0.07, right=0.97, top=0.9, bottom=0.04)
    if dfb is not None:
        cs, _, deltas = corners.match_corners(df, dfb)
    else:
        cs, deltas = corners.detect_corners(df), []
    label = corners.session_label(df, cs, deltas)
    fig.suptitle(f"Corners — {label}" if label else "Corners", color=FG, fontsize=11)

    ax1.set_facecolor(PNL2)
    if deltas:
        nums = [d.number for d in deltas]
        vals = [d.time_s or 0.0 for d in deltas]
        ax1.bar([str(n) for n in nums], vals, color=[GRN if v <= 0 else ACC for v in vals])
        ax1.axhline(0, color=FG, lw=0.8)
        ax1.set_title("Time vs reference per corner (above 0 = lost, s)", color=FG, fontsize=9)
        ax1.set_xlabel("Corner", fontsize=7, color=DIM)
        ax1.tick_params(colors=DIM, labelsize=8); ax1.grid(axis="y", alpha=0.3)
    else:
        msg = ("No corners found on this lap." if not cs else
               "Turn Compare ON and load your PB as lap B\nto score each corner against it.")
        ax1.text(0.5, 0.5, msg, ha="center", va="center", color=DIM, fontsize=10,
                 transform=ax1.transAxes)
        ax1.set_xticks([]); ax1.set_yticks([])

    ax2.set_facecolor(PNL2); ax2.set_xticks([]); ax2.set_yticks([])
    for s in ax2.spines.values(): s.set_visible(False)

    def fmt(v, spec, unit):
        return "--".rjust(len(format(0.0, spec)) + len(unit)) if v is None else f"{v:{spec}}{unit}"

    if deltas:
        lines = [" #   Brake pt     Min speed      Throttle-on     Time"]
        for d in deltas:
            lines.append(f"{d.number:>2}  {fmt(d.brake_m, '+7.0f', ' m')}  {fmt(d.min_speed_kmh, '+7.0f', ' km/h')}"
                         f"  {fmt(d.throttle_m, '+7.0f', ' m')}  {fmt(d.time_s, '+7.2f', ' s')}")
        lines += ["", corners.describe_time_loss(deltas), "",
                  "Brake pt +: braked later than reference.  Throttle-on −: back on power earlier.",
                  "Min speed +: more speed through the corner."]
    else:
        lines = [" #   Brake at     Min speed      Throttle at"]
        for c in cs:
            lines.append(f"{c.number:>2}  {fmt(c.brake_pos, '7.0f', ' m')}  {fmt(c.min_speed, '7.0f', ' km/h')}"
                         f"  {fmt(c.throttle_pos, '7.0f', ' m')}")
    ax2.text(0.02, 0.97, "\n".join(lines), ha="left", va="top", color=FG, fontsize=8,
             family="monospace", transform=ax2.transAxes)

def draw_style(fig, df, dfb=None):
    """Driving-style classification: how smooth vs aggressive the inputs are
    (rate-of-change of steering/throttle/brake), and whether the car tends
    to understeer or oversteer while cornering (front vs rear tyre slip
    while lat_g is meaningful). This is a heuristic read on style, not a
    physics model or a lap-time verdict -- it won't tell you the lap was
    fast, just how it was driven."""
    axs = fig.subplots(2, 2); fig.subplots_adjust(hspace=0.55, wspace=0.4)

    def style_metrics(d):
        # Per-sample changes shrink as the recording rate rises, so scale
        # them to a 10 Hz equivalent -- the same lap scores the same at 10 or 60 Hz.
        step = d["t"].diff()
        step = step[step > 1e-4]
        k = 0.1 / float(step.median()) if len(step) else 1.0
        dt = d["steering"].diff().abs().fillna(0) * k
        tt = d["throttle"].diff().abs().fillna(0) * k
        bt = d["brake"].diff().abs().fillna(0) * k
        aggression = float(min(100.0, (dt.mean()*220 + tt.mean()*140 + bt.mean()*140)))
        corner = d[d["lat_g"].abs() > 0.5]
        slip_cols = {"tyre_slip_fl", "tyre_slip_fr", "tyre_slip_rl", "tyre_slip_rr"}
        if len(corner) > 10 and slip_cols <= set(d.columns):
            front = corner[["tyre_slip_fl", "tyre_slip_fr"]].mean(axis=1)
            rear  = corner[["tyre_slip_rl", "tyre_slip_rr"]].mean(axis=1)
            balance = float((rear - front).mean())  # + oversteer, - understeer
        else:
            balance = 0.0
        return aggression, balance, corner

    agg_a, bal_a, corn_a = style_metrics(df)
    agg_b, bal_b, corn_b = style_metrics(dfb) if dfb is not None else (None, None, None)

    def style_label(agg):
        return "Smooth" if agg < 33 else "Balanced" if agg < 66 else "Aggressive"

    def balance_label(bal):
        if bal > 0.015: return "Oversteer-leaning"
        if bal < -0.015: return "Understeer-leaning"
        return "Neutral"

    ax1 = axs[0, 0]
    labels_ = ["A"] + (["B"] if dfb is not None else [])
    vals = [agg_a] + ([agg_b] if dfb is not None else [])
    cols = [GRN if v < 33 else YLW if v < 66 else ACC for v in vals]
    ax1.barh(labels_, vals, color=cols, height=0.5)
    for i, v in enumerate(vals):
        ax1.text(v + 2, i, f"{v:.0f} — {style_label(v)}", va="center", color=FG, fontsize=8)
    ax1.set_xlim(0, 130); ax1.set_title("Driving Style (Smooth ↔ Aggressive)", color=FG, fontsize=9)
    ax1.set_xlabel("Aggression index (0-100)", fontsize=7, color=DIM)
    ax1.set_facecolor(PNL2); ax1.tick_params(colors=DIM, labelsize=8); ax1.grid(axis="x", alpha=0.3)

    ax2 = axs[0, 1]
    show_b_bal = dfb is not None and not is_compact(dfb)   # a ghost carries no tyre-slip data
    bvals = [bal_a] + ([bal_b] if show_b_bal else [])
    bcols = [ACC if b > 0.015 else CYN if b < -0.015 else DIM for b in bvals]
    ax2.barh(labels_[:len(bvals)], bvals, color=bcols, height=0.5)
    ax2.axvline(0, color=FG, lw=0.8)
    for i, b in enumerate(bvals):
        ax2.text(b + (0.002 if b >= 0 else -0.002), i, balance_label(b),
                  va="center", ha="left" if b >= 0 else "right", color=FG, fontsize=8)
    lim = max(0.05, max(abs(b) for b in bvals) * 1.6)
    ax2.set_xlim(-lim, lim)
    ax2.set_title("Cornering Balance (← Understeer | Oversteer →)", color=FG, fontsize=9)
    ax2.set_xlabel("Rear − front slip while cornering", fontsize=7, color=DIM)
    ax2.set_facecolor(PNL2); ax2.tick_params(colors=DIM, labelsize=8); ax2.grid(axis="x", alpha=0.3)

    ax3 = axs[1, 0]
    if len(corn_a) > 10:
        front_a = corn_a[["tyre_slip_fl", "tyre_slip_fr"]].mean(axis=1)
        rear_a  = corn_a[["tyre_slip_rl", "tyre_slip_rr"]].mean(axis=1)
        ax3.scatter(corn_a["track_position"], rear_a - front_a, s=6, color=CYN, alpha=0.6, label="A")
    if dfb is not None and corn_b is not None and len(corn_b) > 10:
        front_b = corn_b[["tyre_slip_fl", "tyre_slip_fr"]].mean(axis=1)
        rear_b  = corn_b[["tyre_slip_rl", "tyre_slip_rr"]].mean(axis=1)
        ax3.scatter(corn_b["track_position"], rear_b - front_b, s=6, color=ACC, alpha=0.5, label="B")
    ax3.axhline(0, color=DIM, lw=0.7)
    ax3.set_title("Balance Through Corners", color=FG, fontsize=9)
    ax3.set_xlabel("Track Position (m)", fontsize=7, color=DIM)
    ax3.set_ylabel("Rear − front slip", fontsize=7, color=DIM)
    if dfb is not None: ax3.legend(fontsize=7)
    ax3.set_facecolor(PNL2); ax3.tick_params(colors=DIM, labelsize=7); ax3.grid(alpha=0.3)

    ax4 = axs[1, 1]; ax4.axis("off"); ax4.set_facecolor(PNL2)
    bcol = ACC if bal_a > 0.015 else CYN if bal_a < -0.015 else FG
    ax4.text(0.5, 0.75, style_label(agg_a), ha="center", color=CYN, fontsize=20,
             fontweight="bold", transform=ax4.transAxes)
    ax4.text(0.5, 0.55, f"Aggression {agg_a:.0f}/100", ha="center", color=FG, fontsize=9,
             transform=ax4.transAxes)
    ax4.text(0.5, 0.35, balance_label(bal_a), ha="center", color=bcol, fontsize=14,
             fontweight="bold", transform=ax4.transAxes)
    ax4.text(0.5, 0.18, "Lap A summary", ha="center", color=DIM, fontsize=8, transform=ax4.transAxes)
    ax4.set_title("Style Summary", color=FG, fontsize=9)

def draw_theoretical(fig, df, dfb=None, pool=None):
    """Theoretical best lap: the fastest micro-sector from each lap on this car and
    track, stitched into the lap you could drive, and the zones where your best
    real lap is held back. `pool` is a list of (label, frame); without one it
    uses Lap A and Lap B. It is a ceiling: sectors interact, so fixing a weak
    zone usually gains less than shown."""
    laps = pool if pool is not None else [("Lap A", df)] + ([("Lap B", dfb)] if dfb is not None else [])
    r = theoretical.theoretical_best(laps)
    if r is None:
        ax = fig.add_subplot(1, 1, 1)
        ax.set_facecolor(PNL2); ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values(): s.set_visible(False)
        ax.text(0.5, 0.5, "Needs at least two complete laps of this car on this track.\n"
                          "Drive more laps in the Live Dashboard, or load a second lap as Lap B.",
                ha="center", va="center", color=DIM, fontsize=10, transform=ax.transAxes)
        return
    palette = [CYN, ACC, YLW, GRN, PRP, ORG, "#96949c", "#f2f0ec"]
    axs = fig.subplots(2, 2)
    fig.subplots_adjust(hspace=0.45, wspace=0.28, left=0.07, right=0.97, top=0.92, bottom=0.07)
    centers = r["centers"]

    ax = axs[0, 0]
    ax.fill_between(centers, r["cum_gap"], color=ACC, alpha=0.25, step="mid")
    ax.step(centers, r["cum_gap"], color=ACC, lw=1.4, where="mid")
    _ax(ax, "Time left on the table along the lap (s)", yl="s")

    ax = axs[0, 1]
    zones = r["zones"]
    if zones:
        top3 = {id(z) for z in zones[:3]}
        ax.bar([(z["start"] + z["end"]) / 2 for z in zones], [z["loss"] for z in zones],
               width=[(z["end"] - z["start"]) * 0.9 for z in zones],
               color=[ACC if id(z) in top3 else DIM for z in zones])
    _ax(ax, "Where your best lap loses time (per ~100 m zone, s)", yl="s")

    ax = axs[1, 0]
    step = float(centers[1] - centers[0]) if len(centers) > 1 else 1.0
    ax.bar(centers, 1, width=step, color=[palette[d % len(palette)] for d in r["donor"]])
    ax.set_yticks([])
    for i, lab in enumerate(r["labels"]):
        ax.bar([], [], color=palette[i % len(palette)], label=lab[:28])
    ax.legend(fontsize=6, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.2))
    _ax(ax, "Which lap holds the fastest time in each part of the track")

    ax = axs[1, 1]
    ax.axis("off")
    best_label = r["labels"][r["best_idx"]]
    lines = [f"Best real lap   {_fmt_laptime(r['actual'])}   ({best_label[:30]})",
             f"Theoretical     {_fmt_laptime(r['theoretical'])}",
             f"On the table    {r['gap']:.3f} s", f"Laps used       {len(r['labels'])}"]
    if r["skipped"]:
        lines.append(f"Left out        {r['skipped']} (incomplete or a different length)")
    lines.append("")
    if zones:
        lines.append("Held back most at:")
        for z in zones[:3]:
            lines.append(f"  {z['start']:>5.0f}-{z['end']:<5.0f} m   +{z['loss']:.3f} s   "
                         f"({r['labels'][z['donor']][:22]} is quicker)")
    else:
        lines.append("Your best lap is already the quickest everywhere.")
    ax.text(0.0, 1.0, "\n".join(lines), ha="left", va="top", color=FG, fontsize=8, family="monospace",
            transform=ax.transAxes)
    ax.set_title("Theoretical best lap", color=FG, fontsize=9)

def _blank(fig, msg):
    ax = fig.add_subplot(1, 1, 1)
    ax.set_facecolor(PNL2); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)
    ax.text(0.5, 0.5, msg, ha="center", va="center", color=DIM, fontsize=10, transform=ax.transAxes)


def draw_dyno(fig, df, dfb=None):
    """Dyno & Tune: the engine's power and torque curve, the gearbox (ratios, top
    speed per gear, best shift points) and tuning hints, all rebuilt from this
    lap's telemetry -- see dyno.py for how, and why the absolute numbers are an
    estimate while the curve shape and shift points are solid."""
    import textwrap
    if is_compact(df):
        _blank(fig, "A leaderboard ghost only carries driver inputs.\nLoad one of your own saved laps.")
        return
    gr = df.attrs.get("gear_ratios") or []
    res = dyno.analyse(df, gr, df.attrs.get("mass_kg"), df.attrs.get("tyre_radius_m"))
    if not res["ok"]:
        _blank(fig, "Can't build the dyno from this lap.\n\n" + textwrap.fill(res["msg"], 70))
        return
    resb = None
    if dfb is not None and not is_compact(dfb):
        resb = dyno.analyse(dfb, dfb.attrs.get("gear_ratios") or [], dfb.attrs.get("mass_kg"),
                            dfb.attrs.get("tyre_radius_m"))
        resb = resb if resb["ok"] else None
    palette = [CYN, ACC, YLW, GRN, PRP, ORG, "#96949c", "#f2f0ec"]
    axs = fig.subplots(2, 2)
    fig.subplots_adjust(hspace=0.36, wspace=0.34, left=0.07, right=0.93, top=0.93, bottom=0.07)

    # power + torque (+ boost)
    ax = axs[0, 0]
    r = res["rpm"]
    ax.fill_between(r, res["q1_disp"], res["q3_disp"], color=ACC, alpha=0.15)
    ax.plot(r, res["p_disp"], color=ACC, lw=2.0, label=f"Power ({res['unit_p']})")
    ax.plot([res["peak_rpm"]], [res["peak_p_disp"]], "o", color=ACC)
    ax.annotate(f"{res['peak_p_disp']:.0f} {res['unit_p']} @ {res['peak_rpm']:.0f}",
                (res["peak_rpm"], res["peak_p_disp"]), xytext=(6, -14), textcoords="offset points",
                color=ACC, fontsize=8)
    if resb:
        ax.plot(resb["rpm"], resb["p_disp"], color=CYN, lw=1.6, ls="--", label="Power, Lap B")
    ax.set_ylim(bottom=0)
    ax2 = ax.twinx()
    ax2.plot(r, res["t_disp"], color=YLW, lw=1.5, label=f"Torque ({res['unit_t']})")
    if resb:
        ax2.plot(resb["rpm"], resb["t_disp"], color=YLW, lw=1.2, ls="--", alpha=0.6)
    ax2.set_ylim(bottom=0)
    ax2.tick_params(colors=YLW, labelsize=7)
    ax2.spines["right"].set_visible(True); ax2.spines["right"].set_color(YLW)
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="lower center", ncol=2)
    _ax(ax, "Power & torque at the wheels (shaded = spread of the samples)", xl="Engine rpm",
        yl=res["unit_p"])

    # pull in each gear
    ax = axs[0, 1]
    for g, (vv, th) in sorted(res["curves"].items()):
        ax.plot(vv, th, color=palette[(g - 1) % len(palette)], lw=1.3, label=str(g))
    for gd in res["gears"]:
        if gd["opt_shift"]:
            vs = gd["opt_shift"] / gd["k"]
            ax.plot([vs * 3.6], [res["P_edge"](gd["opt_shift"]) / vs / dyno.G], "v", color=FG, ms=5)
    ax.set_ylim(bottom=0)
    ax.legend(title="Gear", fontsize=6, title_fontsize=7, ncol=2, loc="upper right")
    _ax(ax, "Pull in each gear: dots = best shift points", xl="Speed (km/h)", yl="thrust (g)")

    # gearbox table
    ax = axs[1, 0]
    ax.axis("off")
    top = res["top"]
    lines = [f"Peak power    {res['peak_p_disp']:.0f} {res['unit_p']} at {res['peak_rpm']:.0f} rpm"
             + (f"   ({res['hp_per_tonne']:.0f} hp/t)" if res["mass"] else ""),
             f"Peak torque   {res['peak_t_disp']:.0f} {res['unit_t']} at {res['peak_tq_rpm']:.0f} rpm",
             f"Rev limiter   ~{res['rev_lim']:.0f} rpm",
             f"True top speed {top['kmh']:.0f} km/h  (limited by {top['by']}, gear {top['gear']})",
             "" if res["mass"] else "Enter the car's weight to see real hp and Nm.", "",
             "Gear  Ratio  km/h/1000  Top km/h  Lap%   Shift: best  yours"]
    for gd in res["gears"]:
        s_ratio = "%.3f" % gd["ratio"] if gd["ratio"] else "-"
        s_best = "%.0f" % gd["opt_shift"] if gd["opt_shift"] else "-"
        s_mine = "%.0f" % gd["your_shift"] if gd["your_shift"] else "-"
        star = "*" if gd["src"] != "measured" else " "
        lines.append(f"{gd['g']:>3}{star}  {s_ratio:>6}  {gd['kmh_per_1000rpm']:>8.1f}  "
                     f"{gd['top_kmh']:>8.0f}  {gd['share'] * 100:>4.0f}   {s_best:>10}  {s_mine:>5}")
    rs = res["resist"]
    lines += ["", f"Drag model {rs['src']}  (rolling {rs['c0']:.3f}, aero {rs['c1'] * 1e4:.2f}e-4)",
              f"Built from {res['n_samples']} clean full-throttle samples. * = gear inferred from its ratio."]
    if any("final_drive" in gd for gd in res["gears"]):
        fd = float(np.median([gd["final_drive"] for gd in res["gears"] if "final_drive" in gd]))
        lines.append(f"Final drive ~{fd:.2f}")
    ax.text(0.0, 1.0, "\n".join(lines), ha="left", va="top", color=FG, fontsize=8.8, family="monospace",
            transform=ax.transAxes)
    ax.set_title("Engine & gearbox", color=FG, fontsize=9)

    # tuning hints
    ax = axs[1, 1]
    ax.axis("off")
    hints = dyno.advise(df, res, gr)
    out = []
    if resb:
        dp = (resb["peak_wkg"] / res["peak_wkg"] - 1.0) * 100.0
        out += [f"Lap B vs Lap A: peak power-to-weight {dp:+.1f}%,",
                f"peak at {resb['peak_rpm']:.0f} rpm (A: {res['peak_rpm']:.0f}).", ""]
    if hints:
        for h in hints[:8]:
            tag = "!!!" if h["sev"] >= 3 else "!!" if h["sev"] == 2 else "!"
            out += textwrap.wrap(f"[{tag}] {h['area']}: {h['finding']}", 66)
            out += textwrap.wrap("-> " + h["fix"], 66, initial_indent="    ", subsequent_indent="       ")
            out.append("")
    else:
        out += ["Nothing stands out: balance, gearing and shift points look healthy for this lap."]
    out += textwrap.wrap("Change one thing at a time, then drive again. These point a direction; the "
                         "right amount depends on the car. (GTPlanet, Coach Dave, racinggames.gg)", 66)
    out = out[:30]
    ax.text(0.0, 1.0, "\n".join(out), ha="left", va="top", color=FG, fontsize=8.0, family="monospace",
            transform=ax.transAxes)
    ax.set_title("Tuning hints from this lap", color=FG, fontsize=9)


# ── Groups registry ────────────────────────────────────────────────────────────
GROUPS = [
    ("Inputs",    draw_inputs,          (3,3), (13,10)),
    ("Engine",    draw_engine,          (3,3), (13,10)),
    ("Tyres",     draw_tyres,           (3,3), (13,10)),
    ("Dynamics",  draw_dynamics,        (3,3), (13,10)),
    ("Maps",      draw_maps,            (2,2), (12, 8)),
    ("G-Force",   draw_gforce,          (2,2), (12, 8)),
    ("Fuel",      draw_fuel,            (2,3), (13, 8)),
    ("Braking",   draw_braking,         (3,3), (13,10)),
    ("Sectors",   draw_sectors,         (3,3), (13,10)),
    ("Traction",  draw_traction,        (2,3), (13, 9)),
    ("Tele Diff", draw_telediff,        (3,3), (13,10)),
    ("Ratings",   draw_ratings,         (2,2), (12, 9)),
    ("Heat Maps", draw_minimap_heatmap, (3,3), (13,10)),
    ("Timeline",  draw_timeline,        (3,1), (13, 9)),
    ("Extended",  draw_extended,        (3,3), (13,10)),
    ("Consensus", draw_consensus,       (2,2), (12, 8)),
    ("Style",     draw_style,           (2,2), (12, 8)),
    ("Corners",   draw_corners,         (2,1), (12, 9)),
    ("Theoretical", draw_theoretical,   (2,2), (12, 9)),
    ("Dyno & Tune", draw_dyno,          (2,2), (13,10)),
]

