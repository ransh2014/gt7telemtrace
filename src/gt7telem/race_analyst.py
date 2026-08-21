# race_analyst.py — GT7 Race Analyst
# pip install pandas matplotlib numpy
import base64
import io
import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("TkAgg")
import warnings

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import LineCollection
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Polygon as MplPolygon

warnings.filterwarnings("ignore")

# ── Theme (matches lap_analyst.py) ─────────────────────────────────────────────
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

X = "t"          # race charts are plotted against elapsed race time, not track_position
                  # (track_position resets every lap, which would zig-zag across a race)

# ── Data ──────────────────────────────────────────────────────────────────────
def load_race(path):
    with open(path) as f: data = json.load(f)
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
                "heading","track_position","lap_number","current_position",
                "total_positions","in_pit","t"]:
        if col not in df: df[col] = 0.0
    df = df.fillna(0)
    df = df.sort_values("t").reset_index(drop=True)
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
    df["pit_flag"]     = df["in_pit"].astype(float)
    return data, df

def fmt_dur(t):
    m = int(t // 60); s = t % 60
    return f"{m}:{s:06.3f}"

def race_label(data, short=False):
    car   = data.get("car", "?")
    track = data.get("track", "?").replace("_", " ").title()
    dur   = data.get("race_duration_s", 0)
    if short: return f"{car}  {fmt_dur(dur)}"
    return f"{car} @ {track}  {fmt_dur(dur)}"

def get_lap_segments(df):
    """Split race samples into per-lap segments using lap_number transitions."""
    if df["lap_number"].abs().max() == 0:
        return [df]
    segs = []
    cur_lap = df["lap_number"].iloc[0]
    start = 0
    for i in range(1, len(df)):
        if df["lap_number"].iloc[i] != cur_lap:
            segs.append(df.iloc[start:i])
            start = i
            cur_lap = df["lap_number"].iloc[i]
    segs.append(df.iloc[start:])
    return [s for s in segs if len(s) >= 3]

def lap_split_stats(df):
    """Returns list of dicts: lap, time_s, avg_speed, top_speed, brake%, throttle%, max_lat_g"""
    segs = get_lap_segments(df)
    out = []
    for i, seg in enumerate(segs, 1):
        lap_time = float(seg["t"].iloc[-1] - seg["t"].iloc[0])
        out.append({
            "lap": int(seg["lap_number"].iloc[0]) or i,
            "time_s": lap_time,
            "avg_speed": float(seg["speed_kmh"].mean()),
            "top_speed": float(seg["speed_kmh"].max()),
            "brake_pct": float(seg["brake"].mean() * 100),
            "throttle_pct": float(seg["throttle"].mean() * 100),
            "max_lat_g": float(seg["lat_g"].abs().max()),
        })
    return out

def count_pit_stops(df):
    transitions = (df["pit_flag"].diff() > 0).sum()
    return int(transitions)

def build_stats(data, df):
    splits = lap_split_stats(df)
    lap_times = [s["time_s"] for s in splits if s["time_s"] > 1]
    best_lap = min(lap_times) if lap_times else 0
    avg_lap  = float(np.mean(lap_times)) if lap_times else 0
    fuel_used = df["fuel_remaining"].iloc[0] - df["fuel_remaining"].iloc[-1]
    dur = data.get("race_duration_s", df["t"].iloc[-1] - df["t"].iloc[0])
    return {
        "Duration":     fmt_dur(dur),
        "Laps":         f"{len(splits)}",
        "Best Lap":     fmt_dur(best_lap) if best_lap else "--",
        "Avg Lap":      fmt_dur(avg_lap) if avg_lap else "--",
        "Samples":      f"{len(df)}",
        "Top Speed":    f"{df['speed_kmh'].max():.1f} km/h",
        "Avg Speed":    f"{df['speed_kmh'].mean():.1f} km/h",
        "Full Thr%":    f"{(df['throttle']>0.95).mean()*100:.1f}%",
        "Coasting%":    f"{df['coasting'].mean()*100:.1f}%",
        "Max Lat G":    f"{df['lat_g'].abs().max():.2f}g",
        "Max Long G":   f"{df['long_g'].abs().max():.2f}g",
        "Fuel Used":    f"{fuel_used:.2f}",
        "Pit Stops":    f"{count_pit_stops(df)}",
        "Best Pos":     f"{int(df['current_position'][df['current_position']>0].min())}"
                        if (df['current_position']>0).any() else "--",
    }

# ── Chart helpers ─────────────────────────────────────────────────────────────
def _ax(ax, title, xl="Race Time (s)", yl=""):
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
    """Overlay race B — dashed ACC line."""
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
    lc   = LineCollection(segs, cmap=cmap, norm=norm, lw=1.4, alpha=0.75)
    lc.set_array(v)
    ax.add_collection(lc); ax.autoscale(); ax.set_aspect("equal")
    ax.set_title(title, color=FG, fontsize=9); ax.axis("off")
    cb = plt.colorbar(lc, ax=ax, pad=0.02, fraction=0.04)
    cb.ax.tick_params(labelsize=6, colors=DIM)

# ── Chart groups (time-series ones use X = "t" instead of track_position) ─────
def draw_inputs(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = X
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
    axs[2,2].scatter(df["throttle"]*100, df["speed_kmh"], c=C["throttle"], s=1, alpha=0.25)
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
        axs[2,2].scatter(dfb["throttle"]*100, dfb["speed_kmh"], c=ACC, s=1, alpha=0.18)

def draw_engine(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = X
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
    x = X
    for col,lbl,c in [("tyre_temp_fl","FL",C["fl"]),("tyre_temp_fr","FR",C["fr"]),
                       ("tyre_temp_rl","RL",C["rl"]),("tyre_temp_rr","RR",C["rr"])]:
        axs[0,0].plot(df[x], df[col], color=c, lw=1.2, label=lbl)
    _ax(axs[0,0], "All Tyre Temps", yl="°C"); axs[0,0].legend(fontsize=7)
    axs[0,0].axhspan(80, 100, alpha=0.07, color=GRN)
    axs[0,1].plot(df[x], df["tyre_temp_fl"], color=C["fl"], lw=1.3, label="FL")
    axs[0,1].plot(df[x], df["tyre_temp_fr"], color=C["fr"], lw=1.3, label="FR")
    _ax(axs[0,1], "Front Temps °C"); axs[0,1].legend(fontsize=7)
    axs[0,1].axhspan(80, 100, alpha=0.07, color=GRN)
    axs[0,2].plot(df[x], df["tyre_temp_rl"], color=C["rl"], lw=1.3, label="RL")
    axs[0,2].plot(df[x], df["tyre_temp_rr"], color=C["rr"], lw=1.3, label="RR")
    _ax(axs[0,2], "Rear Temps °C"); axs[0,2].legend(fontsize=7)
    axs[0,2].axhspan(80, 100, alpha=0.07, color=GRN)
    axs[1,0].plot(df[x], df["front_t_avg"], color=C["fl"], lw=1.3, label="Front avg")
    axs[1,0].plot(df[x], df["rear_t_avg"],  color=C["rl"], lw=1.3, label="Rear avg")
    _ax(axs[1,0], "Front vs Rear Avg °C"); axs[1,0].legend(fontsize=7)
    for col,lbl,c in [("tyre_slip_fl","FL",C["fl"]),("tyre_slip_fr","FR",C["fr"]),
                       ("tyre_slip_rl","RL",C["rl"]),("tyre_slip_rr","RR",C["rr"])]:
        axs[1,1].plot(df[x], df[col], color=c, lw=1.0, label=lbl)
    axs[1,1].axhline(1.0, color=DIM, lw=0.8, ls="--")
    _ax(axs[1,1], "All Slip Ratios", yl="Slip"); axs[1,1].legend(fontsize=7)
    axs[1,2].plot(df[x], df["tyre_slip_fl"], color=C["fl"], lw=1.3, label="FL")
    axs[1,2].plot(df[x], df["tyre_slip_fr"], color=C["fr"], lw=1.3, label="FR")
    axs[1,2].axhline(1.0, color=DIM, lw=0.8, ls="--")
    _ax(axs[1,2], "Front Slip"); axs[1,2].legend(fontsize=7)
    axs[2,0].plot(df[x], df["tyre_slip_rl"], color=C["rl"], lw=1.3, label="RL")
    axs[2,0].plot(df[x], df["tyre_slip_rr"], color=C["rr"], lw=1.3, label="RR")
    axs[2,0].axhline(1.0, color=DIM, lw=0.8, ls="--")
    _ax(axs[2,0], "Rear Slip"); axs[2,0].legend(fontsize=7)
    _L(axs[2,1], df, x, "lr_t_bal", YLW); axs[2,1].axhline(0, color=DIM, lw=0.8, ls="--")
    _ax(axs[2,1], "L−R Balance °C", yl="Left − Right")
    axs[2,2].scatter(df["speed_kmh"], df["tyre_slip_rr"], s=1.2, alpha=0.25, color=C["rr"])
    axs[2,2].axhline(1.0, color=DIM, lw=0.8, ls="--")
    _ax(axs[2,2], "Rear-R Slip vs Speed", xl="km/h", yl="Slip"); axs[2,2].grid(False)
    if dfb is not None:
        for col in ["tyre_temp_fl","tyre_temp_fr","tyre_temp_rl","tyre_temp_rr"]:
            axs[0,0].plot(dfb[x], dfb[col], lw=0.8, alpha=0.4, ls="--", color=DIM)
        _Lb(axs[0,1], dfb, x, "tyre_temp_fl"); _Lb(axs[0,1], dfb, x, "tyre_temp_fr")
        _Lb(axs[0,2], dfb, x, "tyre_temp_rl"); _Lb(axs[0,2], dfb, x, "tyre_temp_rr")
        _Lb(axs[1,0], dfb, x, "front_t_avg"); _Lb(axs[1,0], dfb, x, "rear_t_avg")
        for col in ["tyre_slip_fl","tyre_slip_fr","tyre_slip_rl","tyre_slip_rr"]:
            axs[1,1].plot(dfb[x], dfb[col], lw=0.8, alpha=0.3, ls="--", color=DIM)
        _Lb(axs[1,2], dfb, x, "tyre_slip_fl"); _Lb(axs[1,2], dfb, x, "tyre_slip_fr")
        _Lb(axs[2,0], dfb, x, "tyre_slip_rl"); _Lb(axs[2,0], dfb, x, "tyre_slip_rr")
        _Lb(axs[2,1], dfb, x, "lr_t_bal")
        axs[2,2].scatter(dfb["speed_kmh"], dfb["tyre_slip_rr"], s=1.2, alpha=0.18, color=ACC)

def draw_dynamics(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = X
    for col,lbl,c in [("susp_fl","FL",C["fl"]),("susp_fr","FR",C["fr"]),
                       ("susp_rl","RL",C["rl"]),("susp_rr","RR",C["rr"])]:
        axs[0,0].plot(df[x], df[col], color=c, lw=1.0, label=lbl)
    _ax(axs[0,0], "All Suspension mm"); axs[0,0].legend(fontsize=7)
    axs[0,1].plot(df[x], df["susp_fl"], color=C["fl"], lw=1.3, label="FL")
    axs[0,1].plot(df[x], df["susp_fr"], color=C["fr"], lw=1.3, label="FR")
    _ax(axs[0,1], "Front Suspension mm"); axs[0,1].legend(fontsize=7)
    axs[0,2].plot(df[x], df["susp_rl"], color=C["rl"], lw=1.3, label="RL")
    axs[0,2].plot(df[x], df["susp_rr"], color=C["rr"], lw=1.3, label="RR")
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
            axs[0,0].plot(dfb[x], dfb[col], lw=0.8, alpha=0.4, ls="--", color=DIM)
        _Lb(axs[0,1], dfb, x, "susp_fl"); _Lb(axs[0,1], dfb, x, "susp_fr")
        _Lb(axs[0,2], dfb, x, "susp_rl"); _Lb(axs[0,2], dfb, x, "susp_rr")
        _Lb(axs[1,0], dfb, x, "ride_height_mm")
        _Lb(axs[1,1], dfb, x, "ang_y"); _Lb(axs[1,2], dfb, x, "vel_x")
        _Lb(axs[2,0], dfb, x, "vel_y"); _Lb(axs[2,1], dfb, x, "long_g")
        _Lb(axs[2,2], dfb, x, "lat_g")

def draw_maps(fig, df, dfb=None):
    axs = fig.subplots(2, 2); fig.subplots_adjust(hspace=0.3, wspace=0.25)
    has = df["world_x"].abs().max() > 1
    if not has:
        for ax in axs.flat:
            ax.text(0.5,0.5,"No GPS data in this race",ha="center",va="center",color=DIM,fontsize=11)
            ax.axis("off")
        return
    _track_map(axs[0,0], df, "speed_kmh",     "turbo",  "Speed (km/h) — all laps")
    _track_map(axs[0,1], df, "throttle",       "Greens", "Throttle — all laps")
    _track_map(axs[1,0], df, "brake",          "Reds",   "Brake — all laps")
    gf = df.copy(); gf["gear_f"] = df["gear"].astype(float)
    _track_map(axs[1,1], gf, "gear_f",         "plasma", "Gear — all laps")

def draw_gforce(fig, df, dfb=None):
    axs = fig.subplots(2, 2); fig.subplots_adjust(hspace=0.42, wspace=0.35)
    x = X
    _L(axs[0,0], df, x, "long_g", YLW); axs[0,0].axhline(0,color=DIM,lw=0.8,ls="--")
    axs[0,0].fill_between(df[x],df["long_g"],0,where=df["long_g"]>0,alpha=0.15,color=GRN)
    axs[0,0].fill_between(df[x],df["long_g"],0,where=df["long_g"]<0,alpha=0.15,color=ACC)
    _ax(axs[0,0], "Longitudinal G", yl="g")
    _L(axs[0,1], df, x, "lat_g", PRP); axs[0,1].axhline(0,color=DIM,lw=0.8,ls="--")
    _ax(axs[0,1], "Lateral G", yl="g")
    sc = axs[1,0].scatter(df["lat_g"], df["long_g"], c=df["speed_kmh"],
                           cmap="turbo", s=1.5, alpha=0.3)
    axs[1,0].axhline(0,color=DIM,lw=0.5); axs[1,0].axvline(0,color=DIM,lw=0.5)
    th = np.linspace(0, 2*np.pi, 100)
    for r in [1.0, 2.0]: axs[1,0].plot(np.cos(th)*r, np.sin(th)*r, color=DIM, lw=0.6, ls="--")
    _ax(axs[1,0], "G-G Diagram", xl="Lat G", yl="Long G"); axs[1,0].grid(False)
    plt.colorbar(sc, ax=axs[1,0], fraction=0.04, pad=0.02).ax.tick_params(labelsize=6, colors=DIM)
    _L(axs[1,1], df, x, "total_g", CYN, fill=True); _ax(axs[1,1], "Total G", yl="g")
    if dfb is not None:
        _Lb(axs[0,0], dfb, x, "long_g"); _Lb(axs[0,1], dfb, x, "lat_g")
        axs[1,0].scatter(dfb["lat_g"], dfb["long_g"], c=ACC, s=1.2, alpha=0.18)
        _Lb(axs[1,1], dfb, x, "total_g")

def draw_fuel(fig, df, dfb=None):
    axs = fig.subplots(2, 3); fig.subplots_adjust(hspace=0.52, wspace=0.4)
    x = X
    _L(axs[0,0], df, x, "fuel_remaining", GRN, fill=True); _ax(axs[0,0], "Fuel Remaining",  yl="L/kWh")
    refuels = df[df["fuel_remaining"].diff() > 1]
    if len(refuels):
        axs[0,0].scatter(refuels[x], refuels["fuel_remaining"], c=YLW, s=20, zorder=5, label="Refuel")
        axs[0,0].legend(fontsize=6)
    _L(axs[0,1], df, x, "fuel_burn",      ORG);            _ax(axs[0,1], "Fuel Burn Rate",   yl="L/s")
    axs[0,2].fill_between(df[x], df["coasting"], alpha=0.6, color=DIM2, step="post")
    _ax(axs[0,2], "Coasting Zones", yl="1=coasting")
    axs[1,0].hist(df["throttle"]*100, bins=30, color=C["throttle"], alpha=0.85, edgecolor="none", label="A")
    _ax(axs[1,0], "Throttle Distribution", xl="%", yl="Count"); axs[1,0].grid(False)
    axs[1,1].hist(df["brake"]*100, bins=30, color=C["brake"], alpha=0.85, edgecolor="none", label="A")
    _ax(axs[1,1], "Brake Distribution", xl="%", yl="Count"); axs[1,1].grid(False)
    axs[1,2].scatter(df["brake"]*100, df["speed_kmh"], s=1.2, alpha=0.25, color=C["brake"])
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
        axs[1,2].scatter(dfb["brake"]*100, dfb["speed_kmh"], s=1.2, alpha=0.18, color=ACC)

def draw_braking(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)
    x = X
    braking = df[df["brake"] > 0.05]

    _L(axs[0,0], df, x, "brake", C["brake"], fill=True); _ax(axs[0,0], "Brake Trace", yl="0–1")

    axs[0,1].hist(braking["brake"]*100 if len(braking) else [0],
                  bins=30, color=C["brake"], alpha=0.85, edgecolor="none", label="A")
    _ax(axs[0,1], "Brake Pressure Dist", xl="Brake %", yl="Count"); axs[0,1].grid(False)

    axs[0,2].plot(df[x], df["throttle"]*100, color=GRN, lw=1.0, alpha=0.8, label="Thr")
    axs[0,2].plot(df[x], df["brake"]*100,    color=ACC, lw=1.3,             label="Brk")
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
    axs[1,2].plot(df[x], df["speed_kmh"], color=CYN, lw=0.6, alpha=0.3)
    if len(brake_starts):
        axs[1,2].scatter(brake_starts[x], brake_starts["speed_kmh"], c=ACC, s=10, alpha=0.6, zorder=3)
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
        axs[2,1].scatter(df.loc[release_rate.index, x], release_rate, s=3, color=ORG, alpha=0.5)
    _ax(axs[2,1], "Brake Release Rate", xl="Race Time (s)", yl="Δbrake/s")

    if df["tyre_slip_rl"].abs().max() > 0.01 and len(braking) > 10:
        avg_rear = (braking["tyre_slip_rl"] + braking["tyre_slip_rr"]) / 2
        axs[2,2].scatter(braking["brake"]*100, avg_rear, s=1.5, alpha=0.25, color=PRP)
        axs[2,2].axhline(1.0, color=DIM, lw=0.8, ls="--")
        _ax(axs[2,2], "Rear Slip vs Brake %", xl="Brake %", yl="Slip"); axs[2,2].grid(False)
    else:
        axs[2,2].text(0.5,0.5,"No slip data",ha="center",va="center",color=DIM)
        axs[2,2].axis("off")

    if dfb is not None:
        _Lb(axs[0,0], dfb, x, "brake")
        _Lb(axs[1,1], dfb, x, "long_g")
        axs[1,2].plot(dfb[x], dfb["speed_kmh"], color=ACC, lw=0.5, alpha=0.18)
        brk_b = dfb[dfb["brake"] > 0.05]
        if len(brk_b):
            axs[0,1].hist(brk_b["brake"]*100, bins=30, color=ACC, alpha=0.5,
                          edgecolor="none", label="B", histtype="step", lw=1.5)
            axs[0,1].legend(fontsize=7)

# ── Laps (replaces lap_analyst's "Sectors" — splits per lap, not per track third) ──
def draw_laps(fig, df, dfb=None):
    splits_a = lap_split_stats(df)
    splits_b = lap_split_stats(dfb) if dfb is not None else None

    t_a    = [s["time_s"] for s in splits_a]
    avg_a  = [s["avg_speed"] for s in splits_a]
    top_a  = [s["top_speed"] for s in splits_a]
    brk_a  = [s["brake_pct"] for s in splits_a]
    thr_a  = [s["throttle_pct"] for s in splits_a]
    latg_a = [s["max_lat_g"] for s in splits_a]
    na = len(t_a)

    t_b = avg_b = top_b = brk_b = thr_b = latg_b = None
    if splits_b:
        t_b    = [s["time_s"] for s in splits_b]
        avg_b  = [s["avg_speed"] for s in splits_b]
        top_b  = [s["top_speed"] for s in splits_b]
        brk_b  = [s["brake_pct"] for s in splits_b]
        thr_b  = [s["throttle_pct"] for s in splits_b]
        latg_b = [s["max_lat_g"] for s in splits_b]

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
        _ax(ax, title, xl="Lap", yl=yl)
        if nb_ <= 20: ax.set_xticks(xs)

    bars2(axs[0,0], t_a,    t_b,    "Lap Times",      yl="s")
    bars2(axs[0,1], avg_a,  avg_b,  "Avg Speed/Lap",  yl="km/h", col_a=CYN)
    bars2(axs[0,2], top_a,  top_b,  "Top Speed/Lap",  yl="km/h", col_a=GRN)

    if t_b is not None:
        nb_ = min(len(t_a), len(t_b))
        delta = [tb - ta for ta, tb in zip(t_a[:nb_], t_b[:nb_])]
        cols_d = [GRN if d < 0 else ACC for d in delta]
        xs = list(range(1, nb_+1))
        axs[1,0].bar(xs, delta, color=cols_d, alpha=0.88, width=0.65)
        axs[1,0].axhline(0, color=DIM, lw=0.8, ls="--")
        axs[1,0].set_title("Lap Δ B−A", color=FG, fontsize=8)
        axs[1,0].set_xlabel("Lap", fontsize=7, color=DIM)
        axs[1,0].set_ylabel("Δs", fontsize=7, color=DIM)
        axs[1,0].grid(True, alpha=0.4)
        if nb_ <= 20: axs[1,0].set_xticks(xs)
    else:
        cum = list(np.cumsum(t_a))
        lnums = list(range(1, na+1))
        axs[1,0].plot(lnums, cum, color=CYN, marker="o", ms=5, lw=1.5)
        _ax(axs[1,0], "Cumulative Race Time", xl="Lap", yl="s")
        if na <= 20: axs[1,0].set_xticks(lnums)

    heat = np.array(avg_a[:na]).reshape(1, -1) if na else np.zeros((1,1))
    im = axs[1,1].imshow(heat, aspect="auto", cmap="turbo", extent=[0.5, max(na,1)+0.5, 0, 1])
    for i, v in enumerate(avg_a):
        axs[1,1].text(i+1, 0.5, f"L{i+1}\n{v:.0f}", ha="center", va="center",
                      color="white", fontsize=6, fontweight="bold")
    axs[1,1].set_yticks([]); axs[1,1].set_xticks([])
    axs[1,1].set_title("Speed Heatmap by Lap", color=FG, fontsize=9)
    plt.colorbar(im, ax=axs[1,1], fraction=0.04).ax.tick_params(labelsize=5, colors=DIM)

    bars2(axs[1,2], brk_a, brk_b, "Avg Brake%/Lap",   yl="%", col_a=ACC, col_b=PRP)

    has = df["world_x"].abs().max() > 1
    if has:
        segs_a = get_lap_segments(df)
        n_show = min(len(segs_a), 10)  # keep the overlay legible
        seg_colors = [plt.cm.tab10(i / max(n_show-1, 1)) for i in range(n_show)]
        for i in range(n_show):
            seg = segs_a[i]
            xi = seg["world_x"].values; zi = seg["world_z"].values
            axs[2,0].plot(xi, zi, color=seg_colors[i], lw=1.4, alpha=0.85)
        axs[2,0].set_aspect("equal"); axs[2,0].axis("off")
        axs[2,0].set_title(f"Lap Lines (first {n_show})", color=FG, fontsize=9)
    else:
        axs[2,0].text(0.5,0.5,"No GPS data",ha="center",va="center",color=DIM,fontsize=10)
        axs[2,0].axis("off")

    bars2(axs[2,1], thr_a,  thr_b,  "Avg Throttle%/Lap", yl="%", col_a=GRN, col_b=YLW)
    bars2(axs[2,2], latg_a, latg_b, "Max |Lat G|/Lap",   yl="g", col_a=PRP, col_b=ORG)

# ── Race overview (new — position, pit stops, fuel over the whole race) ────────
def draw_race(fig, df, dfb=None):
    axs = fig.subplots(2, 2); fig.subplots_adjust(hspace=0.45, wspace=0.32)
    x = X

    has_pos = (df["current_position"] > 0).any()
    if has_pos:
        axs[0,0].step(df[x], df["current_position"], color=CYN, lw=1.5, where="post", label="A")
        if dfb is not None and (dfb["current_position"] > 0).any():
            axs[0,0].step(dfb[x], dfb["current_position"], color=ACC, lw=1.3, alpha=0.75,
                          ls="--", where="post", label="B")
            axs[0,0].legend(fontsize=7)
        axs[0,0].invert_yaxis()
        _ax(axs[0,0], "Position Over Race", yl="Position")
    else:
        axs[0,0].text(0.5,0.5,"No position data",ha="center",va="center",color=DIM)
        axs[0,0].axis("off")

    axs[0,1].fill_between(df[x], df["pit_flag"], alpha=0.6, color=ORG, step="post", label="A")
    if dfb is not None:
        axs[0,1].fill_between(dfb[x], dfb["pit_flag"]*0.6, alpha=0.4, color=ACC, step="post", label="B")
        axs[0,1].legend(fontsize=7)
    axs[0,1].set_ylim(-0.05, 1.05)
    _ax(axs[0,1], f"Pit Stops (A: {count_pit_stops(df)})", yl="In Pit")

    _L(axs[1,0], df, x, "fuel_remaining", GRN, fill=True)
    refuels = df[df["fuel_remaining"].diff() > 1]
    if len(refuels):
        axs[1,0].scatter(refuels[x], refuels["fuel_remaining"], c=YLW, s=18, zorder=5)
    if dfb is not None: _Lb(axs[1,0], dfb, x, "fuel_remaining")
    _ax(axs[1,0], "Fuel Over Race", yl="L/kWh")

    splits = lap_split_stats(df)
    if splits:
        lap_nums = list(range(1, len(splits)+1))
        lap_ts   = [s["time_s"] for s in splits]
        axs[1,1].plot(lap_nums, lap_ts, color=CYN, marker="o", ms=4, lw=1.3, label="A")
        if dfb is not None:
            splits_b = lap_split_stats(dfb)
            if splits_b:
                lap_nums_b = list(range(1, len(splits_b)+1))
                lap_ts_b   = [s["time_s"] for s in splits_b]
                axs[1,1].plot(lap_nums_b, lap_ts_b, color=ACC, marker="s", ms=4, lw=1.1,
                              ls="--", alpha=0.8, label="B")
                axs[1,1].legend(fontsize=7)
        _ax(axs[1,1], "Lap Time Trend", xl="Lap", yl="s")
    else:
        axs[1,1].axis("off")

# ── Traction Circle ─────────────────────────────────────────────────────────────
def draw_traction(fig, df, dfb=None):
    axs = fig.subplots(2, 3); fig.subplots_adjust(hspace=0.5, wspace=0.42)
    x = X
    th = np.linspace(0, 2*np.pi, 200)

    sc = axs[0,0].scatter(df["lat_g"], df["long_g"], c=df["speed_kmh"],
                           cmap="turbo", s=1.5, alpha=0.35)
    if dfb is not None:
        axs[0,0].scatter(dfb["lat_g"], dfb["long_g"], c=ACC, s=1.2, alpha=0.18, label="B")
        axs[0,0].legend(fontsize=7, markerscale=3)
    axs[0,0].axhline(0,color=DIM,lw=0.5); axs[0,0].axvline(0,color=DIM,lw=0.5)
    for r in [1.0, 2.0, 3.0]:
        axs[0,0].plot(np.cos(th)*r, np.sin(th)*r, color=DIM, lw=0.5, ls="--", alpha=0.7)
    axs[0,0].set_xlim(-3.5, 3.5); axs[0,0].set_ylim(-3.5, 3.5)
    _ax(axs[0,0], "G-G Traction Circle", xl="Lat G", yl="Long G"); axs[0,0].grid(False)
    plt.colorbar(sc, ax=axs[0,0], fraction=0.04, pad=0.02).ax.tick_params(labelsize=6, colors=DIM)

    _L(axs[0,1], df, x, "lat_g", PRP); axs[0,1].axhline(0,color=DIM,lw=0.8,ls="--")
    if dfb is not None: _Lb(axs[0,1], dfb, x, "lat_g")
    _ax(axs[0,1], "Lateral G", yl="g")

    _L(axs[0,2], df, x, "total_g", CYN, fill=True)
    if dfb is not None: _Lb(axs[0,2], dfb, x, "total_g")
    _ax(axs[0,2], "Total G", yl="g")

    sc2 = axs[1,0].scatter(df["lat_g"].abs(), df["speed_kmh"],
                            c=df["throttle"], cmap="RdYlGn", s=1.5, alpha=0.3, vmin=0, vmax=1)
    if dfb is not None:
        axs[1,0].scatter(dfb["lat_g"].abs(), dfb["speed_kmh"], c=ACC, s=1.2, alpha=0.15)
    _ax(axs[1,0], "|Lat G| vs Speed", xl="|Lat G|", yl="km/h"); axs[1,0].grid(False)
    plt.colorbar(sc2, ax=axs[1,0], fraction=0.04, pad=0.02).ax.tick_params(labelsize=6, colors=DIM)

    n = len(df); e = n // 3
    axs[1,1].hist(df.iloc[:e]["lat_g"].abs(),    bins=25, color=GRN, alpha=0.65, label="Early", density=True)
    axs[1,1].hist(df.iloc[e:2*e]["lat_g"].abs(), bins=25, color=YLW, alpha=0.65, label="Mid",   density=True)
    axs[1,1].hist(df.iloc[2*e:]["lat_g"].abs(),  bins=25, color=ACC, alpha=0.65, label="Late",  density=True)
    _ax(axs[1,1], "|Lat G| Early/Mid/Late", xl="|Lat G|", yl="Density")
    axs[1,1].legend(fontsize=7); axs[1,1].grid(False)

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

# ── Telemetry Diff ──────────────────────────────────────────────────────────────
def draw_telediff(fig, df, dfb=None):
    axs = fig.subplots(3, 3); fig.subplots_adjust(hspace=0.55, wspace=0.38)

    if dfb is None:
        for ax in axs.flat:
            ax.text(0.5, 0.5, "Load Race B &\nEnable Compare Mode\nto see telemetry diffs",
                    ha="center", va="center", color=DIM, fontsize=11)
            ax.axis("off")
        return

    dur_a = max(df["t"].max(), 1); dur_b = max(dfb["t"].max(), 1)
    pa = (df["t"]  / dur_a).values
    pb = (dfb["t"] / dur_b).values
    pa_s = np.sort(pa); pb_s = np.sort(pb)

    def va(col): return np.interp(pa_s, pa_s, df.sort_values("t")[col].values)
    def vb(col): return np.interp(pa_s, pb_s, dfb.sort_values("t")[col].values)
    def diff(col): return vb(col) - va(col)

    def dp(ax, col, title, yl="Δ"):
        d = diff(col)
        ax.plot(pa_s, d, color=YLW, lw=1.0)
        ax.axhline(0, color=DIM, lw=0.8, ls="--")
        ax.fill_between(pa_s, d, 0, where=d>0, alpha=0.15, color=ACC)
        ax.fill_between(pa_s, d, 0, where=d<0, alpha=0.15, color=GRN)
        _ax(ax, title, xl="Race %", yl=yl)

    dp(axs[0,0], "speed_kmh", "Δ Speed (B−A)",    yl="Δkm/h")
    dp(axs[0,1], "throttle",  "Δ Throttle (B−A)", yl="Δ")
    dp(axs[0,2], "brake",     "Δ Brake (B−A)",    yl="Δ")
    dp(axs[1,0], "rpm",       "Δ RPM (B−A)",      yl="ΔRPM")
    dp(axs[1,1], "steering",  "Δ Steering (B−A)", yl="Δrad")
    dp(axs[1,2], "lat_g",     "Δ Lat G (B−A)",    yl="Δg")
    dp(axs[2,1], "long_g", "Δ Long G (B−A)", yl="Δg")

    axs[2,0].text(0.5, 0.5,
                  f"A duration: {fmt_dur(dur_a)}\nB duration: {fmt_dur(dur_b)}\nΔ: {dur_b-dur_a:+.1f}s",
                  ha="center", va="center", color=FG, fontsize=10)
    axs[2,0].axis("off")

    axs[2,2].plot(pa_s, va("speed_kmh"), color=CYN, lw=1.3, label="A")
    axs[2,2].plot(pa_s, vb("speed_kmh"), color=ACC, lw=1.3, ls="--", label="B")
    _ax(axs[2,2], "Speed Overlay", xl="Race %", yl="km/h"); axs[2,2].legend(fontsize=7)

# ── Driver Ratings ───────────────────────────────────────────────────────────────
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
        splits = lap_split_stats(d)
        lap_times = [s["time_s"] for s in splits if s["time_s"] > 1]
        consist = (max(0.0, min(100.0, 100.0 - (np.std(lap_times) / max(1.0, np.mean(lap_times))) * 100))
                   if len(lap_times) > 1 else 60.0)
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

    win = max(1, len(df) // 40)
    ax3.plot(df[X], df["throttle"].rolling(win, min_periods=1).mean()*100,
             color=GRN, lw=1.3, label="Throttle A")
    ax3.plot(df[X], df["brake"].rolling(win, min_periods=1).mean()*100,
             color=ACC, lw=1.3, label="Brake A")
    if dfb is not None:
        win_b = max(1, len(dfb) // 40)
        ax3.plot(dfb[X], dfb["throttle"].rolling(win_b, min_periods=1).mean()*100,
                 color=YLW, lw=1.1, ls="--", alpha=0.75, label="Throttle B")
        ax3.plot(dfb[X], dfb["brake"].rolling(win_b, min_periods=1).mean()*100,
                 color=PRP, lw=1.1, ls="--", alpha=0.75, label="Brake B")
    ax3.set_title("Rolling Throttle / Brake", color=FG, fontsize=9)
    ax3.set_xlabel("Race Time (s)", fontsize=7, color=DIM)
    ax3.set_ylabel("%", fontsize=7, color=DIM)
    ax3.legend(fontsize=7); ax3.grid(True, alpha=0.4)
    ax3.set_facecolor(PNL2); ax3.tick_params(colors=DIM, labelsize=7)

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

# ── Mini Map Heatmap ────────────────────────────────────────────────────────────
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
        axs = fig.subplots(4, 2); fig.subplots_adjust(hspace=0.28, wspace=0.18)
        for i, (col, cmap, title) in enumerate(METRICS_4):
            _track_map(axs[i, 0], df,  col, cmap, f"A — {title}")
            _track_map(axs[i, 1], dfb, col, cmap, f"B — {title}")

# ── Race Timeline ────────────────────────────────────────────────────────────────
def draw_timeline(fig, df, dfb=None):
    fig.subplots_adjust(hspace=0.55)

    def make_band(ax, d, label="A", col=CYN):
        xp  = d[X].values
        ft  = d["throttle"].values > 0.95
        brk = d["brake"].values    > 0.05
        cst = (d["throttle"].values < 0.05) & (d["brake"].values < 0.05)
        ax.fill_between(xp, 2, 3, where=ft,  color=GRN, alpha=0.85, step="post")
        ax.fill_between(xp, 1, 2, where=brk, color=ACC, alpha=0.85, step="post")
        ax.fill_between(xp, 0, 1, where=cst, color=DIM, alpha=0.75, step="post")
        pit = d["pit_flag"].values > 0.5
        if pit.any():
            ax.fill_between(xp, -0.4, 0, where=pit, color=ORG, alpha=0.9, step="post")
        ax.set_yticks([0.5, 1.5, 2.5])
        ax.set_yticklabels(["Coast", "Brake", "Full Thr"], fontsize=7, color=DIM)
        ax.set_ylim(-0.5, 3.5); ax.set_xlim(xp.min(), xp.max())
        ax.set_title(f"Race Timeline — {label}  (orange band = pit)", color=col, fontsize=9)
        ax.set_xlabel("Race Time (s)", fontsize=7, color=DIM)
        ax.grid(True, axis="x", alpha=0.3); ax.set_facecolor(PNL2)
        ax.tick_params(colors=DIM, labelsize=7)

    if dfb is None:
        ax1 = fig.add_subplot(3, 1, 1); make_band(ax1, df, "A", CYN)
        ax2 = fig.add_subplot(3, 1, 2)
        ax2.plot(df[X], df["speed_kmh"], color=CYN, lw=1.2)
        ax2.fill_between(df[X], df["speed_kmh"], alpha=0.12, color=CYN)
        _ax(ax2, "Speed Trace", yl="km/h")
        ax3 = fig.add_subplot(3, 1, 3)
        ax3.step(df[X], df["gear"], color=YLW, lw=1.2, where="post")
        ax3.fill_between(df[X], df["gear"], alpha=0.15, color=YLW, step="post")
        _ax(ax3, "Gear", yl="Gear")
    else:
        ax1 = fig.add_subplot(2, 1, 1); make_band(ax1, df,  "A", CYN)
        ax2 = fig.add_subplot(2, 1, 2); make_band(ax2, dfb, "B", ACC)

# ── Groups registry ────────────────────────────────────────────────────────────
GROUPS = [
    ("Race",      draw_race,            (2,2), (12, 8)),
    ("Inputs",    draw_inputs,          (3,3), (13,10)),
    ("Engine",    draw_engine,          (3,3), (13,10)),
    ("Tyres",     draw_tyres,           (3,3), (13,10)),
    ("Dynamics",  draw_dynamics,        (3,3), (13,10)),
    ("Maps",      draw_maps,            (2,2), (12, 8)),
    ("G-Force",   draw_gforce,          (2,2), (12, 8)),
    ("Fuel",      draw_fuel,            (2,3), (13, 8)),
    ("Braking",   draw_braking,         (3,3), (13,10)),
    ("Laps",      draw_laps,            (3,3), (13,10)),
    ("Traction",  draw_traction,        (2,3), (13, 9)),
    ("Tele Diff", draw_telediff,        (3,3), (13,10)),
    ("Ratings",   draw_ratings,         (2,2), (12, 9)),
    ("Heat Maps", draw_minimap_heatmap, (3,3), (13,10)),
    ("Timeline",  draw_timeline,        (3,1), (13, 9)),
]

# ── Replay ────────────────────────────────────────────────────────────────────
class Replay:
    SPEEDS = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0]  # races are long, need faster options
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

        info_wrap = tk.Frame(parent, bg=BG)
        info_wrap.pack(fill="x", padx=4, pady=2)

        self._info_a_frame = tk.Frame(info_wrap, bg=PNL2, pady=4)
        self._info_a_frame.pack(side="left", fill="x", expand=True, padx=(0,1))
        self._iv = {}; self._bars = {}
        for k in ["Time","Speed","Gear","Throttle","Brake","RPM","Lap","Race %"]:
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
        for k in ["Time","Speed","Gear","Throttle","Brake","RPM","Lap","Race %"]:
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
        self._ax.set_title("Load a race to start replay", color=DIM, fontsize=11)
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

    def load(self, df, label=""):
        self._df = df.reset_index(drop=True)
        self._la_label = label
        self._hdg_a = self._compute_headings(self._df)
        self._idx = 0; self._playing = False
        self._pbtn.config(text="▶  Play", fg=GRN)
        self._sv.set(0); self._delta_var.set("")
        self._draw_base(); self._update()

    def load_b(self, df, label=""):
        self._dfb = df.reset_index(drop=True)
        self._lb_label = label
        self._hdg_b = self._compute_headings(self._dfb)
        if not self._b_visible:
            self._info_b_frame.pack(side="left", fill="x", expand=True, padx=(1,0))
            self._mode_btn.pack(side="right", padx=8)
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
            self._ax.text(0.5,0.5,"No GPS data in this race\n(world_x / world_z all zero)",
                          ha="center",va="center",color=DIM,fontsize=12)
            self._canvas.draw(); return

        x, z, spd = df["world_x"].values, df["world_z"].values, df["speed_kmh"].values
        xr = x.max()-x.min(); zr = z.max()-z.min()
        self._car_size = max(xr, zr) * 0.022

        self._ax.plot(x, z, color=DIM, lw=0.5, alpha=0.2, zorder=1)
        pts  = np.array([x,z]).T.reshape(-1,1,2)
        segs = np.concatenate([pts[:-1],pts[1:]],axis=1)
        norm = plt.Normalize(spd.min(), spd.max())
        lc   = LineCollection(segs, cmap="turbo", norm=norm, lw=1.4, alpha=0.5, zorder=2)
        lc.set_array(spd); self._ax.add_collection(lc)
        self._ax.plot(x[0], z[0], "o", color=GRN, ms=7, zorder=5)
        self._ax.plot(x[-1],z[-1], "s", color=YLW, ms=7, zorder=5)

        self._trail_l, = self._ax.plot([], [], color=CYN, lw=2.5, alpha=0.85, zorder=6)
        h0 = float(self._hdg_a[0]) if self._hdg_a is not None else 0.0
        verts_a = self._car_verts(x[0], z[0], h0, self._car_size)
        self._car_patch_a = MplPolygon(verts_a, closed=True, fc=CYN,
                                        ec="#ffffff", lw=1.2, zorder=8, alpha=0.95)
        self._ax.add_patch(self._car_patch_a)

        if self._dfb is not None and self._dfb["world_x"].abs().max() > 1:
            xb, zb = self._dfb["world_x"].values, self._dfb["world_z"].values
            self._ax.plot(xb, zb, color=DIM, lw=0.4, alpha=0.15, zorder=1)
            self._trail_b, = self._ax.plot([], [], color=ACC, lw=2.5, alpha=0.85, zorder=6)
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
        cb.set_label("km/h", color=DIM, fontsize=7)
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
        self._iv["Lap"].set(str(int(row.get("lap_number", 0))))
        self._iv["Race %"].set(f"{idx/(max(1,len(df)-1))*100:.1f}%")
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
                delta = t_b - t_a  # race replay has no track_position-anchored realtime mode

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
            self._iv_b["Lap"].set(str(int(row_b.get("lap_number", 0))))
            self._iv_b["Race %"].set(f"{idx_b/(max(1,len(self._dfb)-1))*100:.1f}%")
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
        self.title("GT7 Race Analyst")
        self.configure(bg=BG)
        self.geometry("1380x860")
        self.minsize(1100, 700)
        self._da = self._dfa = None
        self._db = self._dfb = None
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
        tk.Label(hdr, text="RACE ANALYST", fg=CYN, bg=PNL2,
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

        fa = _section("RACE A", CYN)
        self._la = tk.Label(fa, text="not loaded", fg=DIM, bg=PNL, font=FONTL,
                             wraplength=190, justify="left")
        self._la.pack(anchor="w", padx=8, pady=2)
        tk.Button(fa, text="📂  Browse", command=lambda: self._load("a"),
                  bg=DIM2, fg=CYN, relief="flat", font=FONT, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))

        fb = _section("RACE B", ACC)
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

        tk.Label(p, text="LAP SPLITS — RACE A", fg=DIM, bg=PNL2,
                 font=FONTL).pack(anchor="w", padx=12, pady=(6,2))
        self._sec_outer = tk.Frame(p, bg=PNL2); self._sec_outer.pack(fill="both", padx=6, expand=False)
        self._sec_canvas = tk.Canvas(self._sec_outer, bg=PNL2, highlightthickness=0, height=140)
        self._sec_scroll = ttk.Scrollbar(self._sec_outer, orient="vertical", command=self._sec_canvas.yview)
        self._sec_canvas.configure(yscrollcommand=self._sec_scroll.set)
        self._sec_scroll.pack(side="right", fill="y")
        self._sec_canvas.pack(side="left", fill="both", expand=True)
        self._sec_f = tk.Frame(self._sec_canvas, bg=PNL2)
        self._sec_win = self._sec_canvas.create_window((0,0), window=self._sec_f, anchor="nw")
        self._sec_f.bind("<Configure>",
                          lambda e: self._sec_canvas.configure(scrollregion=self._sec_canvas.bbox("all")))

        tk.Label(p, text="STATS — RACE A", fg=DIM, bg=PNL2,
                 font=FONTL).pack(anchor="w", padx=12, pady=(6,2))
        self._sf = tk.Frame(p, bg=PNL2); self._sf.pack(fill="x", padx=6)

        fn = tk.Frame(p, bg=PNL, pady=4); fn.pack(fill="x", padx=6, pady=(6,4))
        tk.Label(fn, text="RACE NOTES", fg=DIM, bg=PNL, font=FONTL).pack(anchor="w", padx=8)
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
        for h, w in [("Lap",4),("Time",8),("Avg",6),("Top",6)]:
            tk.Label(hdr, text=h, fg=DIM, bg=PNL2, font=FONTL, width=w, anchor="w").pack(side="left")
        cols = [CYN, GRN, YLW]
        for i, s in enumerate(lap_split_stats(df)):
            r = tk.Frame(self._sec_f, bg=PNL2); r.pack(fill="x", pady=1)
            tk.Label(r, text=str(s["lap"]),          fg=cols[i%3], bg=PNL2, font=FONTL, width=4,  anchor="w").pack(side="left")
            tk.Label(r, text=fmt_dur(s["time_s"]),   fg=FG,         bg=PNL2, font=FONTL, width=8,  anchor="w").pack(side="left")
            tk.Label(r, text=f"{s['avg_speed']:.0f}",fg=FG,         bg=PNL2, font=FONTL, width=6,  anchor="w").pack(side="left")
            tk.Label(r, text=f"{s['top_speed']:.0f}",fg=FG,         bg=PNL2, font=FONTL, width=6,  anchor="w").pack(side="left")

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
        fn(fig, self._dfa, dfb)
        cv = FigureCanvasTkAgg(fig, master=f)
        cv.draw(); cv.get_tk_widget().pack(fill="both", expand=True)
        self._cfigs[name] = fig

    # ── load ──────────────────────────────────────────────────────────────────
    def _load(self, slot):
        path = filedialog.askopenfilename(
            title=f"Load Race {slot.upper()}",
            filetypes=[("JSON","*.json"),("All","*.*")])
        if not path: return
        try: data, df = load_race(path)
        except Exception as e: messagebox.showerror("Load Error", str(e)); return
        lbl = race_label(data)
        if slot == "a":
            self._da, self._dfa = data, df
            self._la.config(text=lbl, fg=CYN)
            self._update_stats(data, df)
            self._update_sectors(df)
            self._replay.load(df, lbl)
            self._hdr.config(text=f"A: {lbl}")
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
            messagebox.showinfo("Export", "Load Race A first."); return

        dlg = tk.Toplevel(self)
        dlg.title("Export CSV"); dlg.configure(bg=PNL2)
        dlg.geometry("265x155"); dlg.resizable(False, False)
        dlg.transient(self); dlg.grab_set()

        tk.Label(dlg, text="Export which race?", fg=FG, bg=PNL2, font=FONTB).pack(pady=(14,8))
        choice = tk.StringVar(value="a")
        for val, lbl_ in [("a","Race A"), ("b","Race B"), ("both","Both (two files)")]:
            tk.Radiobutton(dlg, text=lbl_, variable=choice, value=val,
                            bg=PNL2, fg=FG, selectcolor=DIM2,
                            activebackground=PNL2, activeforeground=CYN,
                            font=FONTL).pack(anchor="w", padx=28, pady=1)

        def _do():
            sel = choice.get(); dlg.destroy()
            if sel in ("a", "both"):
                p = filedialog.asksaveasfilename(title="Save Race A CSV",
                    defaultextension=".csv", filetypes=[("CSV","*.csv"),("All","*.*")])
                if p:
                    self._dfa.to_csv(p, index=False)
                    messagebox.showinfo("Exported", f"Race A  →  {Path(p).name}")
            if sel in ("b", "both"):
                if self._dfb is None:
                    messagebox.showinfo("Export", "Race B not loaded."); return
                p = filedialog.asksaveasfilename(title="Save Race B CSV",
                    defaultextension=".csv", filetypes=[("CSV","*.csv"),("All","*.*")])
                if p:
                    self._dfb.to_csv(p, index=False)
                    messagebox.showinfo("Exported", f"Race B  →  {Path(p).name}")

        tk.Button(dlg, text="Export", command=_do,
                   bg=ACC, fg=BG, relief="flat", font=FONTB,
                   padx=14, pady=4, cursor="hand2").pack(pady=8)
        dlg.wait_window()

    def _export_html(self):
        if self._dfa is None:
            messagebox.showinfo("Export", "Load Race A first."); return
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
            f"<title>GT7 Race Analyst — {name}</title>"
            "<style>body{background:#07080f;display:flex;justify-content:center;"
            "align-items:flex-start;min-height:100vh;margin:0;padding:20px;box-sizing:border-box}"
            "img{max-width:100%;border-radius:8px;box-shadow:0 0 30px #00f0d444}"
            "h3{color:#00f0d4;font-family:monospace;text-align:center}</style></head>"
            f"<body><div><h3>GT7 Race Analyst — {name}</h3>"
            f"<img src='data:image/png;base64,{b64}'></div></body></html>"
        )
        Path(path).write_text(html, encoding="utf-8")
        messagebox.showinfo("Exported", f"Saved: {Path(path).name}")

    def _on_tab(self, e):
        try:
            if self._nb.index("current") == 1:
                self._draw_active_chart()
        except Exception: pass

if __name__ == "__main__":
    app = AnalystApp()
    app.mainloop()
