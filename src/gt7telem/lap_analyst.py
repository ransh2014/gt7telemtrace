# lap_analyst.py — GT7 Lap Analyst
# pip install pandas matplotlib numpy
import base64
import io
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import matplotlib
import numpy as np
import pandas as pd

from . import (
    __version__,
    analysis_tools,
    auth,
    dyno,
    lapclean,
    leaderboard,
    records,
    theme,
    tracks,
    uiscroll,
)
from . import config as runtime_config
from . import labels as L

matplotlib.use("TkAgg")
import warnings

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import LineCollection
from matplotlib.patches import Polygon as MplPolygon

warnings.filterwarnings("ignore")

runtime_config.install_mac_buttons()   # macOS ignores Button colours; no-op elsewhere

from .core import registry  # noqa: E402
from .lap_charts import (  # noqa: F401, E402
    _L,
    _LAP_META_CACHE,
    _LAP_NUM_COLS,
    ACC,
    BG,
    CYN,
    DIM,
    DIM2,
    FG,
    GRN,
    GROUPS,
    ORG,
    PNL,
    PNL2,
    PRP,
    YLW,
    C,
    _ax,
    _blank,
    _brake_zone_durations,
    _fmt_laptime,
    _lap_fuel_used,
    _lap_xy,
    _Lb,
    _Lb_step,
    _num,
    _safe_name,
    _save_df_csv,
    _scan_laps,
    _track_map,
    build_sector_stats,
    build_stats,
    compute_sector_deltas,
    draw_braking,
    draw_consensus,
    draw_corners,
    draw_dynamics,
    draw_dyno,
    draw_engine,
    draw_extended,
    draw_fuel,
    draw_gforce,
    draw_inputs,
    draw_maps,
    draw_minimap_heatmap,
    draw_ratings,
    draw_sectors,
    draw_style,
    draw_telediff,
    draw_theoretical,
    draw_timeline,
    draw_traction,
    draw_tyres,
    export_csv,
    is_compact,
    lap_card_png,
    lap_card_spec,
    lap_label,
    lap_sectors,
    load_lap,
    load_lap_data,
    theory_candidates,
)

FROM_MENU = False   # set by the launcher: the window then offers a "Menu" button to go back
FONT  = (theme.MONO, 9)
FONTB = (theme.MONO, 9, "bold")
FONTL = (theme.MONO, 8)
FONTH = (theme.MONO, 13, "bold")

def _modal(win, parent):
    """Make `win` modal. grab_set() on a window that isn't viewable yet raises
    TclError on X11, so wait until it is -- and never let a failed grab break
    the dialog (or the caller, e.g. the window-close path)."""
    try:
        win.transient(parent)
        win.wait_visibility()
        win.grab_set()
    except tk.TclError:
        pass

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
        self._play_pos = 0.0    # fractional sample index while playing (see _tick)
        self._rate     = 10.0   # recorded samples per second (see _sample_rate)
        self._last_tick_t = None
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
        self._cb        = None   # current colorbar, removed before each redraw (see _draw_base)
        self._heatmap_mode = False
        self._current_track = ""
        self._boundary  = None
        self._build(parent)

    @staticmethod
    def _sample_rate(df):
        """Recorded samples per second -- what 1x playback has to advance by
        to run in real time. Median, so gaps where recording was paused
        don't drag it down."""
        dt = np.diff(df["t"].values)
        dt = dt[dt > 0]
        return float(1.0 / np.median(dt)) if len(dt) else 10.0

    def _compute_headings(self, df):
        h = df["heading"].values.copy()
        if h.max() - h.min() > 0.05:
            return h
        dx = np.diff(df["world_x"].values, prepend=df["world_x"].values[0])
        dz = np.diff(df["world_z"].values, prepend=df["world_z"].values[0])
        h = np.arctan2(dx, dz)
        # At a standstill dx = dz = 0 and atan2 snaps to "north": hold the last
        # real heading instead.
        h = np.where((dx == 0) & (dz == 0), np.nan, h)
        return pd.Series(h).ffill().bfill().fillna(0.0).to_numpy()

    def _car_verts(self, cx, cz, heading_rad, size):
        local = np.array([[0, size], [-size*0.55, -size*0.65], [size*0.55, -size*0.65]])
        cos_h, sin_h = math.cos(heading_rad), math.sin(heading_rad)
        # Travel direction is (sin h, cos h) in (x, z); this rotation puts the
        # nose of the (0, +size) triangle on exactly that vector.
        rot = np.array([[cos_h, sin_h], [-sin_h, cos_h]])
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
                                   font=(theme.MONO, 11, "bold"), pady=3)
        self._delta_lbl.pack(fill="x")

        ctrl = tk.Frame(parent, bg=PNL, pady=5)
        ctrl.pack(fill="x", padx=4, pady=(2,0))
        self._pbtn = tk.Button(ctrl, text=L.PLAY, command=self._toggle,
                                bg=PNL2, fg=GRN, relief="flat", font=FONTB,
                                padx=10, pady=2, cursor="hand2")
        self._pbtn.pack(side="left", padx=(8,4))
        tk.Button(ctrl, text=L.RESTART, command=self._reset,
                  bg=PNL2, fg=FG, relief="flat", font=FONT,
                  padx=6, pady=2, cursor="hand2").pack(side="left", padx=2)
        tk.Label(ctrl, text="  " + L.SPEED_LABEL, fg=DIM, bg=PNL, font=FONT).pack(side="left")
        for i, s in enumerate(self.SPEEDS):
            tk.Button(ctrl, text=L.SPEED_FMT.format(speed=s), bg=PNL2, fg=YLW, relief="flat", font=FONTL,
                      padx=5, pady=2, cursor="hand2",
                      command=lambda i=i: self._spd(i)).pack(side="left", padx=1)
        self._mode_btn = tk.Button(ctrl, text=L.MODE_SYNCED, command=self._toggle_mode,
                                    bg=DIM2, fg=CYN, relief="flat", font=FONTL,
                                    padx=8, pady=2, cursor="hand2")
        self._heatmap_btn = tk.Button(ctrl, text=L.HEATMAP, command=self._toggle_heatmap,
                                    bg=DIM2, fg=DIM, relief="flat", font=FONTL,
                                    padx=8, pady=2, cursor="hand2")
        self._boundary_btn = tk.Button(ctrl, text=L.SAVE_BOUNDARY, command=self._save_boundary,
                                    bg=DIM2, fg=DIM, relief="flat", font=FONTL,
                                    padx=8, pady=2, cursor="hand2")
        self._boundary_btn.pack(side="right", padx=(8,0))
        tk.Button(ctrl, text=L.REPLAY_EXPORT_CSV, command=self._export_csv,
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
                cv = tk.Canvas(f, width=theme.px(48), height=theme.px(6), bg=DIM2, highlightthickness=0); cv.pack(pady=1)
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
                cv2 = tk.Canvas(f, width=theme.px(48), height=theme.px(6), bg=DIM2, highlightthickness=0); cv2.pack(pady=1)
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
            self._mode_btn.config(text=L.MODE_REALTIME, fg=ORG)
        else:
            self._dual_mode = "synced"
            self._mode_btn.config(text=L.MODE_SYNCED, fg=CYN)
        self._update()

    def _toggle_heatmap(self):
        self._heatmap_mode = not self._heatmap_mode
        if self._heatmap_mode:
            self._heatmap_btn.config(text=L.HEATMAP_ON, fg=ORG)
        else:
            self._heatmap_btn.config(text=L.HEATMAP, fg=DIM)
        if self._df is not None:
            self._draw_base()
            self._update()

    def _export_csv(self):
        if self._df is None:
            messagebox.showinfo("Export CSV", "Load a lap first.")
            return
        default_name = _safe_name(self._la_label or "lap") + ".csv"
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
        try:
            tracks.save_boundary(self._current_track, boundary)
        except Exception as e:
            messagebox.showerror("Save Boundary", f"Couldn't save the track boundary:\n{e}")
            return
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
        self._rate  = self._sample_rate(self._df)
        self._idx = 0; self._playing = False
        self._pbtn.config(text=L.PLAY, fg=GRN)
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
        # Remove the old colorbar properly: delaxes() leaves the map axes
        # squeezed, and it shrank a little more on every load.
        if self._cb is not None:
            try:
                self._cb.remove()
            except Exception:
                pass
            self._cb = None
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
        cb = self._cb = self._fig.colorbar(lc, ax=self._ax, fraction=0.025, pad=0.01)
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
        # Real-time playback: SPEEDS[x] seconds of recording per wall-clock
        # second, via a fractional sample position. The old integer step,
        # max(1, int(speed)), made 0.25x and 0.5x identical to 1x -- and 1x
        # was one sample per frame, ~2.8x real time at 10 Hz but ~0.5x at
        # 60 Hz. Uses measured elapsed time (capped, so a stall doesn't lurch
        # ahead) rather than 1/FPS, since each redraw takes time too.
        now = time.monotonic()
        dt = 1.0 / self.FPS if self._last_tick_t is None else min(now - self._last_tick_t, 0.25)
        self._last_tick_t = now
        if int(self._play_pos) != self._idx:
            self._play_pos = float(self._idx)   # scrubbed / reset / restarted since last frame
        self._play_pos = min(self._play_pos + self.SPEEDS[self._si] * self._rate * dt,
                             len(self._df) - 1)
        self._idx = int(self._play_pos)
        self._update()
        if self._idx >= len(self._df)-1:
            self._playing = False
            self._pbtn.config(text=L.PLAY, fg=GRN)
        else:
            self._after_id = self._canvas.get_tk_widget().after(1000//self.FPS, self._tick)

    def _toggle(self):
        if self._df is None: return
        self._playing = not self._playing
        if self._playing:
            if self._idx >= len(self._df)-1: self._idx = 0
            self._pbtn.config(text=L.PAUSE, fg=YLW)
            # A frame queued before a quick pause/play would otherwise start a
            # second tick chain alongside this one, doubling playback speed.
            if self._after_id is not None:
                self._canvas.get_tk_widget().after_cancel(self._after_id)
                self._after_id = None
            self._last_tick_t = None
            self._tick()
        else:
            self._pbtn.config(text=L.PLAY, fg=GRN)

    def _reset(self):
        self._playing = False; self._pbtn.config(text=L.PLAY, fg=GRN)
        self._idx = 0; self._update()

    def _spd(self, i): self._si = i

    def _scrub(self, val):
        if self._df is None: return
        n = len(self._df)
        # _update() moves the slider itself, which fires this callback with the
        # value it just set. Snapping _idx to the 1000-step grid on that echo made
        # playback jitter and redrew every frame twice.
        if int(float(val)) == int(self._idx / max(1, n - 1) * 1000): return
        self._idx = int(int(float(val))/1000*(n-1))
        self._update()

# ── Main App ──────────────────────────────────────────────────────────────────
class AnalystApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(L.window_title(L.TOOL_LAP, __version__))
        self.configure(bg=BG)
        theme.fit_window(self, 1380, 860, 1100, 700)
        self._da = self._dfa = None
        self._db = self._dfb = None
        self._path_a = None
        self._consensus_line = None
        self._cfigs        = {}
        self._group_names  = []
        self._compare_mode = False
        self._extra = []  # 3rd+ overlay laps: [{"label","df","color"}, ...]
        self._submitting = False
        self._consensus_gen = 0
        self._top10_gen = 0
        self._theory = {"disk": None, "loading": False, "gen": 0}   # other laps for the Theoretical tab
        self._closing = False
        self._uiq = queue.Queue()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.after(100, self._drain_uiq)

    def _go_menu(self):
        """Back to the launcher menu."""
        self._to_menu = True
        self._quit()

    def _quit(self):
        self._closing = True
        plt.close("all"); self.destroy()

    def _post(self, fn):
        """Hand `fn` to the Tk thread. Worker threads must not call Tk themselves:
        tkinter blocks the caller until the main loop answers and raises if the
        window is already gone."""
        self._uiq.put(fn)

    def _drain_uiq(self):
        try:
            while True:
                fn = self._uiq.get_nowait()
                try:
                    fn()
                except Exception:
                    pass    # e.g. a callback for a dialog that has since closed
        except queue.Empty:
            pass
        if not self._closing:
            self.after(100, self._drain_uiq)

    def _build(self):
        theme.style_ttk(self, light=runtime_config.THEME == "light")
        style = ttk.Style(); style.theme_use("clam")
        for _nb_style in ("TNotebook", "Plain.TNotebook"):   # no light default border around the tab pages
            style.configure(_nb_style, background=BG, borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.configure("TNotebook.Tab", background=PNL, foreground=DIM,
                        padding=[14,5], font=FONTB)
        style.map("TNotebook.Tab",
                  background=[("selected", PNL2)],
                  foreground=[("selected", CYN)])

        hdr = tk.Frame(self, bg=PNL2, pady=7); hdr.pack(fill="x")
        if FROM_MENU:
            tk.Button(hdr, text=L.MENU_BUTTON, command=self._go_menu, bg=PNL, fg=FG, relief="flat",
                      font=FONT, padx=10, pady=2, cursor="hand2").pack(side="right", padx=(4, 12))
        brand = tk.Frame(hdr, bg=PNL2)   # brand + tool name, stacked in the top-left corner
        brand.pack(side="left", padx=(10, 12))
        tk.Label(brand, text=L.HEADER_BRAND, fg=ACC, bg=PNL2, font=(theme.MONO, 8, "bold")).pack(anchor="w")
        tk.Label(brand, text=L.TITLE_LAP, fg=CYN, bg=PNL2,
                 font=(theme.DISPLAY, 15, "bold")).pack(anchor="w")
        self._hdr = tk.Label(hdr, text="", fg=DIM, bg=PNL2, font=FONT)
        self._hdr.pack(side="right", padx=16)

        body = tk.Frame(self, bg=BG); body.pack(fill="both", expand=True)

        side = uiscroll.make_scrollable(body, PNL2, theme.px(284))
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
        self._la = tk.Label(fa, text=L.NOT_LOADED, fg=DIM, bg=PNL, font=FONTL,
                             wraplength=theme.px(236), justify="left")
        self._la.pack(anchor="w", padx=8, pady=2)
        tk.Button(fa, text=L.BROWSE, command=lambda: self._load("a"),
                  bg=DIM2, fg=CYN, relief="flat", font=FONT, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))

        fb = _section("LAP B", ACC)
        self._lb = tk.Label(fb, text=L.NOT_LOADED, fg=DIM, bg=PNL, font=FONTL,
                             wraplength=theme.px(236), justify="left")
        self._lb.pack(anchor="w", padx=8, pady=2)
        tk.Button(fb, text=L.BROWSE, command=lambda: self._load("b"),
                  bg=DIM2, fg=ACC, relief="flat", font=FONT, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))

        fo = _section("OVERLAY (3+ LAPS)", PRP)
        self._ov_list_f = tk.Frame(fo, bg=PNL)
        self._ov_list_f.pack(fill="x", padx=8)
        tk.Button(fo, text=L.ADD_OVERLAY_LAP, command=self._add_overlay_lap,
                  bg=DIM2, fg=PRP, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))
        self._refresh_overlay_list()

        fe = _section("EXPORT", DIM)
        tk.Button(fe, text=L.EXPORT_CSV, command=self._export_csv,
                  bg=DIM2, fg=GRN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,2))
        tk.Button(fe, text=L.EXPORT_CHART_HTML, command=self._export_html,
                  bg=DIM2, fg=CYN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))
        tk.Button(fe, text=L.EXPORT_LAP_CARD, command=self._export_card,
                  bg=DIM2, fg=PRP, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))
        tk.Button(fe, text=L.BACKUP_ALL_LAPS, command=self._backup_all_laps,
                  bg=DIM2, fg=YLW, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))
        tk.Button(fe, text=L.FIND_DUPLICATE_LAPS, command=self._find_duplicate_laps,
                  bg=DIM2, fg=YLW, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))

        ft = _section("ANALYSIS TOOLS", GRN)
        for kind, text, colour in registry.LAP_TOOLS:
            tk.Button(ft, text=text, command=lambda k=kind: self._show_tool(k),
                      bg=DIM2, fg=globals()[colour], relief="flat", font=FONTL, padx=8, pady=2,
                      cursor="hand2").pack(anchor="w", padx=8, pady=(2, 2))
        tk.Button(ft, text=registry.REPLAY_EXPORT_LABEL, command=self._export_replay3d,
                  bg=DIM2, fg=GRN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2, 4))

        fl = _section("LEADERBOARD", YLW)
        tk.Button(fl, text=L.SUBMIT_TO_LEADERBOARD, command=self._submit_leaderboard,
                  bg=DIM2, fg=YLW, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))
        tk.Button(fl, text=L.LOAD_COMMUNITY_LINE, command=self._load_consensus,
                  bg=DIM2, fg=CYN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))
        self._top10_f = tk.Frame(fl, bg=PNL)
        self._top10_f.pack(fill="x", padx=8, pady=(0,4))
        tk.Label(self._top10_f, text=L.TOP10_PLACEHOLDER, fg=DIM, bg=PNL,
                 font=FONTL, wraplength=theme.px(236), justify="left").pack(anchor="w")

        tk.Label(p, text=L.SECTORS_LAP_A, fg=DIM, bg=PNL2,
                 font=FONTL).pack(anchor="w", padx=12, pady=(6,2))
        self._sec_f = tk.Frame(p, bg=PNL2); self._sec_f.pack(fill="x", padx=6)

        tk.Label(p, text=L.STATS_LAP_A, fg=DIM, bg=PNL2,
                 font=FONTL).pack(anchor="w", padx=12, pady=(6,2))
        self._sf = tk.Frame(p, bg=PNL2); self._sf.pack(fill="x", padx=6)

        fn = tk.Frame(p, bg=PNL, pady=4); fn.pack(fill="x", padx=6, pady=(6,4))
        tk.Label(fn, text=L.LAP_NOTES, fg=DIM, bg=PNL, font=FONTL).pack(anchor="w", padx=8)
        self._notes = tk.Text(fn, height=5, bg=PNL2, fg=FG, font=FONTL,
                              insertbackground=CYN, relief="flat",
                              wrap="word", padx=4, pady=4)
        self._notes.pack(fill="x", padx=8, pady=4)
        tk.Button(fn, text=L.SAVE_NOTES, command=self._save_notes,
                  bg=DIM2, fg=CYN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))

    def _notes_dirty(self):
        return (self._da is not None and bool(self._path_a)
                and self._notes.get("1.0", "end").strip() != str(self._da.get("notes") or "").strip())

    def _save_notes(self, quiet=False):
        """Notes used to live only in the text box and were lost on close.
        They're stored in Lap A's own JSON file under "notes" (written via a
        temp file so a failed write can't corrupt the lap)."""
        if self._da is None or not self._path_a:
            if not quiet:
                messagebox.showinfo("Lap Notes", "Load Lap A from a file first.")
            return False
        notes = self._notes.get("1.0", "end").strip()
        path = Path(self._path_a)
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            data["notes"] = notes
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
            self._da["notes"] = notes
            if not quiet:
                messagebox.showinfo("Lap Notes", "Notes saved with this lap.")
            return True
        except Exception as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            messagebox.showerror("Lap Notes", f"Couldn't save notes:\n{e}")
            return False

    # ── overlay laps (3+ lap comparison) ────────────────────────────────────
    _OVERLAY_COLORS = [YLW, GRN, PRP, ORG, "#96949c", "#f2f0ec"]

    def _refresh_overlay_list(self):
        for w in self._ov_list_f.winfo_children(): w.destroy()
        if not self._extra:
            tk.Label(self._ov_list_f, text="none added", fg=DIM, bg=PNL,
                     font=FONTL).pack(anchor="w")
            return
        for i, ov in enumerate(self._extra):
            row = tk.Frame(self._ov_list_f, bg=PNL); row.pack(fill="x", pady=1)
            tk.Label(row, text="●", fg=ov["color"], bg=PNL, font=FONTL).pack(side="left")
            tk.Label(row, text=ov["label"], fg=FG, bg=PNL, font=FONTL,
                     wraplength=theme.px(150), justify="left", anchor="w").pack(side="left", padx=4)
            tk.Button(row, text="✕", command=lambda i=i: self._remove_overlay(i),
                      bg=PNL, fg=ACC, relief="flat", font=FONTL, bd=0,
                      cursor="hand2").pack(side="right")

    def _add_overlay_lap(self):
        path = self._browse_laps_dialog("overlay lap")
        if not path: return
        try:
            data, df = load_lap(path)
        except Exception as e:
            messagebox.showerror("Load Error", str(e)); return
        used = {ov["color"] for ov in self._extra}
        color = next((c for c in self._OVERLAY_COLORS if c not in used),
                     self._OVERLAY_COLORS[len(self._extra) % len(self._OVERLAY_COLORS)])
        self._extra.append({"label": lap_label(data), "df": df, "color": color})
        self._refresh_overlay_list()
        for k in list(self._cfigs):
            if self._cfigs[k]: plt.close(self._cfigs[k])
            self._cfigs[k] = None
        self._draw_active_chart()

    def _remove_overlay(self, i):
        if 0 <= i < len(self._extra):
            del self._extra[i]
        self._refresh_overlay_list()
        for k in list(self._cfigs):
            if self._cfigs[k]: plt.close(self._cfigs[k])
            self._cfigs[k] = None
        self._draw_active_chart()

    def _backup_all_laps(self):
        def worker():
            path = runtime_config.backup_laps()
            err = runtime_config.last_backup_error
            self._post(lambda: self._after_backup(path, err))
        threading.Thread(target=worker, daemon=True).start()

    # ── analysis tools (coach, setup advisor, DNA, planner, validation, dyno compare, 3D replay) ──────────
    def _tool_text(self, kind):
        return analysis_tools.lap_tool(kind, self._dfa, self._dfb, self._da, self._db,
                                       [e["df"] for e in self._extra])

    def _show_tool(self, kind):
        try:
            title, text = self._tool_text(kind)
        except Exception as e:
            messagebox.showerror("Analysis tools", f"Couldn't run that tool on this lap: {e}")
            return
        win = tk.Toplevel(self)
        win.title(f"TRACE - {title}")
        win.configure(bg=BG)
        theme.fit_window(win, 760, 460, 480, 280)
        box = tk.Text(win, bg=PNL2, fg=FG, font=FONT, wrap="word", relief="flat", padx=12, pady=10)
        box.pack(fill="both", expand=True)
        box.insert("1.0", text)
        box.config(state="disabled")

    def _export_replay3d(self):
        if not analysis_tools.replay_ok(self._dfa):
            messagebox.showinfo("3D replay", "Load Lap A first. It needs a lap with position data (not a leaderboard ghost).")
            return
        path = filedialog.asksaveasfilename(title="Export 3D replay", defaultextension=".html",
                                            filetypes=[("HTML", "*.html"), ("All", "*.*")])
        if not path:
            return
        try:
            analysis_tools.write_replay(path, self._dfa, self._dfb if analysis_tools.replay_ok(self._dfb) else None,
                                        title=f"TRACE replay - {lap_label(self._da or {})}")
        except Exception as e:
            messagebox.showerror("3D replay", f"Couldn't write the replay: {e}")
            return
        messagebox.showinfo("3D replay", f"Saved. Open it in any browser: {path}")

    def _find_duplicate_laps(self):
        """Scan the laps folder for identical lap files and offer to move the extra
        copies into _duplicates (nothing is deleted)."""
        folder = Path(runtime_config.load().get("LAPS_FOLDER") or runtime_config.LAPS_FOLDER)

        def worker():
            try:
                groups = lapclean.find_duplicates(folder)
            except Exception as e:
                msg = str(e)
                self._post(lambda: messagebox.showerror("Duplicate laps", f"Scan failed:\n{msg}"))
                return
            self._post(lambda: self._after_duplicate_scan(folder, groups))
        threading.Thread(target=worker, daemon=True).start()

    def _after_duplicate_scan(self, folder, groups):
        extras = [p for _keep, ps in groups for p in ps]
        if not extras:
            messagebox.showinfo("Duplicate laps", "No duplicate laps found.")
            return
        shown = "\n".join(f"  {p.parent.name}/{p.name}" for p in extras[:12])
        more = f"\n  ... and {len(extras) - 12} more" if len(extras) > 12 else ""
        if not messagebox.askyesno(
                "Duplicate laps",
                f"Found {len(extras)} extra cop{'y' if len(extras) == 1 else 'ies'} of laps you already have:"
                f"\n\n{shown}{more}\n\nMove them to the _duplicates folder? The original of each "
                "lap stays where it is, and nothing is deleted."):
            return
        moved, errors = lapclean.move_duplicates(groups, folder)
        msg = f"Moved {len(moved)} file(s) to:\n{folder / lapclean.DUP_FOLDER}"
        if errors:
            msg += f"\n\n{len(errors)} could not be moved (first: {errors[0][1]})"
        messagebox.showinfo("Duplicate laps", msg)

    def _after_backup(self, path, err):
        if path is not None:
            messagebox.showinfo("Backup Laps", f"Saved to:\n{path}")
        elif err:
            messagebox.showerror("Backup Laps", f"Couldn't back up your laps:\n{err}")
        else:
            messagebox.showinfo("Backup Laps", "No laps found to back up yet.")

    # ── search/filter lap picker (replaces the bare native file dialog) ────
    def _browse_laps_dialog(self, slot_label):
        """Scans the configured laps folder, pulling car/track/date out of
        each lap's own JSON so laps can be filtered without guessing from
        filenames in a native file browser. Falls back to that native
        dialog if the folder is missing/empty or nothing parses."""
        folder = Path(runtime_config.load().get("LAPS_FOLDER") or runtime_config.LAPS_FOLDER)
        entries = _scan_laps(folder)
        if not entries:
            kw = {"initialdir": str(folder)} if folder.exists() else {}
            return filedialog.askopenfilename(
                title=f"Load {slot_label}", filetypes=[("JSON", "*.json"), ("All", "*.*")], **kw)

        result = {"path": None}
        dlg = tk.Toplevel(self)
        dlg.title(f"Load {slot_label} — Search")
        dlg.configure(bg=PNL2)
        dlg.geometry(f"{theme.px(580)}x{theme.px(480)}")
        _modal(dlg, self)

        top = tk.Frame(dlg, bg=PNL2); top.pack(fill="x", padx=10, pady=8)
        car_var, track_var, date_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for lbl_, var in [("Car:", car_var), ("Track:", track_var), ("Date:", date_var)]:
            tk.Label(top, text=lbl_, fg=DIM, bg=PNL2, font=FONTL).pack(side="left")
            tk.Entry(top, textvariable=var, bg=DIM2, fg=FG, insertbackground=FG,
                      relief="flat", width=14).pack(side="left", padx=(4, 10))

        list_f = tk.Frame(dlg, bg=PNL); list_f.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        lb = tk.Listbox(list_f, bg=PNL, fg=FG, font=FONTL, selectbackground=DIM2,
                         relief="flat", activestyle="none")
        sb = ttk.Scrollbar(list_f, command=lb.yview); lb.config(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")

        shown = []
        def refresh(*_):
            lb.delete(0, "end"); shown.clear()
            cq, tq, dq = car_var.get().lower(), track_var.get().lower(), date_var.get().lower()
            for e in entries:
                if cq and cq not in e["car"].lower(): continue
                if tq and tq not in e["track"].lower(): continue
                if dq and dq not in e["date"].lower(): continue
                shown.append(e)
                lb.insert("end", f"{e['date']}  |  {e['car']}  |  {e['track']}  |  {e['lap_time']}")
        for v in (car_var, track_var, date_var):
            v.trace_add("write", refresh)
        refresh()

        def pick(_e=None):
            sel = lb.curselection()
            if not sel: return
            result["path"] = shown[sel[0]]["path"]
            dlg.destroy()

        lb.bind("<Double-Button-1>", pick)
        btn_f = tk.Frame(dlg, bg=PNL2); btn_f.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(btn_f, text="Load Selected", command=pick, bg=DIM2, fg=CYN,
                  relief="flat", font=FONTB, padx=10, pady=4, cursor="hand2").pack(side="right")
        tk.Button(btn_f, text="Cancel", command=dlg.destroy, bg=DIM2, fg=DIM,
                  relief="flat", font=FONTB, padx=10, pady=4, cursor="hand2").pack(side="right", padx=(0, 8))

        dlg.wait_window()
        return result["path"]

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
        self._cmp_btn = tk.Button(hdr, text=L.COMPARE_OFF,
                                   command=self._toggle_compare,
                                   bg=DIM2, fg=DIM, relief="flat", font=FONTB,
                                   padx=10, pady=2, cursor="hand2")
        self._cmp_btn.pack(side="right", padx=10)

        snb = ttk.Notebook(parent, style="Plain.TNotebook"); snb.pack(fill="both", expand=True)
        self._snb = snb
        self._ctabs = {}
        for name, fn, shape, fs in GROUPS:
            f = tk.Frame(snb, bg=BG)
            snb.add(f, text=f" {name} ")
            self._ctabs[name] = (f, fn, fs)
            self._cfigs[name] = None
            self._group_names.append(name)
        snb.bind("<<NotebookTabChanged>>", lambda e: self._draw_active_chart())
        uiscroll.tab_strip(parent, snb, self._group_names, BG, PNL, DIM, PNL2, CYN, FONTB)

    def _toggle_compare(self):
        self._compare_mode = not self._compare_mode
        if self._compare_mode:
            self._cmp_btn.config(text=L.COMPARE_ON, fg=CYN, bg=PNL2)
        else:
            self._cmp_btn.config(text=L.COMPARE_OFF, fg=DIM, bg=DIM2)
        for k in list(self._cfigs):
            if self._cfigs[k]: plt.close(self._cfigs[k])
            self._cfigs[k] = None
        self._draw_active_chart()

    def _draw_active_chart(self):
        if self._dfa is None: return
        idx  = self._snb.index("current")
        name = self._group_names[idx]
        if self._cfigs.get(name) is not None:
            return      # already drawn for the current laps; switching tabs shouldn't rebuild it
        self._draw_chart(name)

    def _draw_chart(self, name):
        if self._dfa is None: return
        f, fn, fs = self._ctabs[name]
        for w in f.winfo_children(): w.destroy()
        if self._cfigs.get(name):
            plt.close(self._cfigs[name])
        self._cfigs[name] = None
        if name == "Theoretical" and self._theory["disk"] is None:
            self._theory_start()
            tk.Label(f, text="Looking through your saved laps for this car and track...", fg=DIM, bg=BG,
                     font=FONT).pack(expand=True)
            return
        if name == "Dyno & Tune":
            self._ensure_mass()
        fig = plt.figure(figsize=fs, facecolor=BG)
        dfb = self._dfb if self._compare_mode else None
        try:
            if name == "Consensus":
                fn(fig, self._dfa, dfb, self._consensus_line)
            elif name == "Theoretical":
                fn(fig, self._dfa, dfb, self._theory_pool())
            elif name == "Inputs" and self._extra:
                extra = [(ov["label"], ov["df"], ov["color"]) for ov in self._extra]
                fn(fig, self._dfa, dfb, extra)
            else:
                fn(fig, self._dfa, dfb)
        except Exception as e:
            plt.close(fig)       # don't leak the half-drawn figure or keep it for HTML export
            tk.Label(f, text=f"Couldn't draw this chart:\n{e}", fg=ACC, bg=BG, font=FONT,
                     justify="center").pack(expand=True)
            return
        # The figure keeps a readable minimum size and the pane scrolls when the window is smaller.
        keep = 1.0 if name == "Dyno & Tune" else 0.8
        if name == "Dyno & Tune":
            bar = tk.Frame(f, bg=BG)
            bar.pack(side="top", fill="x")
            kg = self._dfa.attrs.get("mass_kg")
            tk.Button(bar, text="Set weight" + (f" ({kg:.0f} kg)" if kg else ""),
                      command=self._set_weight, bg=PNL, fg=FG, relief="flat", font=FONT,
                      padx=10, pady=2).pack(side="right", padx=6, pady=3)
        host = uiscroll.scroll_view(f, BG, int(fig.get_figwidth() * fig.dpi * keep),
                                    int(fig.get_figheight() * fig.dpi * keep))
        cv = FigureCanvasTkAgg(fig, master=host)
        cv.draw(); cv.get_tk_widget().pack(fill="both", expand=True)
        self._cfigs[name] = fig

    def _ensure_mass(self):
        """The car's weight turns the dyno's power-to-weight into real hp and
        Nm. Asked once per car and remembered; Cancel keeps the hp/tonne view."""
        asked = self.__dict__.setdefault("_mass_asked", set())
        for df in (self._dfa, self._dfb if self._compare_mode else None):
            if df is None:
                continue
            car = df.attrs.get("car") or ""
            kg = dyno.load_weight(car) if car else None
            if kg is None and car and car.lower() not in asked:
                asked.add(car.lower())
                kg = simpledialog.askfloat(
                    "Car weight",
                    f"Weight of the {car} in kg?\n(on the car's info screen or tuning sheet)\n\n"
                    "Cancel to see power-to-weight (hp/tonne) instead.",
                    parent=self, minvalue=300, maxvalue=5000)
                if kg:
                    dyno.save_weight(car, kg)
            df.attrs["mass_kg"] = kg

    def _set_weight(self):
        """Set or change the car's weight any time (the one-time prompt may have been skipped)."""
        for df in (self._dfa, self._dfb if self._compare_mode else None):
            if df is None:
                continue
            car = df.attrs.get("car") or ""
            if not car:
                continue
            cur = dyno.load_weight(car)
            kg = simpledialog.askfloat(
                "Car weight", f"Weight of the {car} in kg?", parent=self,
                initialvalue=cur, minvalue=300, maxvalue=5000)
            if kg:
                dyno.save_weight(car, kg)
        self._ensure_mass()
        self._draw_chart("Dyno & Tune")

    # ── theoretical best: the other laps on this car and track ────────────────
    THEORY_MAX_LAPS = 10

    def _theory_reset(self):
        """Lap A changed: forget the laps found for the old one (a worker still
        running for it is told so by the generation number)."""
        self._theory = {"disk": None, "loading": False, "gen": self._theory["gen"] + 1}

    def _theory_start(self):
        """Find this car+track's fastest saved laps on a worker thread (each file
        is a full parse), then redraw the tab."""
        if self._theory["loading"] or self._da is None:
            return
        self._theory["loading"] = True
        gen, da, path_a = self._theory["gen"], dict(self._da), self._path_a
        folder = runtime_config.load().get("LAPS_FOLDER")

        def work():
            found = []
            try:
                for row in theory_candidates(records.scan_laps(folder), da, path_a, self.THEORY_MAX_LAPS):
                    try:
                        _d, frame = load_lap(row["path"])
                    except Exception:
                        continue
                    found.append((f"{row['recorded_at'][:8]} {_fmt_laptime(row['time_s'])}", frame))
            except Exception:
                found = []
            self._post(lambda: self._theory_done(gen, found))

        threading.Thread(target=work, daemon=True).start()

    def _theory_done(self, gen, found):
        if gen != self._theory["gen"]:
            return        # a different Lap A was loaded meanwhile
        self._theory.update(disk=found, loading=False)
        self._cfigs["Theoretical"] = None
        try:
            if self._group_names[self._snb.index("current")] == "Theoretical":
                self._draw_chart("Theoretical")
        except Exception:
            pass

    def _theory_pool(self):
        """Lap A, Lap B, the overlays and the saved laps found on disk."""
        pool = [("Lap A", self._dfa)]
        if self._dfb is not None:
            pool.append(("Lap B", self._dfb))
        pool += [(ov["label"], ov["df"]) for ov in self._extra]
        return pool + list(self._theory["disk"] or [])

    # ── load ──────────────────────────────────────────────────────────────────
    def _load(self, slot):
        path = self._browse_laps_dialog(f"Lap {slot.upper()}")
        if not path: return
        try: data, df = load_lap(path)
        except Exception as e: messagebox.showerror("Load Error", str(e)); return
        lbl = lap_label(data)
        if slot == "a":
            if self._notes_dirty() and messagebox.askyesno(
                    "Lap Notes", "The notes on the current Lap A haven't been saved.\nSave them before switching?"):
                self._save_notes(quiet=True)
            # The community line belongs to the previous car/track; drop it and
            # ignore any download still in flight for it.
            self._consensus_line = None
            self._consensus_gen += 1
            self._theory_reset()
            self._da, self._dfa = data, df
            self._path_a = path
            self._notes.delete("1.0", "end")
            self._notes.insert("1.0", data.get("notes", ""))
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
            elif self._cfigs.get("Theoretical"):     # Lap B is one of the laps it stitches
                plt.close(self._cfigs["Theoretical"])
                self._cfigs["Theoretical"] = None
                self._draw_active_chart()

    # ── exports ───────────────────────────────────────────────────────────────
    def _export_csv(self):
        if self._dfa is None:
            messagebox.showinfo("Export", "Load Lap A first."); return

        dlg = tk.Toplevel(self)
        dlg.title("Export CSV"); dlg.configure(bg=PNL2)
        dlg.geometry(f"{theme.px(265)}x{theme.px(155)}"); dlg.resizable(False, False)
        _modal(dlg, self)

        tk.Label(dlg, text="Export which lap?", fg=FG, bg=PNL2, font=FONTB).pack(pady=(14,8))
        choice = tk.StringVar(value="a")
        for val, lbl_ in [("a","Lap A"), ("b","Lap B"), ("both","Both (two files)")]:
            tk.Radiobutton(dlg, text=lbl_, variable=choice, value=val,
                            bg=PNL2, fg=FG, selectcolor=DIM2,
                            activebackground=PNL2, activeforeground=CYN,
                            font=FONTL).pack(anchor="w", padx=28, pady=1)

        def _do():
            sel = choice.get(); dlg.destroy()
            jobs = []
            if sel in ("a", "both"):
                jobs.append(("A", self._dfa, self._da))
            if sel in ("b", "both"):
                if self._dfb is None:
                    messagebox.showinfo("Export", "Lap B not loaded.")
                else:
                    jobs.append(("B", self._dfb, self._db))
            for name, d, meta in jobs:
                p = filedialog.asksaveasfilename(
                    title=f"Save Lap {name} CSV", defaultextension=".csv",
                    initialfile=_safe_name(lap_label(meta or {}, short=True)) + ".csv",
                    filetypes=[("CSV","*.csv"),("All","*.*")])
                if not p:
                    continue
                try:
                    _save_df_csv(d, p)
                except Exception as e:
                    messagebox.showerror("Export CSV", f"Couldn't save Lap {name} (is the file open in another program?):\n{e}")
                else:
                    messagebox.showinfo("Exported", f"Lap {name}  →  {Path(p).name}")

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
        try:
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=BG)
            b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            messagebox.showerror("Export", f"Couldn't render the chart:\n{e}"); return
        html = (
            "<!DOCTYPE html><html><head>"
            # Written as UTF-8 below; without this a browser opening the file
            # over file:// guesses the locale encoding and the dash garbles.
            '<meta charset="utf-8">'
            f"<title>TRACE Lap Analyst — {name}</title>"
            "<style>body{background:#07080f;display:flex;justify-content:center;"
            "align-items:flex-start;min-height:100vh;margin:0;padding:20px;box-sizing:border-box}"
            "img{max-width:100%;border-radius:8px;box-shadow:0 0 30px #00f0d444}"
            "h3{color:#00f0d4;font-family:monospace;text-align:center}</style></head>"
            f"<body><div><h3>TRACE Lap Analyst — {name}</h3>"
            f"<img src='data:image/png;base64,{b64}'></div></body></html>"
        )
        try:
            Path(path).write_text(html, encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Export", f"Couldn't save the file (is it open in another program?):\n{e}"); return
        messagebox.showinfo("Exported", f"Saved: {Path(path).name}")

    def _export_card(self):
        if self._dfa is None:
            messagebox.showinfo("Export", "Load Lap A first."); return
        d = self._da or {}
        lap_s = _num(d.get("lap_time_s"))
        base = _safe_name(f"TRACE_{d.get('track') or 'lap'}_{lap_s:.3f}s")
        path = filedialog.asksaveasfilename(
            title="Save Lap Card", defaultextension=".png", initialfile=f"{base}.png",
            filetypes=[("PNG image", "*.png"), ("All", "*.*")])
        if not path: return
        try:
            Path(path).write_bytes(lap_card_png(d, self._dfa))
        except Exception as e:
            messagebox.showerror("Lap Card", f"Couldn't create the lap card:\n{e}"); return
        messagebox.showinfo("Exported", f"Lap card saved: {Path(path).name}")
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
        self._top10_gen += 1
        gen = self._top10_gen

        def worker():
            try:
                rows = leaderboard.get_top_laps(car, track, n=10)
            except Exception:
                rows = []
            self._post(lambda: self._render_top10(car, track, rows, gen))

        threading.Thread(target=worker, daemon=True).start()

    def _render_top10(self, car, track, rows, gen=None):
        if gen is not None and gen != self._top10_gen:
            return      # a newer lap was loaded while this was downloading
        for w in self._top10_f.winfo_children(): w.destroy()
        tk.Label(self._top10_f, text=f"{car} @ {track}", fg=CYN, bg=PNL,
                 font=FONTL, wraplength=theme.px(236), justify="left").pack(anchor="w")
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        if not rows:
            tk.Label(self._top10_f, text="no times yet — be the first!",
                     fg=DIM, bg=PNL, font=FONTL, wraplength=theme.px(236),
                     justify="left").pack(anchor="w", pady=(2,0))
            return
        for i, row in enumerate(rows, 1):
            time_str = _fmt_laptime(_num(row.get("lap_time_ms")) / 1000.0)
            name = str(row.get("psn_name") or "?")
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
        gen = self._consensus_gen

        def worker():
            try:
                rows = leaderboard.get_consensus_line(car, track, n=10)
            except Exception:
                rows = []
            self._post(lambda: self._after_consensus(rows, gen))

        threading.Thread(target=worker, daemon=True).start()

    def _after_consensus(self, rows, gen=None):
        if gen is not None and gen != self._consensus_gen:
            return      # Lap A changed while this was downloading; it's for another car/track
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
        try:
            samples = leaderboard.get_lap_samples(row["id"])
        except Exception:
            samples = []
        if not samples:
            self._post(lambda: messagebox.showerror(
                "Ghost Download", "Couldn't download that lap -- check your internet connection and try again."))
            return
        data = {
            "car": str(row.get("car_name") or "?"),
            "track": str(row.get("track_name") or "?"),
            "lap_time_s": _num(row.get("lap_time_ms")) / 1000.0,
            "samples": samples,
        }
        try:
            _, dfb = load_lap_data(data)
        except Exception as e:
            msg = str(e)
            self._post(lambda: messagebox.showerror("Ghost Download", msg))
            return
        self._post(lambda: self._load_ghost_result(data, dfb, row))

    def _load_ghost_result(self, data, dfb, row):
        lbl = f"{str(row.get('psn_name') or '?')} (ghost) -- {lap_label(data, short=True)}"
        self._db, self._dfb = data, dfb
        self._lb.config(text=lbl, fg=ACC)
        self._replay.load_b(dfb, lbl)
        if self._compare_mode:
            for k in list(self._cfigs):
                if self._cfigs[k]: plt.close(self._cfigs[k])
                self._cfigs[k] = None
            self._draw_active_chart()
        elif self._cfigs.get("Theoretical"):     # Lap B is one of the laps it stitches
            plt.close(self._cfigs["Theoretical"])
            self._cfigs["Theoretical"] = None
            self._draw_active_chart()

    def _submit_leaderboard(self):
        if self._submitting:
            return      # a second click while one is in flight used to post the lap twice
        if self._da is None:
            messagebox.showinfo("Submit to Leaderboard", "Load Lap A first."); return
        car   = self._da.get("car_display")   or self._da.get("car", "?")
        track = self._da.get("track_display") or self._da.get("track", "?")
        problem = leaderboard.lap_submission_error(self._da)
        if problem:
            messagebox.showinfo("Submit to Leaderboard", problem); return
        lap_time_ms = int(round(_num(self._da.get("lap_time_s")) * 1000))
        samples = self._da["samples"]

        if not runtime_config.SUPABASE_ACCESS_TOKEN:
            if not self._prompt_create_account():
                return

        psn = simpledialog.askstring(
            "Submit to Leaderboard",
            f"Submitting: {car} @ {track}  —  {lap_time_ms/1000:.3f}s\n\nPSN name:",
            initialvalue=runtime_config.PSN_NAME, parent=self)
        if not psn: return
        psn = " ".join(psn.split())[:32]   # the leaderboard accepts 1-32 characters
        if not psn: return
        runtime_config.PSN_NAME = psn
        runtime_config.save(PSN_NAME=psn)

        self._submitting = True

        def worker():
            try:
                ok, reason = leaderboard.submit_lap(
                    car, track, lap_time_ms, psn, samples,
                    access_token=runtime_config.SUPABASE_ACCESS_TOKEN,
                    user_id=runtime_config.SUPABASE_USER_ID)
                if not ok and reason == "auth":
                    # Expired/rejected access token: refresh once and retry.
                    # Payload errors (400/422) and 5xx never come here, so they
                    # no longer trigger a pointless refresh.
                    session = auth.refresh_session(runtime_config.SUPABASE_REFRESH_TOKEN)
                    if session:
                        runtime_config.SUPABASE_ACCESS_TOKEN = session["access_token"]
                        runtime_config.SUPABASE_REFRESH_TOKEN = session["refresh_token"]
                        runtime_config.save(
                            SUPABASE_ACCESS_TOKEN=session["access_token"],
                            SUPABASE_REFRESH_TOKEN=session["refresh_token"],
                        )
                        ok, reason = leaderboard.submit_lap(
                            car, track, lap_time_ms, psn, samples,
                            access_token=runtime_config.SUPABASE_ACCESS_TOKEN,
                            user_id=runtime_config.SUPABASE_USER_ID)
                    elif auth.last_refresh_error == "invalid":
                        # Only a clear "this refresh token is bad" verdict ends the
                        # account; a rate limit or outage must not delete it.
                        reason = "session"
                    else:
                        reason = "network"
                    if reason == "auth":
                        reason = "server"
            except Exception:
                ok, reason = False, "network"
            self._post(lambda: self._after_submit(ok, reason))

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
        _modal(win, self)

        result = {"ok": False}
        busy = {"v": False}

        def alive():
            try:
                return bool(win.winfo_exists())
            except tk.TclError:
                return False

        def close():
            # Closing mid sign-up would orphan the new account (tokens never saved).
            if not busy["v"]:
                win.destroy()

        win.protocol("WM_DELETE_WINDOW", close)

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
                           wraplength=theme.px(260), justify="center")
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

        def set_busy(b):
            busy["v"] = b
            if not alive():
                return
            state = "disabled" if b else "normal"
            create_btn.config(state=state)
            cancel_btn.config(state=state)

        def do_create():
            name = " ".join(name_var.get().split())[:32]
            if not name:
                status.config(text="Enter a display name first.")
                return
            set_busy(True)
            status.config(text="Creating account…", fg=DIM)

            def worker():
                session = None
                name_saved = False
                try:
                    session = auth.sign_up_anonymous()
                    if session:
                        name_saved = auth.set_display_name(session["access_token"], session["user_id"], name)
                except Exception:
                    pass
                self._post(lambda: after_create(session, name, name_saved))

            threading.Thread(target=worker, daemon=True).start()

        def after_create(session, name, name_saved=True):
            set_busy(False)
            if not session:
                if alive():
                    status.config(text="Couldn't reach the server -- try again.", fg=ACC)
                return
            runtime_config.SUPABASE_ACCESS_TOKEN = session["access_token"]
            runtime_config.SUPABASE_REFRESH_TOKEN = session["refresh_token"]
            runtime_config.SUPABASE_USER_ID = session["user_id"]
            runtime_config.PSN_NAME = name
            runtime_config.ONBOARDING_DONE = True
            saved = runtime_config.save(
                SUPABASE_ACCESS_TOKEN=session["access_token"],
                SUPABASE_REFRESH_TOKEN=session["refresh_token"],
                SUPABASE_USER_ID=session["user_id"],
                PSN_NAME=name,
                ONBOARDING_DONE=True,
            )
            result["ok"] = True
            if not saved:
                messagebox.showwarning(
                    "Create a Free Account",
                    "Your account was created, but this PC couldn't save the sign-in "
                    "(is the settings file read-only or open in another program?). "
                    "You may be asked to create an account again next time.")
            if not alive():
                return
            if name_saved:
                win.destroy()
                return
            # Same known Supabase issue as launcher.py's onboarding screen --
            # account/session is fine, just the display-name sync failed.
            # Say so briefly instead of closing as if nothing happened.
            status.config(
                text="Account created — the display name didn't save. "
                     "Submissions use the name you type when submitting, so "
                     "this doesn't matter. Continuing...", fg=ACC)
            win.after(1800, lambda: win.destroy() if alive() else None)

        create_btn.config(command=do_create)
        cancel_btn.config(command=close)

        self.wait_window(win)
        return result["ok"]

    def _after_submit(self, ok, reason="network"):
        self._submitting = False
        if ok:
            messagebox.showinfo("Submit to Leaderboard",
                                 "Submitted! If it's a new top time it'll appear on the leaderboard shortly.")
            self._refresh_top10()
        elif reason == "session":
            runtime_config.SUPABASE_ACCESS_TOKEN = ""
            runtime_config.SUPABASE_REFRESH_TOKEN = ""
            runtime_config.SUPABASE_USER_ID = ""
            runtime_config.save(SUPABASE_ACCESS_TOKEN="", SUPABASE_REFRESH_TOKEN="", SUPABASE_USER_ID="")
            messagebox.showinfo("Submit to Leaderboard",
                                "Your leaderboard account on this PC is no longer valid. "
                                "Create a new free account (just a display name) and submit again.")
            if self._prompt_create_account():
                self._submit_leaderboard()
        elif reason == "data":
            messagebox.showerror("Submit to Leaderboard",
                                  "This lap's data couldn't be prepared for upload (is the file damaged?).")
        elif reason == "server":
            messagebox.showerror("Submit to Leaderboard",
                                  "The server rejected the submission -- this is a server-side issue, "
                                  "not your connection. Try again later.")
        else:
            messagebox.showerror("Submit to Leaderboard",
                                  "Couldn't reach the leaderboard — check your internet connection and try again.")

if __name__ == "__main__":
    theme.enable_dpi_awareness()
    app = AnalystApp()
    app.mainloop()
