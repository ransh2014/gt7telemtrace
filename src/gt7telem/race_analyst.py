# race_analyst.py — GT7 Race Analyst
# pip install pandas matplotlib numpy
import base64
import io
import math
import os
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib
import numpy as np

matplotlib.use("TkAgg")
import warnings

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import LineCollection
from matplotlib.patches import Polygon as MplPolygon

warnings.filterwarnings("ignore")

from . import (  # noqa: E402
    __version__,
    analysis_tools,
    theme,  # noqa: E402
    uiscroll,
)
from . import config as runtime_config  # noqa: E402
from . import labels as L  # noqa: E402

runtime_config.install_mac_buttons()   # macOS ignores Button colours; no-op elsewhere

from .core import registry  # noqa: E402
from .race_charts import (  # noqa: F401, E402
    _L,
    _RACE_NUM_COLS,
    _SCORE_CACHE,
    ACC,
    BG,
    CYN,
    DIM,
    DIM2,
    FG,
    GRN,
    GROUPS,
    ORG,
    PIT_LANE_MAX_KPH,
    PNL,
    PNL2,
    PRP,
    REFUEL_MIN_JUMP,
    YLW,
    C,
    X,
    _ax,
    _dt_series,
    _infer_pit_flag,
    _lap_segments,
    _Lb,
    _Lb_step,
    _no_data,
    _num,
    _refuel_spans,
    _safe_name,
    _save_df_csv,
    _track_map,
    best_lap_baseline,
    build_stats,
    corner_baseline,
    count_pit_stops,
    draw_braking,
    draw_consistency,
    draw_corners,
    draw_dynamics,
    draw_engine,
    draw_fuel,
    draw_gforce,
    draw_inputs,
    draw_laps,
    draw_maps,
    draw_minimap_heatmap,
    draw_race,
    draw_ratings,
    draw_telediff,
    draw_timeline,
    draw_traction,
    draw_tyres,
    export_csv,
    fmt_dur,
    fuel_per_lap_stats,
    get_lap_segments,
    lap_split_stats,
    load_race,
    pb_file_baseline,
    pb_reference_path,
    race_card_png,
    race_card_spec,
    race_label,
    race_lap_frames,
    score_race_corners,
)

FROM_MENU = False   # set by the launcher: the window then offers a "Menu" button to go back
FONT  = (theme.MONO, 9)
FONTB = (theme.MONO, 9, "bold")
FONTL = (theme.MONO, 8)
FONTH = (theme.MONO, 13, "bold")

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
        self._cb        = None
        self._in_update = False
        self._cum_a     = None
        self._cum_b     = None
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
        ang = np.arctan2(dx, dz)
        # Stationary samples would snap the arrow to 0 -- hold the last heading.
        keep = np.where(np.hypot(dx, dz) > 0.05, np.arange(len(ang)), 0)
        keep = np.maximum.accumulate(keep)
        return ang[keep]

    @staticmethod
    def _cum_dist(df):
        return (df["speed_kmh"] / 3.6 * _dt_series(df)).cumsum().to_numpy()

    def _car_verts(self, cx, cz, heading_rad, size):
        local = np.array([[0, size], [-size*0.55, -size*0.65], [size*0.55, -size*0.65]])
        cos_h, sin_h = math.cos(heading_rad), math.sin(heading_rad)
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
        tk.Button(ctrl, text=L.REPLAY_EXPORT_CSV, command=self._export_csv,
                  bg=DIM2, fg=FG, relief="flat", font=FONTL,
                  padx=8, pady=2, cursor="hand2").pack(side="right", padx=8)

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
                cv = tk.Canvas(f, width=theme.px(48), height=theme.px(6), bg=DIM2, highlightthickness=0); cv.pack(pady=1)
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
        self._ax.set_title("Load a race to start replay", color=DIM, fontsize=11)
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

    def _export_csv(self):
        if self._df is None:
            messagebox.showinfo("Export CSV", "Load a race first.")
            return
        default_name = _safe_name(self._la_label) + ".csv"
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

    def load(self, df, label=""):
        self._df = df.reset_index(drop=True)
        self._la_label = label
        self._hdg_a = self._compute_headings(self._df)
        self._cum_a = self._cum_dist(self._df)
        self._rate  = self._sample_rate(self._df)
        self._idx = 0; self._playing = False
        self._pbtn.config(text=L.PLAY, fg=GRN)
        self._sv.set(0); self._delta_var.set("")
        self._draw_base(); self._update()

    def load_b(self, df, label=""):
        self._dfb = df.reset_index(drop=True)
        self._lb_label = label
        self._hdg_b = self._compute_headings(self._dfb)
        self._cum_b = self._cum_dist(self._dfb)
        if not self._b_visible:
            self._info_b_frame.pack(side="left", fill="x", expand=True, padx=(1,0))
            self._mode_btn.pack(side="right", padx=8)
            self._b_visible = True
        if self._df is not None:
            self._draw_base()
        self._update()

    def _draw_base(self):
        df = self._df
        if self._cb is not None:
            try: self._cb.remove()      # restores the map axes' size
            except Exception: pass
            self._cb = None
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
        self._cb = cb
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
        self._in_update = True
        try:
            self._sv.set(int(idx/(max(1,len(df)-1))*1000))
        finally:
            self._in_update = False
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
            elif self._cum_a is not None and self._cum_b is not None:
                # Real-time: gap in distance covered at the same race time,
                # converted to seconds at A's current speed.
                gap_m = float(self._cum_a[idx] - self._cum_b[idx_b])
                spd_ms = max(float(row.get("speed_kmh", 0)) / 3.6, 1.0)
                delta = -gap_m / spd_ms
            else:
                delta = t_b - t_a

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
        try:
            self._update()
        except Exception:
            self._playing = False
            self._pbtn.config(text=L.PLAY, fg=GRN)
            return
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
        if self._df is None or self._in_update: return
        self._idx = int(int(float(val))/1000*(len(self._df)-1))
        self._update()

# ── Main App ──────────────────────────────────────────────────────────────────
class AnalystApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(L.window_title(L.TOOL_RACE, __version__))
        self.configure(bg=BG)
        theme.fit_window(self, 1380, 860, 1100, 700)
        self._da = self._dfa = None
        self._db = self._dfb = None
        self._cfigs        = {}
        self._group_names  = []
        self._compare_mode = False
        self._baseline_mode = "best"     # Corners tab: "best" lap of the race, or the saved "pb" lap
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._quit)

    def _go_menu(self):
        """Back to the launcher menu."""
        self._to_menu = True
        self._quit()

    def _quit(self):
        try:
            self._replay._playing = False
            if self._replay._after_id is not None:
                self._replay._canvas.get_tk_widget().after_cancel(self._replay._after_id)
        except Exception:
            pass
        plt.close("all"); self.destroy()

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
        tk.Label(brand, text=L.TITLE_RACE, fg=CYN, bg=PNL2,
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

        fa = _section("RACE A", CYN)
        self._la = tk.Label(fa, text=L.NOT_LOADED, fg=DIM, bg=PNL, font=FONTL,
                             wraplength=theme.px(236), justify="left")
        self._la.pack(anchor="w", padx=8, pady=2)
        tk.Button(fa, text=L.BROWSE, command=lambda: self._load("a"),
                  bg=DIM2, fg=CYN, relief="flat", font=FONT, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))

        fb = _section("RACE B", ACC)
        self._lb = tk.Label(fb, text=L.NOT_LOADED, fg=DIM, bg=PNL, font=FONTL,
                             wraplength=theme.px(236), justify="left")
        self._lb.pack(anchor="w", padx=8, pady=2)
        tk.Button(fb, text=L.BROWSE, command=lambda: self._load("b"),
                  bg=DIM2, fg=ACC, relief="flat", font=FONT, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,4))

        fe = _section("EXPORT", DIM)
        tk.Button(fe, text=L.EXPORT_CSV, command=self._export_csv,
                  bg=DIM2, fg=GRN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(2,2))
        tk.Button(fe, text=L.EXPORT_CHART_HTML, command=self._export_html,
                  bg=DIM2, fg=CYN, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))
        tk.Button(fe, text=L.EXPORT_RACE_CARD, command=self._export_card,
                  bg=DIM2, fg=PRP, relief="flat", font=FONTL, padx=8, pady=2,
                  cursor="hand2").pack(anchor="w", padx=8, pady=(0,4))

        ft = _section("ANALYSIS TOOLS", GRN)
        for kind, text, colour in registry.RACE_TOOLS:
            tk.Button(ft, text=text, command=lambda k=kind: self._show_tool(k),
                      bg=DIM2, fg=globals()[colour], relief="flat", font=FONTL, padx=8, pady=2,
                      cursor="hand2").pack(anchor="w", padx=8, pady=(2, 2))

        tk.Label(p, text=L.LAP_SPLITS_RACE_A, fg=DIM, bg=PNL2,
                 font=FONTL).pack(anchor="w", padx=12, pady=(6,2))
        self._sec_outer = tk.Frame(p, bg=PNL2); self._sec_outer.pack(fill="both", padx=6, expand=False)
        self._sec_canvas = tk.Canvas(self._sec_outer, bg=PNL2, highlightthickness=0, height=theme.px(140))
        self._sec_scroll = ttk.Scrollbar(self._sec_outer, orient="vertical", command=self._sec_canvas.yview)
        self._sec_canvas.configure(yscrollcommand=self._sec_scroll.set)
        self._sec_scroll.pack(side="right", fill="y")
        self._sec_canvas.pack(side="left", fill="both", expand=True)
        self._sec_f = tk.Frame(self._sec_canvas, bg=PNL2)
        self._sec_win = self._sec_canvas.create_window((0,0), window=self._sec_f, anchor="nw")
        self._sec_f.bind("<Configure>",
                          lambda e: self._sec_canvas.configure(scrollregion=self._sec_canvas.bbox("all")))

        tk.Label(p, text=L.STATS_RACE_A, fg=DIM, bg=PNL2,
                 font=FONTL).pack(anchor="w", padx=12, pady=(6,2))
        self._sf = tk.Frame(p, bg=PNL2); self._sf.pack(fill="x", padx=6)

        fn = tk.Frame(p, bg=PNL, pady=4); fn.pack(fill="x", padx=6, pady=(6,4))
        tk.Label(fn, text=L.RACE_NOTES, fg=DIM, bg=PNL, font=FONTL).pack(anchor="w", padx=8)
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
        self._cmp_btn = tk.Button(hdr, text=L.COMPARE_OFF,
                                   command=self._toggle_compare,
                                   bg=DIM2, fg=DIM, relief="flat", font=FONTB,
                                   padx=10, pady=2, cursor="hand2")
        self._cmp_btn.pack(side="right", padx=10)
        self._base_btn = tk.Button(hdr, text=L.CORNER_BASELINE_BEST,
                                   command=self._toggle_baseline,
                                   bg=DIM2, fg=CYN, relief="flat", font=FONTB,
                                   padx=10, pady=2, cursor="hand2")
        self._base_btn.pack(side="right", padx=4)

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

    def _toggle_baseline(self):
        """Corners tab: score every lap against the race's best lap, or against
        your saved PB lap for this track and car."""
        self._baseline_mode = "pb" if self._baseline_mode == "best" else "best"
        self._base_btn.config(text=L.CORNER_BASELINE_PB if self._baseline_mode == "pb" else L.CORNER_BASELINE_BEST)
        if self._cfigs.get("Corners"):
            plt.close(self._cfigs["Corners"])
        self._cfigs["Corners"] = None
        if self._dfa is not None and self._group_names[self._snb.index("current")] == "Corners":
            self._draw_chart("Corners")

    def _draw_active_chart(self):
        if self._dfa is None: return
        idx  = self._snb.index("current")
        name = self._group_names[idx]
        if self._cfigs.get(name) is not None:
            return      # already drawn for the current races; switching tabs shouldn't rebuild it
        self._draw_chart(name)

    def _draw_chart(self, name):
        if self._dfa is None: return
        f, fn, fs = self._ctabs[name]
        for w in f.winfo_children(): w.destroy()
        if self._cfigs.get(name):
            plt.close(self._cfigs[name])
        fig = plt.figure(figsize=fs, facecolor=BG)
        dfb = self._dfb if self._compare_mode else None
        try:
            if name == "Corners":
                fn(fig, self._dfa, dfb, corner_baseline(self._da or {}, self._dfa, self._baseline_mode))
            else:
                fn(fig, self._dfa, dfb)
        except Exception as e:
            plt.close(fig)
            self._cfigs[name] = None
            tk.Label(f, text=f"Couldn't draw this chart:\n{type(e).__name__}: {e}",
                     fg=ACC, bg=BG, font=FONT, justify="left").pack(padx=20, pady=20)
            return
        # The figure keeps a readable minimum size and the pane scrolls when the window is smaller.
        host = uiscroll.scroll_view(f, BG, int(fig.get_figwidth() * fig.dpi * 0.8),
                                    int(fig.get_figheight() * fig.dpi * 0.8))
        cv = FigureCanvasTkAgg(fig, master=host)
        cv.draw(); cv.get_tk_widget().pack(fill="both", expand=True)
        self._cfigs[name] = fig

    # ── load ──────────────────────────────────────────────────────────────────
    def _load(self, slot):
        _start = str(runtime_config.LAPS_FOLDER)
        path = filedialog.askopenfilename(
            title=f"Load Race {slot.upper()}",
            initialdir=_start if os.path.isdir(_start) else None,
            filetypes=[("JSON","*.json"),("All","*.*")])
        if not path: return
        try:
            data, df = load_race(path)
            lbl = race_label(data)
        except Exception as e:
            messagebox.showerror("Load Error", f"{type(e).__name__}: {e}"); return
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

    # ── analysis tools across this race's laps ────────────────────────────────
    def _show_tool(self, kind):
        if self._dfa is None:
            messagebox.showinfo("Analysis tools", "Load Race A first.")
            return
        try:
            text = analysis_tools.race_report(kind, race_lap_frames(self._dfa))
        except Exception as e:
            messagebox.showerror("Analysis tools", f"Couldn't run that tool on this race: {e}")
            return
        win = tk.Toplevel(self)
        win.title("TRACE - Analysis tools")
        win.configure(bg=BG)
        theme.fit_window(win, 760, 460, 480, 280)
        box = tk.Text(win, bg=PNL2, fg=FG, font=FONT, wrap="word", relief="flat", padx=12, pady=10)
        box.pack(fill="both", expand=True)
        box.insert("1.0", text)
        box.config(state="disabled")

    # ── exports ───────────────────────────────────────────────────────────────
    def _export_csv(self):
        if self._dfa is None:
            messagebox.showinfo("Export", "Load Race A first."); return

        dlg = tk.Toplevel(self)
        dlg.title("Export CSV"); dlg.configure(bg=PNL2)
        dlg.geometry(f"{theme.px(265)}x{theme.px(155)}"); dlg.resizable(False, False)
        dlg.transient(self)
        try:
            dlg.wait_visibility(); dlg.grab_set()
        except tk.TclError:
            pass

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
                    try:
                        _save_df_csv(self._dfa, p)
                        messagebox.showinfo("Exported", f"Race A  →  {Path(p).name}")
                    except Exception as e:
                        messagebox.showerror("Export failed", f"Race A: {e}")
            if sel in ("b", "both"):
                if self._dfb is None:
                    messagebox.showinfo("Export", "Race B not loaded."); return
                p = filedialog.asksaveasfilename(title="Save Race B CSV",
                    defaultextension=".csv", filetypes=[("CSV","*.csv"),("All","*.*")])
                if p:
                    try:
                        _save_df_csv(self._dfb, p)
                        messagebox.showinfo("Exported", f"Race B  →  {Path(p).name}")
                    except Exception as e:
                        messagebox.showerror("Export failed", f"Race B: {e}")

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
        try:
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=BG)
            b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            messagebox.showerror("Export failed", str(e)); return
        html = (
            "<!DOCTYPE html><html><head>"
            # Written as UTF-8 below; without this meta a browser opening the
            # file over file:// falls back to the locale encoding and the em
            # dash in the title renders as mojibake.
            '<meta charset="utf-8">'
            f"<title>TRACE Race Analyst — {name}</title>"
            "<style>body{background:#07080f;display:flex;justify-content:center;"
            "align-items:flex-start;min-height:100vh;margin:0;padding:20px;box-sizing:border-box}"
            "img{max-width:100%;border-radius:8px;box-shadow:0 0 30px #00f0d444}"
            "h3{color:#00f0d4;font-family:monospace;text-align:center}</style></head>"
            f"<body><div><h3>TRACE Race Analyst — {name}</h3>"
            f"<img src='data:image/png;base64,{b64}'></div></body></html>"
        )
        try:
            Path(path).write_text(html, encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Export failed", f"Couldn't write {path}:\n{e}"); return
        messagebox.showinfo("Exported", f"Saved: {Path(path).name}")

    def _export_card(self):
        if self._dfa is None:
            messagebox.showinfo("Export", "Load Race A first."); return
        d = self._da or {}
        base = _safe_name(f"TRACE_race_{d.get('track') or 'race'}_{fmt_dur(d.get('race_duration_s'))}")
        path = filedialog.asksaveasfilename(
            title="Save Race Card", defaultextension=".png", initialfile=f"{base}.png",
            filetypes=[("PNG image", "*.png"), ("All", "*.*")])
        if not path: return
        try:
            data = race_card_png(d, self._dfa)
            tmp = str(path) + ".part"
            with open(tmp, "wb") as f: f.write(data)
            os.replace(tmp, path)
        except Exception as e:
            messagebox.showerror("Race Card", f"Couldn't create the race card:\n{e}"); return
        messagebox.showinfo("Exported", f"Race card saved: {Path(path).name}")

    def _on_tab(self, e):
        try:
            if self._nb.index("current") == 1:
                self._draw_active_chart()
        except Exception: pass

if __name__ == "__main__":
    theme.enable_dpi_awareness()
    app = AnalystApp()
    app.mainloop()
