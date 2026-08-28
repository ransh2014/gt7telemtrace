# lap_analyst.py — GT7 Lap Analyst
# pip install pandas matplotlib numpy
import base64
import csv
import io
import json
import math
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import matplotlib
import numpy as np
import pandas as pd

from . import auth, leaderboard, tracks
from . import config as runtime_config

matplotlib.use("TkAgg")
import warnings

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import LineCollection
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Polygon as MplPolygon

warnings.filterwarnings("ignore")

# ── Theme ─────────────────────────────────────────────────────────────────────
BG   = "#07080f"
PNL  = "#0d0e1a"
PNL2 = "#13141f"
ACC  = "#ff2255"
CYN  = "#00f0d4"
GRN  = "#39ff85"
YLW  = "#ffd500"
ORG  = "#ff8c00"
PRP  = "#b06aff"
FG   = "#c8d3f5"
DIM  = "#343856"
DIM2 = "#232438"
FONT  = ("Consolas", 9)
FONTB = ("Consolas", 9, "bold")
FONTL = ("Consolas", 8)
FONTH = ("Consolas", 13, "bold")

C = dict(speed=CYN, throttle=GRN, brake=ACC, gear=YLW, rpm=ORG,
         steering=PRP, clutch="#8899ff",
         fl=ACC, fr=YLW, rl=GRN, rr=CYN,
         col_a=CYN, col_b=ACC)

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": PNL2,
    "axes.edgecolor": DIM, "axes.labelcolor": DIM,
    "text.color": FG, "xtick.color": DIM, "ytick.color": DIM,
    "grid.color": DIM2, "grid.alpha": 1.0,
    "legend.facecolor": PNL, "legend.edgecolor": DIM,
    "font.family": "monospace", "font.size": 8,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.titlepad": 6,
})

# ── Data ──────────────────────────────────────────────────────────────────────
def load_lap_data(data):
    """Build the analysis DataFrame from an already-parsed lap dict --
    shared by load_lap() (reads a local JSON file) and ghost-lap download
    (data comes from Supabase instead of disk, via leaderboard.py). A
    downloaded ghost's samples only carry the compact fields
    (track_position/speed_kmh/throttle/brake/steering/gear), so every other
    column below falls back to its 0.0/"" default -- meaning no GPS map or
    replay for a ghost, but the input-trace charts and A/B diffs work fine."""
    samples = data.get("samples", [])
    if not samples: raise ValueError("No samples in file")
    df = pd.DataFrame(samples)
    for col in ["world_x","world_y","world_z","speed_kmh","throttle","brake","steering",
                "clutch","clutch_engaged","gear","suggested_gear","rpm","max_rpm",
                "rpm_warning","rpm_limiter","rpm_after_clutch","boost",
                "tyre_temp_fl","tyre_temp_fr","tyre_temp_rl","tyre_temp_rr",
                "tyre_slip_fl","tyre_slip_fr","tyre_slip_rl","tyre_slip_rr",
                "susp_fl","susp_fr","susp_rl","susp_rr","ride_height_mm",
                "fuel_remaining","fuel_capacity","oil_temp","water_temp","oil_pressure",
                "ang_x","ang_y","ang_z","vel_x","vel_y","vel_z",
                "heading","track_position","t",
                "wheel_rotation","steering_angular_velocity","sway","heave","surge",
                "energy_recovery","current_lap_ms",
                "wheel_steering_angle_l","wheel_steering_angle_r","wheel_base"]:
        if col not in df: df[col] = 0.0
    for col in ["surface_type", "car_category"]:
        if col not in df: df[col] = ""
    df = df.fillna(0)
    dt = df["t"].diff().replace(0, 0.1).fillna(0.1)
    dv = df["speed_kmh"].diff().fillna(0) / 3.6
    df["long_g"]       = (dv / dt / 9.81).clip(-4, 4)
    df["lat_g"]        = (df["ang_y"] * df["speed_kmh"] / 3.6 / 9.81).clip(-4, 4)
    df["total_g"]      = np.sqrt(df["long_g"]**2 + df["lat_g"]**2)
    df["fuel_burn"]    = (-df["fuel_remaining"].diff() / dt).clip(0, 10).fillna(0)
    df["coasting"]     = ((df["throttle"] < 0.05) & (df["brake"] < 0.05)).astype(float)
    df["front_t_avg"]  = (df["tyre_temp_fl"] + df["tyre_temp_fr"]) / 2
    df["rear_t_avg"]   = (df["tyre_temp_rl"] + df["tyre_temp_rr"]) / 2
    df["lr_t_bal"]     = (df["tyre_temp_fl"] + df["tyre_temp_rl"]) / 2 \
                       - (df["tyre_temp_fr"] + df["tyre_temp_rr"]) / 2
    return data, df

def load_lap(path):
    with open(path) as f: data = json.load(f)
    return load_lap_data(data)

def export_csv(df, out_path):
    """Dump a lap's per-sample telemetry to CSV. distance_m is derived by
    integrating speed_kmh over each frame's dt (same dt pattern used to
    build long_g in load_lap_data), since GT7's telemetry has no raw
    distance field of its own."""
    dt = df["t"].diff().replace(0, 0.1).fillna(0.1)
    distance_m = (df["speed_kmh"] / 3.6 * dt).cumsum()
    cols = ["distance_m","speed_kmh","throttle","brake","rpm","gear","steering"]
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i in range(len(df)):
            row = df.iloc[i]
            w.writerow([
                round(float(distance_m.iloc[i]), 2),
                round(float(row.get("speed_kmh", 0)), 2),
                round(float(row.get("throttle", 0)), 4),
                round(float(row.get("brake", 0)), 4),
                round(float(row.get("rpm", 0)), 1),
                int(row.get("gear", 0)),
                round(float(row.get("steering", 0)), 4),
            ])

def compute_sector_deltas(df_a, df_b, sector_length_m=25):
    """Per-point time-delta-to-reference, bucketed into fixed-distance
    sectors, for coloring the race-line map as a micro-sector heatmap.
    Alignment is by lap-percentage (same convention as the Replay tab's
    'synced' dual mode) since GT7 laps rarely share identical sample
    counts or timestamps. Returns an array the same length as df_a --
    positive = slower than reference in that sector, negative = faster."""
    dt_a = df_a["t"].diff().replace(0, 0.1).fillna(0.1)
    dist_a = (df_a["speed_kmh"] / 3.6 * dt_a).cumsum().values
    t_a = df_a["t"].values

    t_b = df_b["t"].values

    n = len(df_a)
    total_dist = max(dist_a[-1], 1.0)
    n_sectors = max(1, int(total_dist // sector_length_m) + 1)

    deltas = np.zeros(n)
    for s in range(n_sectors):
        lo, hi = s * sector_length_m, (s + 1) * sector_length_m
        mask_a = (dist_a >= lo) & (dist_a < hi)
        if not mask_a.any(): continue
        idx_a_in_sector = np.where(mask_a)[0]

        pct_lo = idx_a_in_sector[0] / max(1, n - 1)
        pct_hi = idx_a_in_sector[-1] / max(1, n - 1)
        idx_b_lo = int(pct_lo * (len(df_b) - 1))
        idx_b_hi = int(pct_hi * (len(df_b) - 1))
        idx_b_hi = max(idx_b_hi, idx_b_lo)

        sector_time_a = t_a[idx_a_in_sector[-1]] - t_a[idx_a_in_sector[0]]
        sector_time_b = t_b[idx_b_hi] - t_b[idx_b_lo]
        deltas[idx_a_in_sector] = sector_time_a - sector_time_b

    return deltas

def lap_label(data, short=False):
    t = data.get("lap_time_s", 0)
    m = int(t//60); s = t % 60
    car   = data.get("car","?")
    track = data.get("track","?").replace("_"," ").title()
    time_str = f"{m}:{s:06.3f}"
    if short: return f"{car}  {time_str}"
    return f"{car} @ {track}  {time_str}"

def build_stats(data, df):
    t = data.get("lap_time_s", 0); m = int(t//60); s = t%60
    fuel_used = df["fuel_remaining"].iloc[0] - df["fuel_remaining"].iloc[-1]
    return {
        "Lap Time":     f"{m}:{s:06.3f}",
        "Samples":      f"{len(df)}",
        "Top Speed":    f"{df['speed_kmh'].max():.1f} km/h",
        "Avg Speed":    f"{df['speed_kmh'].mean():.1f} km/h",
        "Full Thr%":    f"{(df['throttle']>0.95).mean()*100:.1f}%",
        "Coasting%":    f"{df['coasting'].mean()*100:.1f}%",
        "Max Lat G":    f"{df['lat_g'].abs().max():.2f}g",
        "Max Long G":   f"{df['long_g'].abs().max():.2f}g",
        "Fuel Used":    f"{fuel_used:.2f}",
        "Peak RPM":     f"{df['rpm'].max():.0f}",
        "Gear Changes": f"{int((df['gear'].diff()!=0).sum())}",
        "Avg Tyre °C":  f"{df[['tyre_temp_fl','tyre_temp_fr','tyre_temp_rl','tyre_temp_rr']].mean().mean():.1f}",
    }

def build_sector_stats(df):
    n = len(df)
    thirds = [df.iloc[:n//3], df.iloc[n//3:2*n//3], df.iloc[2*n//3:]]
    out = []
    for i, seg in enumerate(thirds, 1):
        if len(seg) == 0: continue
        t_seg = seg["t"].iloc[-1] - seg["t"].iloc[0]
        avg_s = seg["speed_kmh"].mean()
        top_s = seg["speed_kmh"].max()
        out.append((f"S{i}", f"{t_seg:.2f}s", f"{avg_s:.0f}", f"{top_s:.0f}"))
    return out

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
def draw_inputs(fig, df, dfb=None):
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
    _L(axs[1,0], df, x, "oil_temp",         ACC);            _ax(axs[1,0], "Oil Temp",       yl="°C")
    _L(axs[1,1], df, x, "water_temp",       CYN);            _ax(axs[1,1], "Water Temp",     yl="°C")
    _L(axs[1,2], df, x, "oil_pressure",     GRN);            _ax(axs[1,2], "Oil Pressure",   yl="kPa")
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
    _L(axs[0,0], df, x, "fuel_remaining", GRN, fill=True); _ax(axs[0,0], "Fuel Remaining",  yl="L/kWh")
    _L(axs[0,1], df, x, "fuel_burn",      ORG);            _ax(axs[0,1], "Fuel Burn Rate",   yl="L/s")
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

    zones = []
    in_z = False; t0 = 0.0
    for _, row in df.iterrows():
        if row["brake"] > 0.1 and not in_z:
            in_z = True; t0 = row["t"]
        elif row["brake"] <= 0.1 and in_z:
            in_z = False; zones.append(row["t"] - t0)
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
    def get_segs(d):
        nn = len(d); sz = max(1, nn // N)
        segs = [d.iloc[i*sz : (i+1)*sz] for i in range(N)]
        # last sector absorbs remainder
        if N * sz < nn:
            segs[-1] = d.iloc[(N-1)*sz:]
        return [s for s in segs if len(s) >= 2]

    def sector_stats(segs):
        times, avgs, tops, brks, thrs, latgs = [], [], [], [], [], []
        for seg in segs:
            times.append(seg["t"].iloc[-1] - seg["t"].iloc[0])
            avgs.append(seg["speed_kmh"].mean())
            tops.append(seg["speed_kmh"].max())
            brks.append(seg["brake"].mean() * 100)
            thrs.append(seg["throttle"].mean() * 100)
            latgs.append(seg["lat_g"].abs().max())
        return times, avgs, tops, brks, thrs, latgs

    segs_a = get_segs(df)
    t_a, avg_a, top_a, brk_a, thr_a, latg_a = sector_stats(segs_a)
    na = len(t_a)

    t_b = avg_b = top_b = brk_b = thr_b = latg_b = segs_b = None
    if dfb is not None:
        segs_b = get_segs(dfb)
        t_b, avg_b, top_b, brk_b, thr_b, latg_b = sector_stats(segs_b)

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
    dp(axs[1,1], "steering",  "Δ Steering (B−A)", yl="Δrad")
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
        steer_smooth = max(0.0, 1.0 - min(1.0, d["steering"].diff().fillna(0).std() * 5)) * 100
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
        nn = len(d); sz = max(1, nn // 10)
        sec_avgs = [d.iloc[i*sz:(i+1)*sz]["speed_kmh"].mean() for i in range(10)]
        consist = max(0.0, min(100.0, 100.0 - np.std(sec_avgs) / max(1.0, np.mean(sec_avgs)) * 100))
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
    sc_b = compute_scores(dfb) if dfb is not None else None
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
        _track_map(axs[2,0], gf, "surf_code", "tab10", "Surface (FL) — 0=T 1=C 2=D 3=G 4=S 5=s")
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
]

# ── Replay ────────────────────────────────────────────────────────────────────
class Replay:
    SPEEDS = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
    FPS    = 28

    def __init__(self, parent):
        self._df       = None
        self._dfb      = None
        self._la_label = ""
        self._lb_label = ""
        self._idx      = 0
        self._playing  = False
        self._si       = 2
        self._has_map  = False
        self._car_size = 10
        self._car_patch_a  = None
        self._car_patch_b  = None
        self._trail_l      = None
        self._trail_b      = None
        self._connect_line = None
        self._hdg_a    = None
        self._hdg_b    = None
        self._dual_mode = "synced"
        self._b_visible = False
        self._after_id  = None
        self._heatmap_mode = False
        self._current_track = ""
        self._boundary  = None
        self._build(parent)

    def _compute_headings(self, df):
        h = df["heading"].values.copy()
        if h.max() - h.min() > 0.05:
            return h
        dx = np.diff(df["world_x"].values, prepend=df["world_x"].values[0])
        dz = np.diff(df["world_z"].values, prepend=df["world_z"].values[0])
        return np.arctan2(dx, dz)

    def _car_verts(self, cx, cz, heading_rad, size):
        local = np.array([[0, size], [-size*0.55, -size*0.65], [size*0.55, -size*0.65]])
        cos_h, sin_h = math.cos(heading_rad), math.sin(heading_rad)
        rot = np.array([[cos_h, -sin_h], [sin_h, cos_h]])
        w = (rot @ local.T).T
        w[:, 0] += cx; w[:, 1] += cz
        return w

    def _update_car_patch(self, patch, cx, cz, heading_rad, size):
        patch.set_xy(self._car_verts(cx, cz, heading_rad, size))

    def _update_mini_bar(self, bars_dict, key, val_01):
        if key not in bars_dict: return
        cv, rect, w = bars_dict[key]
        cv.coords(rect, 0, 0, int(val_01 * w), 6)

    def _get_idx_b(self, idx_a):
        if self._dfb is None: return 0
        if self._dual_mode == "synced":
            pct = idx_a / max(1, len(self._df) - 1)
            return int(pct * (len(self._dfb) - 1))
        else:
            t_a = self._df.iloc[idx_a]["t"]
            t_b = self._dfb["t"].values
            raw = int(np.searchsorted(t_b, t_a, side="left"))
            return min(raw, len(self._dfb) - 1)

    def _build(self, parent):
        self._delta_var = tk.StringVar(value="")
        self._delta_lbl = tk.Label(parent, textvariable=self._delta_var,
                                   fg=YLW, bg=BG,
                                   font=("Consolas", 11, "bold"), pady=3)
        self._delta_lbl.pack(fill="x")

        ctrl = tk.Frame(parent, bg=PNL, pady=5)
        ctrl.pack(fill="x", padx=4, pady=(2,0))
        self._pbtn = tk.Button(ctrl, text="▶  Play", command=self._toggle,
                                bg=PNL2, fg=GRN, relief="flat", font=FONTB,
                                padx=10, pady=2, cursor="hand2")
        self._pbtn.pack(side="left", padx=(8,4))
        tk.Button(ctrl, text="⏮", command=self._reset,
                  bg=PNL2, fg=FG, relief="flat", font=FONT,
                  padx=6, pady=2, cursor="hand2").pack(side="left", padx=2)
        tk.Label(ctrl, text="  Speed:", fg=DIM, bg=PNL, font=FONT).pack(side="left")
        for i, s in enumerate(self.SPEEDS):
            tk.Button(ctrl, text=f"{s}×", bg=PNL2, fg=YLW, relief="flat", font=FONTL,
                      padx=5, pady=2, cursor="hand2",
                      command=lambda i=i: self._spd(i)).pack(side="left", padx=1)
        self._mode_btn = tk.Button(ctrl, text="🔀 Synced", command=self._toggle_mode,
                                    bg=DIM2, fg=CYN, relief="flat", font=FONTL,
                                    padx=8, pady=2, cursor="hand2")
        self._heatmap_btn = tk.Button(ctrl, text="🌡 Heatmap", command=self._toggle_heatmap,
                                    bg=DIM2, fg=DIM, relief="flat", font=FONTL,
                                    padx=8, pady=2, cursor="hand2")
        self._boundary_btn = tk.Button(ctrl, text="💾 Save Boundary", command=self._save_boundary,
                                    bg=DIM2, fg=DIM, relief="flat", font=FONTL,
                                    padx=8, pady=2, cursor="hand2")
        self._boundary_btn.pack(side="right", padx=(8,0))
        tk.Button(ctrl, text="📄 Export CSV", command=self._export_csv,
                  bg=DIM2, fg=FG, relief="flat", font=FONTL,
                  padx=8, pady=2, cursor="hand2").pack(side="right", padx=8)

        info_wrap = tk.Frame(parent, bg=BG)
        info_wrap.pack(fill="x", padx=4, pady=2)

        self._info_a_frame = tk.Frame(info_wrap, bg=PNL2, pady=4)
        self._info_a_frame.pack(side="left", fill="x", expand=True, padx=(0,1))
        self._iv = {}; self._bars = {}
        for k in ["Time","Speed","Gear","Throttle","Brake","RPM","Lap %"]:
            f = tk.Frame(self._info_a_frame, bg=PNL2); f.pack(side="left", expand=True)
            tk.Label(f, text=k, fg=DIM, bg=PNL2, font=FONTL).pack()
            v = tk.StringVar(value="—")
            tk.Label(f, textvariable=v, fg=CYN, bg=PNL2, font=FONTB).pack()
            self._iv[k] = v
            if k in ("Throttle","Brake"):
                bar_c = GRN if k == "Throttle" else ACC
                cv = tk.Canvas(f, width=48, height=6, bg=DIM2, highlightthickness=0); cv.pack(pady=1)
                rect = cv.create_rectangle(0, 0, 0, 6, fill=bar_c, outline="")
                self._bars[k] = (cv, rect, 48)

        self._info_b_frame = tk.Frame(info_wrap, bg=PNL, pady=4)
        self._iv_b = {}; self._bars_b = {}
        for k in ["Time","Speed","Gear","Throttle","Brake","RPM","Lap %"]:
            f = tk.Frame(self._info_b_frame, bg=PNL); f.pack(side="left", expand=True)
            tk.Label(f, text=k, fg=DIM, bg=PNL, font=FONTL).pack()
            v = tk.StringVar(value="—")
            tk.Label(f, textvariable=v, fg=ACC, bg=PNL, font=FONTB).pack()
            self._iv_b[k] = v
            if k in ("Throttle","Brake"):
                bar_c = GRN if k == "Throttle" else ACC
                cv2 = tk.Canvas(f, width=48, height=6, bg=DIM2, highlightthickness=0); cv2.pack(pady=1)
                rect2 = cv2.create_rectangle(0, 0, 0, 6, fill=bar_c, outline="")
                self._bars_b[k] = (cv2, rect2, 48)

        self._sv = tk.IntVar(value=0)
        tk.Scale(parent, variable=self._sv, from_=0, to=1000, orient="horizontal",
                 bg=PNL, fg=DIM, troughcolor=PNL2, highlightthickness=0,
                 sliderrelief="flat", showvalue=False,
                 command=self._scrub).pack(fill="x", padx=4)

        self._fig, self._ax = plt.subplots(figsize=(9,5))
        self._fig.patch.set_facecolor(BG)
        self._ax.set_facecolor(BG); self._ax.axis("off")
        self._ax.set_title("Load a lap to start replay", color=DIM, fontsize=11)
        self._canvas = FigureCanvasTkAgg(self._fig, master=parent)
        self._canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)
        self._canvas.draw()

    def _toggle_mode(self):
        if self._dual_mode == "synced":
            self._dual_mode = "realtime"
            self._mode_btn.config(text="⏱ Real-Time", fg=ORG)
        else:
            self._dual_mode = "synced"
            self._mode_btn.config(text="🔀 Synced", fg=CYN)
        self._update()

    def _toggle_heatmap(self):
        self._heatmap_mode = not self._heatmap_mode
        if self._heatmap_mode:
            self._heatmap_btn.config(text="🌡 Heatmap ON", fg=ORG)
        else:
            self._heatmap_btn.config(text="🌡 Heatmap", fg=DIM)
        if self._df is not None:
            self._draw_base()
            self._update()

    def _export_csv(self):
        if self._df is None:
            messagebox.showinfo("Export CSV", "Load a lap first.")
            return
        default_name = (self._la_label or "lap").replace(" ", "_").replace(":", "") + ".csv"
        out_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=default_name,
        )
        if not out_path: return
        try:
            export_csv(self._df, out_path)
            messagebox.showinfo("Export CSV", f"Saved to:\n{out_path}")
        except Exception as e:
            messagebox.showerror("Export CSV", f"Failed to export:\n{e}")

    def _save_boundary(self):
        if self._df is None:
            messagebox.showinfo("Save Boundary", "Load a lap first.")
            return
        if not self._has_map:
            messagebox.showinfo("Save Boundary", "This lap has no GPS data to extract a boundary from.")
            return
        if not self._current_track:
            messagebox.showinfo("Save Boundary", "This lap has no track name recorded, so there's nothing to key the boundary cache by.")
            return
        samples = self._df[["world_x", "world_z", "track_position"]].to_dict("records")
        boundary = tracks.extract_boundary(samples)
        if not boundary["bins"]:
            messagebox.showinfo("Save Boundary", "Not enough GPS samples in this lap to extract a boundary.")
            return
        tracks.save_boundary(self._current_track, boundary)
        self._boundary = boundary
        self._boundary_btn.config(fg=CYN)
        self._draw_base(); self._update()
        messagebox.showinfo("Save Boundary", f"Saved track boundary for '{self._current_track}' ({len(boundary['bins'])} sectors).")

    def load(self, df, label="", track=""):
        self._df = df.reset_index(drop=True)
        self._la_label = label
        self._current_track = track or ""
        self._boundary = tracks.load_boundary(self._current_track) if self._current_track else None
        self._hdg_a = self._compute_headings(self._df)
        self._idx = 0; self._playing = False
        self._pbtn.config(text="▶  Play", fg=GRN)
        self._sv.set(0); self._delta_var.set("")
        if self._boundary:
            self._boundary_btn.config(fg=CYN)
        else:
            self._boundary_btn.config(fg=DIM)
        self._draw_base(); self._update()

    def load_b(self, df, label=""):
        self._dfb = df.reset_index(drop=True)
        self._lb_label = label
        self._hdg_b = self._compute_headings(self._dfb)
        if not self._b_visible:
            self._info_b_frame.pack(side="left", fill="x", expand=True, padx=(1,0))
            self._mode_btn.pack(side="right", padx=8)
            self._heatmap_btn.pack(side="right", padx=(8,0))
            self._b_visible = True
        if self._df is not None:
            self._draw_base()
        self._update()

    def _draw_base(self):
        df = self._df
        for ax in list(self._fig.axes):
            if ax is not self._ax: self._fig.delaxes(ax)
        self._ax.cla(); self._ax.set_facecolor(BG); self._ax.axis("off")
        self._car_patch_a = self._car_patch_b = None
        self._trail_l = self._trail_b = self._connect_line = None

        self._has_map = df["world_x"].abs().max() > 1
        if not self._has_map:
            self._ax.text(0.5,0.5,"No GPS data in this lap\n(world_x / world_z all zero)",
                          ha="center",va="center",color=DIM,fontsize=12)
            self._canvas.draw(); return

        x, z, spd = df["world_x"].values, df["world_z"].values, df["speed_kmh"].values
        xr = x.max()-x.min(); zr = z.max()-z.min()
        self._car_size = max(xr, zr) * 0.022

        if self._boundary and self._boundary.get("bins"):
            bins = self._boundary["bins"]
            lx = [b["left_x"] for b in bins] + [bins[0]["left_x"]]
            lz = [b["left_z"] for b in bins] + [bins[0]["left_z"]]
            rx = [b["right_x"] for b in bins] + [bins[0]["right_x"]]
            rz = [b["right_z"] for b in bins] + [bins[0]["right_z"]]
            self._ax.plot(lx, lz, color=DIM, lw=1.0, alpha=0.4, ls="--", zorder=0)
            self._ax.plot(rx, rz, color=DIM, lw=1.0, alpha=0.4, ls="--", zorder=0)

        use_heatmap = self._heatmap_mode and self._dfb is not None
        if use_heatmap:
            color_vals = compute_sector_deltas(df, self._dfb, sector_length_m=25)
            vmax = max(abs(color_vals.min()), abs(color_vals.max()), 0.01)
            cmap_name, norm = "RdYlGn_r", plt.Normalize(-vmax, vmax)
        else:
            color_vals, cmap_name, norm = spd, "turbo", plt.Normalize(spd.min(), spd.max())

        self._ax.plot(x, z, color=DIM, lw=0.8, alpha=0.25, zorder=1)
        pts  = np.array([x,z]).T.reshape(-1,1,2)
        segs = np.concatenate([pts[:-1],pts[1:]],axis=1)
        lc   = LineCollection(segs, cmap=cmap_name, norm=norm, lw=2.5, alpha=0.65, zorder=2)
        lc.set_array(color_vals); self._ax.add_collection(lc)
        self._ax.plot(x[0], z[0], "o", color=GRN, ms=7, zorder=5)
        self._ax.plot(x[-1],z[-1], "s", color=YLW, ms=7, zorder=5)

        self._trail_l, = self._ax.plot([], [], color=CYN, lw=2.5, alpha=0.75, zorder=6)
        h0 = float(self._hdg_a[0]) if self._hdg_a is not None else 0.0
        verts_a = self._car_verts(x[0], z[0], h0, self._car_size)
        self._car_patch_a = MplPolygon(verts_a, closed=True, fc=CYN,
                                        ec="#ffffff", lw=1.2, zorder=8, alpha=0.95)
        self._ax.add_patch(self._car_patch_a)

        if self._dfb is not None and self._dfb["world_x"].abs().max() > 1:
            xb, zb = self._dfb["world_x"].values, self._dfb["world_z"].values
            self._ax.plot(xb, zb, color=DIM, lw=0.5, alpha=0.18, zorder=1)
            self._trail_b, = self._ax.plot([], [], color=ACC, lw=2.5, alpha=0.75, zorder=6)
            h0b = float(self._hdg_b[0]) if self._hdg_b is not None else 0.0
            verts_b = self._car_verts(xb[0], zb[0], h0b, self._car_size)
            self._car_patch_b = MplPolygon(verts_b, closed=True, fc=ACC,
                                            ec="#ffffff", lw=1.2, zorder=8, alpha=0.95)
            self._ax.add_patch(self._car_patch_b)
            self._connect_line, = self._ax.plot([], [], color=YLW, lw=1.2,
                                                  alpha=0.55, ls="--", zorder=7)

        self._ax.set_aspect("equal"); self._ax.autoscale()
        title = f"Replay  ▷  {self._la_label}"
        if self._dfb is not None:
            title += f"  vs  {self._lb_label}"
        self._ax.set_title(title, color=FG, fontsize=9)
        cb = self._fig.colorbar(lc, ax=self._ax, fraction=0.025, pad=0.01)
        cb.set_label("Δs/sector (slower→red)" if use_heatmap else "km/h", color=DIM, fontsize=7)
        cb.ax.tick_params(colors=DIM, labelsize=6)
        self._canvas.draw()

    def _update(self):
        df = self._df
        if df is None: return
        idx = min(self._idx, len(df)-1)
        row = df.iloc[idx]

        if self._car_patch_a is not None:
            cx, cz = row["world_x"], row["world_z"]
            hdg = float(self._hdg_a[idx]) if self._hdg_a is not None else 0.0
            self._update_car_patch(self._car_patch_a, cx, cz, hdg, self._car_size)
            if self._trail_l is not None:
                s = max(0, idx-50)
                self._trail_l.set_data(df["world_x"].values[s:idx+1],
                                       df["world_z"].values[s:idx+1])

        t = row.get("t", 0)
        self._iv["Time"].set(f"{int(t//60)}:{t%60:05.2f}")
        self._iv["Speed"].set(f"{row.get('speed_kmh',0):.0f}")
        g = int(row.get("gear",0))
        self._iv["Gear"].set(str(g) if g>0 else "N")
        thr = row.get("throttle",0); brk = row.get("brake",0)
        self._iv["Throttle"].set(f"{thr*100:.0f}%")
        self._iv["Brake"].set(f"{brk*100:.0f}%")
        self._iv["RPM"].set(f"{row.get('rpm',0):.0f}")
        self._iv["Lap %"].set(f"{idx/(max(1,len(df)-1))*100:.1f}%")
        self._sv.set(int(idx/(max(1,len(df)-1))*1000))
        self._update_mini_bar(self._bars, "Throttle", thr)
        self._update_mini_bar(self._bars, "Brake", brk)

        if self._dfb is not None and self._car_patch_b is not None:
            idx_b = self._get_idx_b(idx)
            row_b = self._dfb.iloc[idx_b]
            cxb = row_b["world_x"]; czb = row_b["world_z"]
            hdgb = float(self._hdg_b[idx_b]) if self._hdg_b is not None else 0.0
            self._update_car_patch(self._car_patch_b, cxb, czb, hdgb, self._car_size)
            if self._trail_b is not None:
                sb = max(0, idx_b-50)
                self._trail_b.set_data(self._dfb["world_x"].values[sb:idx_b+1],
                                       self._dfb["world_z"].values[sb:idx_b+1])
            if self._connect_line is not None:
                self._connect_line.set_data([row["world_x"], cxb], [row["world_z"], czb])

            t_a = float(row.get("t", 0))
            t_b = float(row_b.get("t", 0))
            if self._dual_mode == "synced":
                delta = t_b - t_a
            else:
                pa = float(df["track_position"].values[idx])
                tp = self._dfb["track_position"].values
                tt = self._dfb["t"].values
                if len(tp) > 1:
                    t_b_at_pa = float(np.interp(pa, tp, tt))
                    delta = t_b_at_pa - t_a
                else:
                    delta = 0.0

            if abs(delta) < 0.01:
                self._delta_var.set("  ══  DEAD HEAT  ══")
            elif delta > 0:
                self._delta_var.set(f"  ▷▷  △ A  +{delta:.3f}s ahead of B ◁◁  ")
            else:
                self._delta_var.set(f"  ▷▷  ▲ B  +{-delta:.3f}s ahead of A ◁◁  ")

            tb_t = float(row_b.get("t", 0))
            self._iv_b["Time"].set(f"{int(tb_t//60)}:{tb_t%60:05.2f}")
            self._iv_b["Speed"].set(f"{row_b.get('speed_kmh',0):.0f}")
            gb = int(row_b.get("gear",0))
            self._iv_b["Gear"].set(str(gb) if gb>0 else "N")
            thr_b = row_b.get("throttle",0); brk_b = row_b.get("brake",0)
            self._iv_b["Throttle"].set(f"{thr_b*100:.0f}%")
            self._iv_b["Brake"].set(f"{brk_b*100:.0f}%")
            self._iv_b["RPM"].set(f"{row_b.get('rpm',0):.0f}")
            self._iv_b["Lap %"].set(f"{idx_b/(max(1,len(self._dfb)-1))*100:.1f}%")
            self._update_mini_bar(self._bars_b, "Throttle", thr_b)
            self._update_mini_bar(self._bars_b, "Brake", brk_b)

        self._canvas.draw_idle()

    def _tick(self):
        if not self._playing or self._df is None: return
        step = max(1, int(self.SPEEDS[self._si]))
        self._idx = min(self._idx + step, len(self._df)-1)
        self._update()
        if self._idx >= len(self._df)-1:
            self._playing = False
            self._pbtn.config(text="▶  Play", fg=GRN)
        else:
            self._after_id = self._canvas.get_tk_widget().after(1000//self.FPS, self._tick)

    def _toggle(self):
        if self._df is None: return
        self._playing = not self._playing
        if self._playing:
            if self._idx >= len(self._df)-1: self._idx = 0
            self._pbtn.config(text="⏸  Pause", fg=YLW)
            self._tick()
        else:
            self._pbtn.config(text="▶  Play", fg=GRN)

    def _reset(self):
        self._playing = False; self._pbtn.config(text="▶  Play", fg=GRN)
        self._idx = 0; self._update()

    def _spd(self, i): self._si = i

    def _scrub(self, val):
        if self._df is None: return
        self._idx = int(int(val)/1000*(len(self._df)-1))
        self._update()

# ── Main App ──────────────────────────────────────────────────────────────────
class AnalystApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GT7 Lap Analyst")
        self.configure(bg=BG)
        self.geometry("1380x860")
        self.minsize(1100, 700)
        self._da = self._dfa = None
        self._db = self._dfb = None
        self._consensus_line = None
        self._cfigs        = {}
        self._group_names  = []
        self._compare_mode = False
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._quit)

    def _quit(self):
        plt.close("all"); self.destroy()

    def _build(self):
        style = ttk.Style(); style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PNL, foreground=DIM,
                        padding=[14,5], font=FONTB)
        style.map("TNotebook.Tab",
                  background=[("selected", PNL2)],
                  foreground=[("selected", CYN)])

        hdr = tk.Frame(self, bg=PNL2, pady=7); hdr.pack(fill="x")
        tk.Label(hdr, text="GT7", fg=ACC, bg=PNL2,
                 font=("Consolas",15,"bold")).pack(side="left", padx=(16,2))
        tk.Label(hdr, text="LAP ANALYST", fg=CYN, bg=PNL2,
                 font=("Consolas",15,"bold")).pack(side="left")
        self._hdr = tk.Label(hdr, text="", fg=DIM, bg=PNL2, font=FONT)
        self._hdr.pack(side="right", padx=16)

        body = tk.Frame(self, bg=BG); body.pack(fill="both", expand=True)

        side = tk.Frame(body, bg=PNL2, width=236)
        side.pack(side="left", fill="y"); side.pack_propagate(False)
        self._build_sidebar(side)

        nb = ttk.Notebook(body); nb.pack(fill="both", expand=True)
        self._nb = nb

        rep_f = tk.Frame(nb, bg=BG); nb.add(rep_f, text=" ▶  Replay ")
        self._replay = Replay(rep_f)

        chart_f = tk.Frame(nb, bg=BG); nb.add(chart_f, text=" 📊  Charts ")
        self._build_charts(chart_f)

        nb.bind("<<NotebookTabChanged>>", self._on_tab)

    # ── sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self, p):
        def _section(title, color):
            f = tk.Frame(p, bg=PNL, pady=6); f.pack(fill="x", padx=6, pady=(0,4))
            tk.Label(f, text=title, fg=color, bg=PNL, font=FONTB).pack(anchor="w", padx=8)
            return f

        fa = _section("LAP A", CYN)
        self._la = tk.Label(fa, text="not loaded", fg=DIM, bg=PNL, font=FONTL,
                             wraplength=190, justify="left")
        self._la.pack(anchor="w", padx=8, pady=2)
        tk.Button(fa, text="📂  Browse", command=lambda: self._load("a"),
                  bg=DIM2, fg=CYN, relief="flat", font=FONT, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))

        fb = _section("LAP B", ACC)
        self._lb = tk.Label(fb, text="not loaded", fg=DIM, bg=PNL, font=FONTL,
                             wraplength=190, justify="left")
        self._lb.pack(anchor="w", padx=8, pady=2)
        tk.Button(fb, text="📂  Browse", command=lambda: self._load("b"),
                  bg=DIM2, fg=ACC, relief="flat", font=FONT, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))

        fe = _section("EXPORT", DIM)
        tk.Button(fe, text="💾  Export CSV", command=self._export_csv,
                  bg=DIM2, fg=GRN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,2))
        tk.Button(fe, text="🌐  Export Chart HTML", command=self._export_html,
                  bg=DIM2, fg=CYN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))

        fl = _section("LEADERBOARD", YLW)
        tk.Button(fl, text="🏆  Submit to Leaderboard", command=self._submit_leaderboard,
                  bg=DIM2, fg=YLW, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))
        tk.Button(fl, text="🌐  Load Community Line", command=self._load_consensus,
                  bg=DIM2, fg=CYN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))
        self._top10_f = tk.Frame(fl, bg=PNL)
        self._top10_f.pack(fill="x", padx=8, pady=(0,4))
        tk.Label(self._top10_f, text="load a lap to see the top 10", fg=DIM, bg=PNL,
                 font=FONTL, wraplength=190, justify="left").pack(anchor="w")

        tk.Label(p, text="SECTORS — LAP A", fg=DIM, bg=PNL2,
                 font=FONTL).pack(anchor="w", padx=12, pady=(6,2))
        self._sec_f = tk.Frame(p, bg=PNL2); self._sec_f.pack(fill="x", padx=6)

        tk.Label(p, text="STATS — LAP A", fg=DIM, bg=PNL2,
                 font=FONTL).pack(anchor="w", padx=12, pady=(6,2))
        self._sf = tk.Frame(p, bg=PNL2); self._sf.pack(fill="x", padx=6)

        fn = tk.Frame(p, bg=PNL, pady=4); fn.pack(fill="x", padx=6, pady=(6,4))
        tk.Label(fn, text="LAP NOTES", fg=DIM, bg=PNL, font=FONTL).pack(anchor="w", padx=8)
        self._notes = tk.Text(fn, height=5, bg=PNL2, fg=FG, font=FONTL,
                              insertbackground=CYN, relief="flat",
                              wrap="word", padx=4, pady=4)
        self._notes.pack(fill="x", padx=8, pady=4)

    def _update_stats(self, data, df):
        for w in self._sf.winfo_children(): w.destroy()
        for k, v in build_stats(data, df).items():
            r = tk.Frame(self._sf, bg=PNL2); r.pack(fill="x", pady=1)
            tk.Label(r, text=k, fg=DIM, bg=PNL2, font=FONTL, width=13,
                     anchor="w").pack(side="left", padx=4)
            tk.Label(r, text=v, fg=FG, bg=PNL2, font=FONTL,
                     anchor="w").pack(side="left")

    def _update_sectors(self, df):
        for w in self._sec_f.winfo_children(): w.destroy()
        hdr = tk.Frame(self._sec_f, bg=PNL2); hdr.pack(fill="x", pady=1)
        for h, w in [("Sec",4),("Time",8),("Avg",6),("Top",6)]:
            tk.Label(hdr, text=h, fg=DIM, bg=PNL2, font=FONTL, width=w, anchor="w").pack(side="left")
        cols = [CYN, GRN, YLW]
        for i, (s, t, avg, top) in enumerate(build_sector_stats(df)):
            r = tk.Frame(self._sec_f, bg=PNL2); r.pack(fill="x", pady=1)
            tk.Label(r, text=s,   fg=cols[i%3], bg=PNL2, font=FONTL, width=4,  anchor="w").pack(side="left")
            tk.Label(r, text=t,   fg=FG,         bg=PNL2, font=FONTL, width=8,  anchor="w").pack(side="left")
            tk.Label(r, text=avg, fg=FG,         bg=PNL2, font=FONTL, width=6,  anchor="w").pack(side="left")
            tk.Label(r, text=top, fg=FG,         bg=PNL2, font=FONTL, width=6,  anchor="w").pack(side="left")

    # ── chart tab ─────────────────────────────────────────────────────────────
    def _build_charts(self, parent):
        hdr = tk.Frame(parent, bg=PNL, pady=4); hdr.pack(fill="x")
        tk.Label(hdr, text="CHARTS", fg=DIM, bg=PNL, font=FONTB).pack(side="left", padx=12)
        self._cmp_btn = tk.Button(hdr, text="⚡ Compare: OFF",
                                   command=self._toggle_compare,
                                   bg=DIM2, fg=DIM, relief="flat", font=FONTB,
                                   padx=10, pady=2, cursor="hand2")
        self._cmp_btn.pack(side="right", padx=10)

        snb = ttk.Notebook(parent); snb.pack(fill="both", expand=True)
        self._snb = snb
        self._ctabs = {}
        for name, fn, shape, fs in GROUPS:
            f = tk.Frame(snb, bg=BG)
            snb.add(f, text=f" {name} ")
            self._ctabs[name] = (f, fn, fs)
            self._cfigs[name] = None
            self._group_names.append(name)
        snb.bind("<<NotebookTabChanged>>", lambda e: self._draw_active_chart())

    def _toggle_compare(self):
        self._compare_mode = not self._compare_mode
        if self._compare_mode:
            self._cmp_btn.config(text="⚡ Compare: ON", fg=CYN, bg=PNL2)
        else:
            self._cmp_btn.config(text="⚡ Compare: OFF", fg=DIM, bg=DIM2)
        for k in list(self._cfigs):
            if self._cfigs[k]: plt.close(self._cfigs[k])
            self._cfigs[k] = None
        self._draw_active_chart()

    def _draw_active_chart(self):
        if self._dfa is None: return
        idx  = self._snb.index("current")
        name = self._group_names[idx]
        self._draw_chart(name)

    def _draw_chart(self, name):
        if self._dfa is None: return
        f, fn, fs = self._ctabs[name]
        for w in f.winfo_children(): w.destroy()
        if self._cfigs.get(name):
            plt.close(self._cfigs[name])
        fig = plt.figure(figsize=fs, facecolor=BG)
        dfb = self._dfb if self._compare_mode else None
        if name == "Consensus":
            fn(fig, self._dfa, dfb, self._consensus_line)
        else:
            fn(fig, self._dfa, dfb)
        cv = FigureCanvasTkAgg(fig, master=f)
        cv.draw(); cv.get_tk_widget().pack(fill="both", expand=True)
        self._cfigs[name] = fig

    # ── load ──────────────────────────────────────────────────────────────────
    def _load(self, slot):
        path = filedialog.askopenfilename(
            title=f"Load Lap {slot.upper()}",
            filetypes=[("JSON","*.json"),("All","*.*")])
        if not path: return
        try: data, df = load_lap(path)
        except Exception as e: messagebox.showerror("Load Error", str(e)); return
        lbl = lap_label(data)
        if slot == "a":
            self._da, self._dfa = data, df
            self._la.config(text=lbl, fg=CYN)
            self._update_stats(data, df)
            self._update_sectors(df)
            self._replay.load(df, lbl, track=data.get("track", ""))
            self._hdr.config(text=f"A: {lbl}")
            self._refresh_top10()
            for k in list(self._cfigs):
                if self._cfigs[k]: plt.close(self._cfigs[k])
                self._cfigs[k] = None
            self._draw_active_chart()
        else:
            self._db, self._dfb = data, df
            self._lb.config(text=lbl, fg=ACC)
            self._replay.load_b(df, lbl)
            if self._compare_mode:
                for k in list(self._cfigs):
                    if self._cfigs[k]: plt.close(self._cfigs[k])
                    self._cfigs[k] = None
                self._draw_active_chart()

    # ── exports ───────────────────────────────────────────────────────────────
    def _export_csv(self):
        if self._dfa is None:
            messagebox.showinfo("Export", "Load Lap A first."); return

        dlg = tk.Toplevel(self)
        dlg.title("Export CSV"); dlg.configure(bg=PNL2)
        dlg.geometry("265x155"); dlg.resizable(False, False)
        dlg.transient(self); dlg.grab_set()

        tk.Label(dlg, text="Export which lap?", fg=FG, bg=PNL2, font=FONTB).pack(pady=(14,8))
        choice = tk.StringVar(value="a")
        for val, lbl_ in [("a","Lap A"), ("b","Lap B"), ("both","Both (two files)")]:
            tk.Radiobutton(dlg, text=lbl_, variable=choice, value=val,
                            bg=PNL2, fg=FG, selectcolor=DIM2,
                            activebackground=PNL2, activeforeground=CYN,
                            font=FONTL).pack(anchor="w", padx=28, pady=1)

        def _do():
            sel = choice.get(); dlg.destroy()
            if sel in ("a", "both"):
                p = filedialog.asksaveasfilename(title="Save Lap A CSV",
                    defaultextension=".csv", filetypes=[("CSV","*.csv"),("All","*.*")])
                if p:
                    self._dfa.to_csv(p, index=False)
                    messagebox.showinfo("Exported", f"Lap A  →  {Path(p).name}")
            if sel in ("b", "both"):
                if self._dfb is None:
                    messagebox.showinfo("Export", "Lap B not loaded."); return
                p = filedialog.asksaveasfilename(title="Save Lap B CSV",
                    defaultextension=".csv", filetypes=[("CSV","*.csv"),("All","*.*")])
                if p:
                    self._dfb.to_csv(p, index=False)
                    messagebox.showinfo("Exported", f"Lap B  →  {Path(p).name}")

        tk.Button(dlg, text="Export", command=_do,
                   bg=ACC, fg=BG, relief="flat", font=FONTB,
                   padx=14, pady=4, cursor="hand2").pack(pady=8)
        dlg.wait_window()

    def _export_html(self):
        if self._dfa is None:
            messagebox.showinfo("Export", "Load Lap A first."); return
        try:
            idx  = self._snb.index("current")
            name = self._group_names[idx]
        except Exception:
            messagebox.showinfo("Export", "Open a chart tab first."); return
        fig = self._cfigs.get(name)
        if fig is None:
            messagebox.showinfo("Export", "No chart rendered yet."); return
        path = filedialog.asksaveasfilename(
            title="Export Chart as HTML",
            defaultextension=".html",
            filetypes=[("HTML","*.html"),("All","*.*")])
        if not path: return
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=BG)
        b64 = base64.b64encode(buf.getvalue()).decode()
        html = (
            "<!DOCTYPE html><html><head>"
            f"<title>GT7 Analyst — {name}</title>"
            "<style>body{background:#07080f;display:flex;justify-content:center;"
            "align-items:flex-start;min-height:100vh;margin:0;padding:20px;box-sizing:border-box}"
            "img{max-width:100%;border-radius:8px;box-shadow:0 0 30px #00f0d444}"
            "h3{color:#00f0d4;font-family:monospace;text-align:center}</style></head>"
            f"<body><div><h3>GT7 Lap Analyst — {name}</h3>"
            f"<img src='data:image/png;base64,{b64}'></div></body></html>"
        )
        Path(path).write_text(html, encoding="utf-8")
        messagebox.showinfo("Exported", f"Saved: {Path(path).name}")

    def _on_tab(self, e):
        try:
            if self._nb.index("current") == 1:
                self._draw_active_chart()
        except Exception: pass

    # ── leaderboard ───────────────────────────────────────────────
    def _refresh_top10(self):
        if self._da is None: return
        car   = self._da.get("car_display")   or self._da.get("car", "?")
        track = self._da.get("track_display") or self._da.get("track", "?")
        for w in self._top10_f.winfo_children(): w.destroy()
        tk.Label(self._top10_f, text="loading top 10…", fg=DIM, bg=PNL,
                 font=FONTL).pack(anchor="w")

        def worker():
            rows = leaderboard.get_top_laps(car, track, n=10)
            self.after(0, lambda: self._render_top10(car, track, rows))

        threading.Thread(target=worker, daemon=True).start()

    def _render_top10(self, car, track, rows):
        for w in self._top10_f.winfo_children(): w.destroy()
        tk.Label(self._top10_f, text=f"{car} @ {track}", fg=CYN, bg=PNL,
                 font=FONTL, wraplength=190, justify="left").pack(anchor="w")
        if not rows:
            tk.Label(self._top10_f, text="no times yet — be the first!",
                     fg=DIM, bg=PNL, font=FONTL, wraplength=190,
                     justify="left").pack(anchor="w", pady=(2,0))
            return
        for i, row in enumerate(rows, 1):
            ms = row.get("lap_time_ms", 0)
            m, s = divmod(ms / 1000.0, 60)
            time_str = f"{int(m)}:{s:06.3f}"
            name = row.get("psn_name", "?")
            r = tk.Frame(self._top10_f, bg=PNL); r.pack(fill="x", pady=1)
            col = YLW if i == 1 else (FG if i <= 3 else DIM)
            tk.Label(r, text=f"{i}.", fg=col, bg=PNL, font=FONTL, width=3, anchor="w").pack(side="left")
            tk.Label(r, text=time_str, fg=col, bg=PNL, font=FONTL, width=10, anchor="w").pack(side="left")
            tk.Label(r, text=name, fg=col, bg=PNL, font=FONTL, anchor="w").pack(side="left", fill="x", expand=True)
            tk.Button(r, text="⬇", command=lambda row=row: self._download_ghost(row),
                      bg=DIM2, fg=CYN, relief="flat", font=FONTL, padx=4, pady=0,
                      cursor="hand2").pack(side="right", padx=(2,4))

    def _load_consensus(self):
        if self._da is None:
            messagebox.showinfo("Community Line", "Load Lap A first."); return
        car   = self._da.get("car_display")   or self._da.get("car", "?")
        track = self._da.get("track_display") or self._da.get("track", "?")

        def worker():
            rows = leaderboard.get_consensus_line(car, track, n=10)
            self.after(0, lambda: self._after_consensus(rows))

        threading.Thread(target=worker, daemon=True).start()

    def _after_consensus(self, rows):
        if not rows:
            messagebox.showinfo("Community Line",
                                 "No community data yet for this car+track (or couldn't reach the server).")
            return
        self._consensus_line = rows
        for k in list(self._cfigs):
            if self._cfigs[k]: plt.close(self._cfigs[k])
            self._cfigs[k] = None
        self._draw_active_chart()

    def _download_ghost(self, row):
        """Download one leaderboard lap's samples and load it as Lap B --
        same downstream path (Replay.load_b / self._dfb) as browsing a local
        JSON file, just sourced from Supabase (Phase 3)."""
        lap_id = row.get("id")
        if lap_id is None:
            messagebox.showerror("Ghost Download", "This row has no lap id -- can't download it.")
            return
        threading.Thread(target=self._download_ghost_worker, args=(row,), daemon=True).start()

    def _download_ghost_worker(self, row):
        samples = leaderboard.get_lap_samples(row["id"])
        if not samples:
            self.after(0, lambda: messagebox.showerror(
                "Ghost Download", "Couldn't download that lap -- check your internet connection and try again."))
            return
        data = {
            "car": row.get("car_name", "?"),
            "track": row.get("track_name", "?"),
            "lap_time_s": row.get("lap_time_ms", 0) / 1000.0,
            "samples": samples,
        }
        try:
            _, dfb = load_lap_data(data)
        except Exception as e:
            msg = str(e)
            self.after(0, lambda: messagebox.showerror("Ghost Download", msg))
            return
        self.after(0, lambda: self._load_ghost_result(data, dfb, row))

    def _load_ghost_result(self, data, dfb, row):
        lbl = f"{row.get('psn_name','?')} (ghost) -- {lap_label(data, short=True)}"
        self._db, self._dfb = data, dfb
        self._lb.config(text=lbl, fg=ACC)
        self._replay.load_b(dfb, lbl)
        if self._compare_mode:
            for k in list(self._cfigs):
                if self._cfigs[k]: plt.close(self._cfigs[k])
                self._cfigs[k] = None
            self._draw_active_chart()

    def _submit_leaderboard(self):
        if self._da is None:
            messagebox.showinfo("Submit to Leaderboard", "Load Lap A first."); return
        car   = self._da.get("car_display")   or self._da.get("car", "?")
        track = self._da.get("track_display") or self._da.get("track", "?")
        lap_time_ms = int(round(self._da.get("lap_time_s", 0) * 1000))
        samples = self._da.get("samples", [])
        if not samples:
            messagebox.showinfo("Submit to Leaderboard", "This lap has no samples to submit."); return

        if not runtime_config.SUPABASE_ACCESS_TOKEN:
            if not self._prompt_create_account():
                return

        psn = simpledialog.askstring(
            "Submit to Leaderboard",
            f"Submitting: {car} @ {track}  —  {lap_time_ms/1000:.3f}s\n\nPSN name:",
            initialvalue=runtime_config.PSN_NAME, parent=self)
        if not psn: return
        psn = psn.strip()
        if not psn: return
        runtime_config.PSN_NAME = psn
        runtime_config.save(PSN_NAME=psn)

        def worker():
            ok, reason = leaderboard.submit_lap(
                car, track, lap_time_ms, psn, samples,
                access_token=runtime_config.SUPABASE_ACCESS_TOKEN,
                user_id=runtime_config.SUPABASE_USER_ID)
            self.after(0, lambda: self._after_submit(ok, reason))

        threading.Thread(target=worker, daemon=True).start()

    def _prompt_create_account(self) -> bool:
        """Submit to Leaderboard was hit with no signed-in session (the user
        skipped onboarding earlier) -- offer the same anonymous sign-up flow
        as launcher.py's onboarding screen, inline, instead of just failing
        on submit. Returns True once a session exists (freshly created),
        False if the user cancelled or sign-up failed."""
        win = tk.Toplevel(self)
        win.title("Create a Free Account")
        win.configure(bg=PNL)
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        result = {"ok": False}

        tk.Label(win, text="Create a free account to submit laps",
                 font=FONTB, fg=YLW, bg=PNL).pack(padx=24, pady=(18, 6))
        tk.Label(win, text="Just a display name -- no email or password needed.",
                 font=FONTL, fg=DIM, bg=PNL).pack(padx=24)

        name_var = tk.StringVar(value=runtime_config.PSN_NAME)
        entry = tk.Entry(win, textvariable=name_var, font=FONT, width=24,
                          bg=PNL2, fg=FG, insertbackground=CYN, relief="flat",
                          justify="center")
        entry.pack(ipady=5, padx=24, pady=14)
        entry.focus_set()

        status = tk.Label(win, text="", font=FONTL, fg=ACC, bg=PNL,
                           wraplength=260, justify="center")
        status.pack(padx=24, pady=(0, 6))

        btn_row = tk.Frame(win, bg=PNL)
        btn_row.pack(pady=(0, 18))

        create_btn = tk.Button(btn_row, text="Create Account", font=FONTB,
                                bg=CYN, fg=PNL, bd=0, padx=12, pady=6,
                                cursor="hand2")
        create_btn.pack(side="left", padx=6)
        cancel_btn = tk.Button(btn_row, text="Cancel", font=FONT, bg=DIM2,
                                fg=FG, bd=0, padx=12, pady=6, cursor="hand2")
        cancel_btn.pack(side="left", padx=6)

        def set_busy(busy):
            state = "disabled" if busy else "normal"
            create_btn.config(state=state)
            cancel_btn.config(state=state)

        def do_create():
            name = name_var.get().strip()
            if not name:
                status.config(text="Enter a display name first.")
                return
            set_busy(True)
            status.config(text="Creating account…", fg=DIM)

            def worker():
                session = auth.sign_up_anonymous()
                name_saved = False
                if session:
                    name_saved = auth.set_display_name(session["access_token"], session["user_id"], name)
                win.after(0, lambda: after_create(session, name, name_saved))

            threading.Thread(target=worker, daemon=True).start()

        def after_create(session, name, name_saved=True):
            if not session:
                set_busy(False)
                status.config(text="Couldn't reach the server -- try again.", fg=ACC)
                return
            runtime_config.SUPABASE_ACCESS_TOKEN = session["access_token"]
            runtime_config.SUPABASE_REFRESH_TOKEN = session["refresh_token"]
            runtime_config.SUPABASE_USER_ID = session["user_id"]
            runtime_config.PSN_NAME = name
            runtime_config.ONBOARDING_DONE = True
            runtime_config.save(
                SUPABASE_ACCESS_TOKEN=session["access_token"],
                SUPABASE_REFRESH_TOKEN=session["refresh_token"],
                SUPABASE_USER_ID=session["user_id"],
                PSN_NAME=name,
                ONBOARDING_DONE=True,
            )
            result["ok"] = True
            if name_saved:
                win.destroy()
                return
            # Same known Supabase issue as launcher.py's onboarding screen --
            # account/session is fine, just the display-name sync failed.
            # Say so briefly instead of closing as if nothing happened.
            set_busy(False)
            status.config(
                text="Account created — display name didn't sync (known "
                     "Supabase issue). Continuing anyway...", fg=ACC)
            win.after(1800, win.destroy)

        create_btn.config(command=do_create)
        cancel_btn.config(command=win.destroy)

        self.wait_window(win)
        return result["ok"]

    def _after_submit(self, ok, reason="network"):
        if ok:
            messagebox.showinfo("Submit to Leaderboard",
                                 "Submitted! If it's a new top time it'll appear on the leaderboard shortly.")
            self._refresh_top10()
        elif reason == "server":
            messagebox.showerror("Submit to Leaderboard",
                                  "The server rejected the submission -- this is a server-side issue, "
                                  "not your connection. Try again later.")
        else:
            messagebox.showerror("Submit to Leaderboard",
                                  "Couldn't reach the leaderboard — check your internet connection and try again.")

if __name__ == "__main__":
    app = AnalystApp()
    app.mainloop()
