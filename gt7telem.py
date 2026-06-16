# gt7telem2.py — GT7 Live Telemetry Viewer (v2)
# New in v2:
#   - Live mini track map canvas (right panel, 240x180)
#   - Tyre hot / cold / fuel-low alert banners (flashing)
#   - Lap history Treeview (scrollable, right panel)

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import threading
import time
import json
import math
from collections import deque
from pathlib import Path
from datetime import datetime

import gt7udp as telem
from config import PS_IP as PS4_IP, LAPS_FOLDER, SAMPLE_RATE

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def ms_to_laptime(ms):
    if ms <= 0:
        return "--:--.---"
    s = ms / 1000.0
    m = int(s // 60)
    s = s % 60
    return f"{m}:{s:06.3f}"

def temp_color(val, cold=60, good=80, hot=100):
    if val <= 0:   return "#555"
    if val < cold: return "#4fc3f7"
    if val < good: return "#2ecc71"
    if val < hot:  return "#f39c12"
    return "#e74c3c"

def slip_color(val):
    if val <= 0:   return "#555"
    if val < 0.95: return "#e74c3c"
    if val < 1.05: return "#2ecc71"
    if val < 1.15: return "#f39c12"
    return "#e74c3c"

# ─────────────────────────────────────────────────────────────────────────────
# Recording state
# ─────────────────────────────────────────────────────────────────────────────
class Session:
    recording  = False
    waiting    = False
    samples    = []
    start_t    = 0.0
    cur_lap    = 0
    prev_lap   = 0
    laps_saved = []

session = Session()

# ─────────────────────────────────────────────────────────────────────────────
# Main App
# ─────────────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GT7 Telemetry v2")
        self.configure(bg="#0a0a12")
        self.geometry("1280x920")
        self.minsize(1100, 700)
        self._fuel_per_lap      = 0.0
        self._fuel_at_lap_start = 0.0
        self._track_pts  = deque(maxlen=3000)
        self._flash_tick = 0
        self._flash_on   = False
        self._saved_laps = 0
        self._build()
        self._start_telem()
        self.after(200, self._poll)

    # ─────────────────────────────────────────────────────────────────────────
    # Build UI
    # ─────────────────────────────────────────────────────────────────────────
    def _build(self):
        BG  = "#0a0a12"
        PNL = "#0f0f1e"
        ACC = "#e94560"
        HI  = "#4fc3f7"
        FG  = "#c0c0e0"
        DIM = "#555566"

        # ── Header ───────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg="#0f3460", pady=6)
        hdr.pack(fill="x")

        tk.Label(hdr, text="GT7 TELEMETRY v2", fg=ACC, bg="#0f3460",
                 font=("Consolas", 14, "bold")).pack(side="left", padx=16)
        self.conn_dot = tk.Label(hdr, text="● OFFLINE", fg=DIM, bg="#0f3460",
                                 font=("Consolas", 10))
        self.conn_dot.pack(side="left", padx=8)

        tk.Label(hdr, text="PS4 IP", fg=DIM, bg="#0f3460",
                 font=("Consolas", 8)).pack(side="right", padx=(6, 2))
        self.ip_var = tk.StringVar(value=PS4_IP)
        ip_entry = tk.Entry(hdr, textvariable=self.ip_var, bg="#16213e", fg=HI,
                            font=("Consolas", 9), width=15, relief="flat",
                            insertbackground=HI)
        ip_entry.pack(side="right", padx=(0, 6))
        ip_entry.bind("<Return>",   lambda e: self._on_ip_change())
        ip_entry.bind("<FocusOut>", lambda e: self._on_ip_change())

        self.rec_btn = tk.Button(hdr, text="Record Lap",
                                 command=self._toggle_record,
                                 bg="#16213e", fg=FG, relief="flat",
                                 font=("Consolas", 10, "bold"), padx=12, pady=3)
        self.rec_btn.pack(side="right", padx=6)
        tk.Button(hdr, text="Export Session",
                  command=self._export_session,
                  bg="#16213e", fg=FG, relief="flat",
                  font=("Consolas", 10), padx=12, pady=3).pack(side="right", padx=4)

        tk.Label(hdr, text="TRACK", fg=DIM, bg="#0f3460",
                 font=("Consolas", 9)).pack(side="right", padx=(8, 2))
        self.track_var = tk.StringVar(value="")
        tk.Entry(hdr, textvariable=self.track_var, bg="#16213e", fg=FG,
                 font=("Consolas", 10), width=20, relief="flat",
                 insertbackground=FG).pack(side="right", padx=(0, 4))

        tk.Label(hdr, text="CAR", fg=DIM, bg="#0f3460",
                 font=("Consolas", 9)).pack(side="right", padx=(8, 2))
        self.car_var = tk.StringVar(value="")
        tk.Entry(hdr, textvariable=self.car_var, bg="#16213e", fg=FG,
                 font=("Consolas", 10), width=18, relief="flat",
                 insertbackground=FG).pack(side="right", padx=(0, 4))

        # ── Body ─────────────────────────────────────────────────────────────
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=6)

        # Scrollable left panel
        left_outer = tk.Frame(body, bg=BG)
        left_outer.pack(side="left", fill="both", expand=True)
        _lcanvas = tk.Canvas(left_outer, bg=BG, highlightthickness=0)
        _lscroll = ttk.Scrollbar(left_outer, orient="vertical", command=_lcanvas.yview)
        _lcanvas.configure(yscrollcommand=_lscroll.set)
        _lscroll.pack(side="right", fill="y")
        _lcanvas.pack(side="left", fill="both", expand=True)
        left = tk.Frame(_lcanvas, bg=BG)
        _lwin = _lcanvas.create_window((0, 0), window=left, anchor="nw")

        def _on_left_configure(e):
            _lcanvas.configure(scrollregion=_lcanvas.bbox("all"))
        def _on_canvas_resize(e):
            _lcanvas.itemconfig(_lwin, width=e.width)
        left.bind("<Configure>", _on_left_configure)
        _lcanvas.bind("<Configure>", _on_canvas_resize)
        _lcanvas.bind_all("<MouseWheel>",
                          lambda e: _lcanvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        _lcanvas.bind_all("<Button-4>", lambda e: _lcanvas.yview_scroll(-1, "units"))
        _lcanvas.bind_all("<Button-5>", lambda e: _lcanvas.yview_scroll(1, "units"))

        right = tk.Frame(body, bg=BG, width=260)
        right.pack(side="right", fill="y", padx=(6, 0))
        right.pack_propagate(False)

        # ── Speed + Gear ──────────────────────────────────────────────────────
        sg = tk.Frame(left, bg=PNL, pady=8)
        sg.pack(fill="x", pady=(0, 4))
        self.speed_lbl = tk.Label(sg, text="0", fg=HI, bg=PNL,
                                  font=("Consolas", 64, "bold"))
        self.speed_lbl.pack(side="left", padx=20)
        tk.Label(sg, text="km/h", fg=DIM, bg=PNL,
                 font=("Consolas", 14)).pack(side="left", pady=(24, 0))
        sg_mid = tk.Frame(sg, bg=PNL)
        sg_mid.pack(side="left", expand=True)
        tk.Label(sg_mid, text="SUGGEST", fg=DIM, bg=PNL, font=("Consolas", 8)).pack()
        self.sug_gear_lbl = tk.Label(sg_mid, text="--", fg="#f39c12", bg=PNL,
                                     font=("Consolas", 20, "bold"))
        self.sug_gear_lbl.pack()
        self.inpit_lbl = tk.Label(sg_mid, text="", fg="#e94560", bg=PNL,
                                  font=("Consolas", 9, "bold"))
        self.inpit_lbl.pack()
        self.gear_lbl = tk.Label(sg, text="N", fg="#f39c12", bg=PNL,
                                 font=("Consolas", 64, "bold"))
        self.gear_lbl.pack(side="right", padx=20)
        tk.Label(sg, text="gear", fg=DIM, bg=PNL,
                 font=("Consolas", 14)).pack(side="right", pady=(24, 0))

        # ── Lap info ──────────────────────────────────────────────────────────
        lap_f = tk.Frame(left, bg=PNL, pady=6)
        lap_f.pack(fill="x", pady=(0, 4))
        for attr, label, col in [
            ("lap_lbl", "LAP", FG), ("cur_lbl", "CURRENT", HI),
            ("best_lbl", "BEST", "#2ecc71"), ("last_lbl", "LAST", FG),
            ("pos_lbl", "POS", FG), ("topspd_lbl", "TOP SPD", HI),
        ]:
            col_f = tk.Frame(lap_f, bg=PNL)
            col_f.pack(side="left", expand=True)
            tk.Label(col_f, text=label, fg=DIM, bg=PNL, font=("Consolas", 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=col, bg=PNL, font=("Consolas", 13, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Progress bars ─────────────────────────────────────────────────────
        def bar_row(parent, label, av, al, fc):
            f = tk.Frame(parent, bg=BG)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=label, fg=DIM, bg=BG,
                     font=("Consolas", 9), width=9).pack(side="left", padx=4)
            var = tk.DoubleVar()
            ttk.Progressbar(f, variable=var, maximum=100,
                            length=300, mode="determinate").pack(
                side="left", fill="x", expand=True, padx=4)
            lbl = tk.Label(f, text="0", fg=fc, bg=BG, font=("Consolas", 10), width=10)
            lbl.pack(side="left")
            setattr(self, av, var)
            setattr(self, al, lbl)

        bar_row(left, "RPM",      "rpm_var", "rpm_lbl", "#e74c3c")
        bar_row(left, "THROTTLE", "thr_var", "thr_lbl", "#2ecc71")
        bar_row(left, "BRAKE",    "brk_var", "brk_lbl", "#e74c3c")
        bar_row(left, "CLUTCH",   "clt_var", "clt_lbl", "#f39c12")

        # ── Tyres ─────────────────────────────────────────────────────────────
        tyre_f = tk.LabelFrame(left, text=" TYRES ", fg=ACC, bg=BG, font=("Consolas", 9))
        tyre_f.pack(fill="x", pady=4, padx=2)
        for label, ta, sa in [("FL", "tyre_fl", "slip_fl"), ("FR", "tyre_fr", "slip_fr"),
                               ("RL", "tyre_rl", "slip_rl"), ("RR", "tyre_rr", "slip_rr")]:
            col_f = tk.Frame(tyre_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=8, pady=4)
            tk.Label(col_f, text=label, fg=DIM, bg=BG, font=("Consolas", 9)).pack()
            t_lbl = tk.Label(col_f, text="--C", fg=HI, bg=BG, font=("Consolas", 13, "bold"))
            t_lbl.pack()
            s_lbl = tk.Label(col_f, text="slip --", fg=DIM, bg=BG, font=("Consolas", 8))
            s_lbl.pack()
            setattr(self, ta, t_lbl)
            setattr(self, sa, s_lbl)

        # ── Suspension ────────────────────────────────────────────────────────
        susp_f = tk.LabelFrame(left, text=" SUSPENSION (mm) ", fg=ACC, bg=BG, font=("Consolas", 9))
        susp_f.pack(fill="x", pady=4, padx=2)
        for label, attr in [("FL", "susp_fl_lbl"), ("FR", "susp_fr_lbl"),
                             ("RL", "susp_rl_lbl"), ("RR", "susp_rr_lbl")]:
            col_f = tk.Frame(susp_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=8, pady=4)
            tk.Label(col_f, text=label, fg=DIM, bg=BG, font=("Consolas", 9)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=("Consolas", 12, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Engine and Fluids ─────────────────────────────────────────────────
        eng_f = tk.LabelFrame(left, text=" ENGINE AND FLUIDS ", fg=ACC, bg=BG, font=("Consolas", 9))
        eng_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("FUEL", "fuel_lbl"), ("FUEL LAPS", "fuellaps_lbl"),
                             ("BOOST", "boost_lbl"), ("OIL C", "oil_lbl"),
                             ("WATER C", "water_lbl"), ("OIL kPa", "oilp_lbl"),
                             ("RIDE mm", "ride_lbl")]:
            col_f = tk.Frame(eng_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=("Consolas", 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=("Consolas", 12, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Dynamics ─────────────────────────────────────────────────────────
        dyn_f = tk.LabelFrame(left, text=" DYNAMICS ", fg=ACC, bg=BG, font=("Consolas", 9))
        dyn_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("VEL X", "velx_lbl"), ("VEL Y", "vely_lbl"),
                             ("VEL Z", "velz_lbl"), ("ANG X", "angx_lbl"),
                             ("ANG Y", "angy_lbl"), ("ANG Z", "angz_lbl"),
                             ("HEADING", "hdg_lbl")]:
            col_f = tk.Frame(dyn_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=4, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=("Consolas", 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=("Consolas", 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Position ──────────────────────────────────────────────────────────
        pos_f = tk.LabelFrame(left, text=" POSITION ", fg=ACC, bg=BG, font=("Consolas", 9))
        pos_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("WORLD X", "wx_lbl"), ("WORLD Y", "wy_lbl"),
                             ("WORLD Z", "wz_lbl"), ("TRACK POS", "tpos_lbl")]:
            col_f = tk.Frame(pos_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=("Consolas", 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=("Consolas", 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Drivetrain extras ─────────────────────────────────────────────────
        drv_f = tk.LabelFrame(left, text=" DRIVETRAIN EXTRAS ", fg=ACC, bg=BG, font=("Consolas", 9))
        drv_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("STEERING", "steer_lbl"), ("CLUTCH ENG", "clteng_lbl"),
                             ("RPM WARN", "rpmwarn_lbl"), ("RPM LIM", "rpmlim_lbl"),
                             ("RPM@CLUTCH", "rpmclt_lbl")]:
            col_f = tk.Frame(drv_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=("Consolas", 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=("Consolas", 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        gr_f = tk.Frame(left, bg=BG)
        gr_f.pack(fill="x", pady=(0, 4), padx=4)
        tk.Label(gr_f, text="GEAR RATIOS:", fg=DIM, bg=BG, font=("Consolas", 8)).pack(side="left", padx=4)
        self.gr_lbl = tk.Label(gr_f, text="--", fg=FG, bg=BG, font=("Consolas", 8))
        self.gr_lbl.pack(side="left")

        # ── Session misc ──────────────────────────────────────────────────────
        misc_f = tk.LabelFrame(left, text=" SESSION / MISC ", fg=ACC, bg=BG, font=("Consolas", 9))
        misc_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("TIME OF DAY", "tod_lbl"), ("IS EV", "isev_lbl"),
                             ("CAR ID", "carid_lbl"), ("FLAG 8E", "f8e_lbl"),
                             ("FLAG 8F", "f8f_lbl"), ("FLAG 93", "f93_lbl")]:
            col_f = tk.Frame(misc_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=("Consolas", 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=("Consolas", 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # =====================================================================
        # RIGHT PANEL  -  alerts / track map / lap history / log
        # =====================================================================

        # ── Alerts row ───────────────────────────────────────────────────────
        af = tk.Frame(right, bg=BG)
        af.pack(fill="x", pady=(0, 4))

        self.alert_hot  = tk.Label(af, text="TYRE HOT",  bg="#e74c3c", fg="#fff",
                                   font=("Consolas", 8, "bold"), padx=5, pady=2)
        self.alert_cold = tk.Label(af, text="TYRE COLD", bg="#1a6a8a", fg="#4fc3f7",
                                   font=("Consolas", 8, "bold"), padx=5, pady=2)
        self.alert_fuel = tk.Label(af, text="FUEL LOW",  bg="#f39c12", fg="#000",
                                   font=("Consolas", 8, "bold"), padx=5, pady=2)
        # hidden until triggered — _poll calls grid / grid_remove
        self.alert_hot.grid( in_=af, row=0, column=0, padx=2, pady=1)
        self.alert_cold.grid(in_=af, row=0, column=1, padx=2, pady=1)
        self.alert_fuel.grid(in_=af, row=0, column=2, padx=2, pady=1)
        self.alert_hot.grid_remove()
        self.alert_cold.grid_remove()
        self.alert_fuel.grid_remove()

        # ── Track map ────────────────────────────────────────────────────────
        tk.Label(right, text="TRACK MAP", fg=ACC, bg=BG,
                 font=("Consolas", 9, "bold")).pack(anchor="w", padx=2)
        self.map_canvas = tk.Canvas(right, bg="#050510", width=240, height=180,
                                    highlightthickness=1, highlightbackground=DIM)
        self.map_canvas.pack(pady=(0, 4))
        self.map_canvas.create_text(120, 90, text="WAITING FOR DATA",
                                    fill="#333344", font=("Consolas", 9))

        # ── Lap history ──────────────────────────────────────────────────────
        tk.Label(right, text="LAP HISTORY", fg=ACC, bg=BG,
                 font=("Consolas", 9, "bold")).pack(anchor="w", padx=2)

        hist_outer = tk.Frame(right, bg=BG)
        hist_outer.pack(fill="x", pady=(0, 4))

        st = ttk.Style()
        st.configure("Hist.Treeview",
                     background="#050510", foreground="#c0c0e0",
                     fieldbackground="#050510",
                     font=("Consolas", 8), rowheight=18)
        st.configure("Hist.Treeview.Heading",
                     background="#0f0f1e", foreground=ACC,
                     font=("Consolas", 8, "bold"))
        st.map("Hist.Treeview",
               background=[("selected", "#1a3a5a")],
               foreground=[("selected", "#4fc3f7")])

        self.hist_tree = ttk.Treeview(
            hist_outer,
            columns=("lap", "time", "track", "car"),
            show="headings", height=6,
            style="Hist.Treeview"
        )
        self.hist_tree.heading("lap",   text="#",    anchor="center")
        self.hist_tree.heading("time",  text="Time", anchor="center")
        self.hist_tree.heading("track", text="Track", anchor="w")
        self.hist_tree.heading("car",   text="Car",   anchor="w")
        self.hist_tree.column("lap",   width=26, minwidth=26, stretch=False)
        self.hist_tree.column("time",  width=74, minwidth=74, stretch=False)
        self.hist_tree.column("track", width=70, minwidth=50, stretch=True)
        self.hist_tree.column("car",   width=66, minwidth=50, stretch=True)

        hist_sb = ttk.Scrollbar(hist_outer, orient="vertical",
                                 command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=hist_sb.set)
        hist_sb.pack(side="right", fill="y")
        self.hist_tree.pack(side="left", fill="both", expand=True)

        # ── Log ──────────────────────────────────────────────────────────────
        tk.Label(right, text="LOG", fg=ACC, bg=BG,
                 font=("Consolas", 9, "bold")).pack(anchor="w", pady=(2, 2), padx=2)
        self.log = scrolledtext.ScrolledText(right, bg="#050510", fg="#00ff88",
                                             font=("Consolas", 8), relief="flat")
        self.log.pack(fill="both", expand=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Track map draw
    # ─────────────────────────────────────────────────────────────────────────
    def _draw_track_map(self):
        c = self.map_canvas
        c.delete("all")
        pts = list(self._track_pts)

        if len(pts) < 2:
            c.create_text(120, 90, text="WAITING FOR DATA",
                          fill="#333344", font=("Consolas", 9))
            return

        W, H, PAD = 240, 180, 14
        xs = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        min_x = min(xs); max_x = max(xs)
        min_z = min(zs); max_z = max(zs)
        rng_x = max_x - min_x or 1
        rng_z = max_z - min_z or 1

        def to_cv(x, z):
            cx = PAD + (x - min_x) / rng_x * (W - 2 * PAD)
            cy = PAD + (z - min_z) / rng_z * (H - 2 * PAD)
            return cx, cy

        # Full trace downsampled to max 400 pts
        step = max(1, len(pts) // 400)
        sparse = pts[::step]
        if len(sparse) >= 2:
            coords = []
            for x, z in sparse:
                coords.extend(to_cv(x, z))
            c.create_line(*coords, fill="#1a3a5a", width=2, smooth=True)

        # Recent 80 pts in bright cyan
        recent = pts[-80:]
        if len(recent) >= 2:
            rc = []
            for x, z in recent:
                rc.extend(to_cv(x, z))
            c.create_line(*rc, fill="#4fc3f7", width=2, smooth=True)

        # Current position dot
        cx, cy = to_cv(*pts[-1])
        c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5,
                      fill="#e94560", outline="#fff", width=1)

    # ─────────────────────────────────────────────────────────────────────────
    # IP change
    # ─────────────────────────────────────────────────────────────────────────
    def _on_ip_change(self):
        new_ip = self.ip_var.get().strip()
        if not new_ip:
            return
        telem.set_ip(new_ip)
        self.log_msg(f"IP changed to {new_ip}  reconnecting...")
        threading.Thread(target=self._connect_thread, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # Telemetry start
    # ─────────────────────────────────────────────────────────────────────────
    def _start_telem(self):
        threading.Thread(target=self._connect_thread, daemon=True).start()

    def _connect_thread(self):
        self.log_msg(f"Connecting to {self.ip_var.get().strip()}...")
        result = telem.wait_for_connection(timeout=60)
        if result is not None:
            self.log_msg("Connected! Telemetry live.")
        else:
            self.log_msg("Timeout -- check PS4_IP in config.py")

    # ─────────────────────────────────────────────────────────────────────────
    # Poll  (10 Hz)
    # ─────────────────────────────────────────────────────────────────────────
    def _poll(self):
        d  = telem.get_snapshot()
        ok = telem.is_connected()

        self.conn_dot.config(
            text="● LIVE" if ok else "● OFFLINE",
            fg="#2ecc71" if ok else "#555"
        )

        if not ok:
            self.after(100, self._poll)
            return

        FG = "#c0c0e0"

        # Speed / Gear
        spd  = float(d.get("speed_kmh") or 0)
        gear = int(d.get("gear") or 0)
        sug  = int(d.get("suggested_gear") or 0)
        rpm  = float(d.get("rpm") or 0)
        mrpm = float(d.get("max_rpm") or 1) or 1
        thr  = float(d.get("throttle") or 0)
        brk  = float(d.get("brake") or 0)
        clt  = float(d.get("clutch") or 0)

        self.speed_lbl.config(text=f"{spd:.0f}")
        self.gear_lbl.config(text=str(gear) if gear > 0 else "N")
        self.sug_gear_lbl.config(text=str(sug) if sug > 0 else "--")
        self.inpit_lbl.config(text="PIT" if d.get("in_pit") else "")

        self.rpm_var.set(min(100, rpm / mrpm * 100))
        self.rpm_lbl.config(text=f"{rpm:.0f} / {int(mrpm)}")
        self.thr_var.set(thr * 100)
        self.thr_lbl.config(text=f"{thr * 100:.0f}%")
        self.brk_var.set(brk * 100)
        self.brk_lbl.config(text=f"{brk * 100:.0f}%")
        self.clt_var.set(clt * 100)
        self.clt_lbl.config(text=f"{clt * 100:.0f}%")

        # Lap info
        cur_lap = int(d.get("lap_number") or 0)
        tot_lap = int(d.get("total_laps") or 0)
        cur_pos = int(d.get("current_position") or 0)
        tot_pos = int(d.get("total_positions") or 0)
        best_ms = int(d.get("best_lap_ms") or -1)
        last_ms = int(d.get("last_lap_ms") or -1)
        top_spd = int(d.get("est_top_speed") or 0)

        self.lap_lbl.config(text=f"{cur_lap}/{tot_lap}")
        self.best_lbl.config(text=ms_to_laptime(best_ms))
        self.last_lbl.config(text=ms_to_laptime(last_ms))
        self.pos_lbl.config(text=f"{cur_pos}/{tot_pos}" if cur_pos else "--")
        self.topspd_lbl.config(text=f"{top_spd} km/h" if top_spd else "--")

        # Tyres -- collect live temps for alert check
        tyre_temps_live = []
        for attr, tk_key, sk_key, sa in [
            ("tyre_fl", "tyre_temp_fl", "tyre_slip_fl", "slip_fl"),
            ("tyre_fr", "tyre_temp_fr", "tyre_slip_fr", "slip_fr"),
            ("tyre_rl", "tyre_temp_rl", "tyre_slip_rl", "slip_rl"),
            ("tyre_rr", "tyre_temp_rr", "tyre_slip_rr", "slip_rr"),
        ]:
            tmp = float(d.get(tk_key) or 0)
            slp = float(d.get(sk_key) or 1)
            getattr(self, attr).config(
                text=f"{tmp:.0f}C" if tmp > 0 else "--C",
                fg=temp_color(tmp))
            getattr(self, sa).config(
                text=f"slip {slp:.2f}", fg=slip_color(slp))
            if tmp > 0:
                tyre_temps_live.append(tmp)

        # Suspension
        for attr, key in [("susp_fl_lbl", "susp_fl"), ("susp_fr_lbl", "susp_fr"),
                           ("susp_rl_lbl", "susp_rl"), ("susp_rr_lbl", "susp_rr")]:
            getattr(self, attr).config(text=f"{float(d.get(key) or 0):.1f}")

        # Engine and Fluids
        fuel_r = float(d.get("fuel_remaining") or 0)
        fuel_c = float(d.get("fuel_capacity") or 0)
        is_ev  = bool(d.get("is_ev"))
        fuel_str = f"{fuel_r:.1f}kWh" if is_ev else f"{fuel_r:.1f}L"
        if fuel_c > 0:
            fuel_str += f"/{fuel_c:.0f}"
        self.fuel_lbl.config(text=fuel_str)

        _fuel_laps_num = (fuel_r / self._fuel_per_lap) \
                         if (fuel_r > 0 and self._fuel_per_lap > 0) else 999
        laps_rem = f"{_fuel_laps_num:.1f}" if _fuel_laps_num < 999 else "--"
        self.fuellaps_lbl.config(text=laps_rem)

        boost = float(d.get("boost") or 0)
        self.boost_lbl.config(text=f"{boost:.2f}" if boost > 0 else "--")
        self.oil_lbl.config(text=f"{float(d.get('oil_temp') or 0):.0f}")
        self.water_lbl.config(text=f"{float(d.get('water_temp') or 0):.0f}")
        self.oilp_lbl.config(text=f"{float(d.get('oil_pressure') or 0):.1f}")
        self.ride_lbl.config(text=f"{float(d.get('ride_height_mm') or 0):.0f}")

        # Dynamics
        self.velx_lbl.config(text=f"{float(d.get('vel_x') or 0):.2f}")
        self.vely_lbl.config(text=f"{float(d.get('vel_y') or 0):.2f}")
        self.velz_lbl.config(text=f"{float(d.get('vel_z') or 0):.2f}")
        self.angx_lbl.config(text=f"{float(d.get('ang_x') or 0):.3f}")
        self.angy_lbl.config(text=f"{float(d.get('ang_y') or 0):.3f}")
        self.angz_lbl.config(text=f"{float(d.get('ang_z') or 0):.3f}")
        self.hdg_lbl.config(
            text=f"{math.degrees(float(d.get('heading') or 0)):.1f}deg")

        # Position
        self.wx_lbl.config(text=f"{float(d.get('world_x') or 0):.1f}")
        self.wy_lbl.config(text=f"{float(d.get('world_y') or 0):.1f}")
        self.wz_lbl.config(text=f"{float(d.get('world_z') or 0):.1f}")
        self.tpos_lbl.config(text=f"{float(d.get('track_position') or 0):.1f} m")

        # Drivetrain extras
        self.steer_lbl.config(text=f"{float(d.get('steering') or 0):+.2f}")
        self.clteng_lbl.config(text=f"{float(d.get('clutch_engaged') or 0):.2f}")
        self.rpmwarn_lbl.config(text=f"{int(d.get('rpm_warning') or 0)}")
        self.rpmlim_lbl.config(text=f"{int(d.get('rpm_limiter') or 0)}")
        self.rpmclt_lbl.config(text=f"{float(d.get('rpm_after_clutch') or 0):.0f}")

        ratios = d.get("gear_ratios") or []
        if ratios:
            gr_str = "  ".join(
                f"G{i+1}:{r:.3f}" for i, r in enumerate(ratios) if r > 0
            )
            self.gr_lbl.config(text=gr_str or "--")

        # Session misc
        tod   = int(d.get("time_of_day") or 0)
        tod_h = (tod // 3600000) % 24
        tod_m = (tod % 3600000) // 60000
        tod_s = (tod % 60000) // 1000
        self.tod_lbl.config(text=f"{tod_h:02d}:{tod_m:02d}:{tod_s:02d}")
        self.isev_lbl.config(
            text="YES" if d.get("is_ev") else "no",
            fg="#2ecc71" if d.get("is_ev") else FG)
        self.carid_lbl.config(text=str(int(d.get("car_id") or 0)))
        self.f8e_lbl.config(text=f"0x{int(d.get('flags_8e') or 0):02X}")
        self.f8f_lbl.config(text=f"0x{int(d.get('flags_8f') or 0):02X}")
        self.f93_lbl.config(text=f"0x{int(d.get('flags_93') or 0):02X}")

        # ── Alerts ────────────────────────────────────────────────────────────
        self._flash_tick = (self._flash_tick + 1) % 4
        self._flash_on   = self._flash_tick < 2

        has_hot  = any(t > 100 for t in tyre_temps_live)
        has_cold = any(t < 60  for t in tyre_temps_live)
        has_fuel = _fuel_laps_num < 2

        if has_hot:
            self.alert_hot.config(bg="#e74c3c" if self._flash_on else "#6a1010")
            self.alert_hot.grid()
        else:
            self.alert_hot.grid_remove()

        if has_cold:
            self.alert_cold.config(bg="#1a6a8a" if self._flash_on else "#0a3040")
            self.alert_cold.grid()
        else:
            self.alert_cold.grid_remove()

        if has_fuel:
            self.alert_fuel.config(bg="#f39c12" if self._flash_on else "#6a3a00")
            self.alert_fuel.grid()
        else:
            self.alert_fuel.grid_remove()

        # ── Track map update ──────────────────────────────────────────────────
        wx = float(d.get("world_x") or 0)
        wz = float(d.get("world_z") or 0)
        if wx != 0 or wz != 0:
            if self._track_pts:
                last_x, last_z = self._track_pts[-1]
                jump = math.sqrt((wx - last_x) ** 2 + (wz - last_z) ** 2)
                if jump > 500:
                    self._track_pts.clear()
                    self.log_msg("Track map reset (position jump)")
            moved = (not self._track_pts or
                     abs(wx - self._track_pts[-1][0]) > 0.5 or
                     abs(wz - self._track_pts[-1][1]) > 0.5)
            if moved:
                self._track_pts.append((wx, wz))
        self._draw_track_map()

        # ── Recording ─────────────────────────────────────────────────────────
        if session.recording or session.waiting:
            self._record_sample(d)

        self.after(100, self._poll)

    # ─────────────────────────────────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────────────────────────────────
    def log_msg(self, msg):
        self.after(0, self._log_append, msg)

    def _log_append(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")

    # ─────────────────────────────────────────────────────────────────────────
    # Record
    # ─────────────────────────────────────────────────────────────────────────
    def _toggle_record(self):
        if session.recording or session.waiting:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self):
        session.waiting   = True
        session.recording = False
        session.samples   = []
        session.cur_lap   = telem.get_int("lap_number")
        session.prev_lap  = session.cur_lap
        telem.reset_lap()
        self.rec_btn.config(text="Waiting for start line...", bg="#f39c12")
        self.log_msg("Armed -- cross the start line to begin recording")

    def _stop_record(self):
        session.recording = False
        session.waiting   = False
        self.rec_btn.config(text="Record Lap", bg="#16213e")
        if session.samples:
            self._save_lap(session.samples, lap_time_ms=None, incomplete=True)
        else:
            self.log_msg("Stopped -- no samples recorded.")

    def _record_sample(self, d):
        lap_raw = d.get("lap_number")
        lap     = int(lap_raw) if lap_raw is not None else session.prev_lap

        if session.waiting:
            if lap > session.prev_lap:
                session.waiting   = False
                session.recording = True
                session.start_t   = time.time()
                session.prev_lap  = lap
                telem.reset_lap()
                self.rec_btn.config(text="Stop Recording", bg="#e94560")
                self.log_msg(f"Start line crossed -- recording lap {lap}")
            else:
                session.prev_lap = lap
            return

        if lap > session.prev_lap and len(session.samples) >= 100:
            self.log_msg(f"Lap complete -- {len(session.samples)} samples")
            last_ms = int(d.get("last_lap_ms") or 0)
            self._save_lap(list(session.samples),
                           lap_time_ms=last_ms if last_ms > 0 else None)
            session.samples = []
            session.start_t = time.time()
            telem.reset_lap()
        session.prev_lap = lap

        def _f(k):
            try: return round(float(d.get(k) or 0), 4)
            except: return 0.0
        def _i(k):
            try: return int(d.get(k) or 0)
            except: return 0
        def _b(k):
            try: return bool(d.get(k))
            except: return False

        session.samples.append({
            "t":                round(time.time() - session.start_t, 3),
            "world_x":          _f("world_x"),   "world_y":    _f("world_y"),
            "world_z":          _f("world_z"),   "heading":    _f("heading"),
            "track_position":   _f("track_position"),
            "speed_kmh":        _f("speed_kmh"),
            "vel_x":            _f("vel_x"),     "vel_y":      _f("vel_y"),
            "vel_z":            _f("vel_z"),     "ang_x":      _f("ang_x"),
            "ang_y":            _f("ang_y"),     "ang_z":      _f("ang_z"),
            "throttle":         _f("throttle"),  "brake":      _f("brake"),
            "steering":         _f("steering"),  "clutch":     _f("clutch"),
            "clutch_engaged":   _f("clutch_engaged"),
            "gear":             _i("gear"),      "suggested_gear": _i("suggested_gear"),
            "rpm":              _f("rpm"),       "max_rpm":    _i("max_rpm"),
            "rpm_warning":      _i("rpm_warning"), "rpm_limiter": _i("rpm_limiter"),
            "rpm_after_clutch": _f("rpm_after_clutch"),
            "gear_ratios":      d.get("gear_ratios", []),
            "boost":            _f("boost"),
            "tyre_temp_fl":     _f("tyre_temp_fl"), "tyre_temp_fr": _f("tyre_temp_fr"),
            "tyre_temp_rl":     _f("tyre_temp_rl"), "tyre_temp_rr": _f("tyre_temp_rr"),
            "tyre_slip_fl":     _f("tyre_slip_fl"), "tyre_slip_fr": _f("tyre_slip_fr"),
            "tyre_slip_rl":     _f("tyre_slip_rl"), "tyre_slip_rr": _f("tyre_slip_rr"),
            "susp_fl":          _f("susp_fl"),   "susp_fr":    _f("susp_fr"),
            "susp_rl":          _f("susp_rl"),   "susp_rr":    _f("susp_rr"),
            "ride_height_mm":   _f("ride_height_mm"),
            "fuel_remaining":   _f("fuel_remaining"), "fuel_capacity": _f("fuel_capacity"),
            "is_ev":            _b("is_ev"),
            "oil_temp":         _f("oil_temp"),  "water_temp": _f("water_temp"),
            "oil_pressure":     _f("oil_pressure"),
            "lap_number":       _i("lap_number"),
            "current_position": _i("current_position"),
            "total_positions":  _i("total_positions"),
            "in_pit":           _b("in_pit"),
            "time_of_day":      _i("time_of_day"),
            "est_top_speed":    _i("est_top_speed"),
            "car_id":           _i("car_id"),
            "flags_8e":         _i("flags_8e"),
            "flags_8f":         _i("flags_8f"),
            "flags_93":         _i("flags_93"),
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Save lap
    # ─────────────────────────────────────────────────────────────────────────
    def _save_lap(self, samples, lap_time_ms=None, incomplete=False):
        import math as _math

        has_coords = any(s.get("world_x", 0) != 0 for s in samples)
        if has_coords:
            lap_dist = sum(
                _math.sqrt((samples[i]["world_x"] - samples[i-1]["world_x"]) ** 2 +
                           (samples[i]["world_z"] - samples[i-1]["world_z"]) ** 2)
                for i in range(1, len(samples))
            )
        else:
            lap_dist = sum(s.get("speed_kmh", 0) / 3.6 * SAMPLE_RATE for s in samples)

        ui_track = self.track_var.get().strip()
        ui_car   = self.car_var.get().strip()
        track    = (ui_track or "unknown").lower().replace(" ", "_")
        car      = ui_car or "unknown"

        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = Path(LAPS_FOLDER) / track
        folder.mkdir(parents=True, exist_ok=True)
        ref    = folder / "reference_lap.json"

        new_time    = (lap_time_ms / 1000.0) if (lap_time_ms and lap_time_ms > 0) \
                      else time.time() - session.start_t
        time_source = "GT7" if (lap_time_ms and lap_time_ms > 0) else "elapsed"

        self.log_msg(
            f"Lap time: {ms_to_laptime(int(new_time * 1000))}  "
            f"({time_source}, {len(samples)} samples"
            f"{'  INCOMPLETE' if incomplete else ''})"
        )

        data = {
            "recorded_at":    ts,
            "track":          track,
            "car":            car,
            "lap_time_s":     round(new_time, 3),
            "lap_distance_m": round(lap_dist, 1),
            "total_samples":  len(samples),
            "incomplete":     incomplete,
            "samples":        samples,
        }

        lap_num = int(samples[-1].get("lap_number", 0)) if samples else 0

        def _do_save(path, label):
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            session.laps_saved.append(data)
            self.log_msg(f"Saved: laps/{track}/{label}")
            self._add_lap_to_history(lap_num, new_time, track, car, incomplete)

        if incomplete:
            dlg = tk.Toplevel(self)
            dlg.title("Incomplete Lap")
            dlg.configure(bg="#0a0a12")
            dlg.resizable(False, False)
            tk.Label(dlg,
                     text=f"Incomplete lap ({new_time:.1f}s, {len(samples)} samples) -- save or discard?",
                     fg="#f39c12", bg="#0a0a12", font=("Consolas", 11),
                     pady=12, padx=16).pack()
            bf = tk.Frame(dlg, bg="#0a0a12")
            bf.pack(pady=(0, 12))
            car_safe = car.lower().replace(" ", "_")
            tk.Button(bf, text="Save", bg="#f39c12", fg="#000",
                      font=("Consolas", 10, "bold"), relief="flat", padx=10,
                      command=lambda: [
                          _do_save(folder / f"{car_safe}_{ts}.json",
                                   f"{car_safe}_{ts}.json"),
                          dlg.destroy()
                      ]).pack(side="left", padx=8)
            tk.Button(bf, text="Discard", bg="#333", fg="#aaa",
                      font=("Consolas", 10), relief="flat", padx=10,
                      command=dlg.destroy).pack(side="left", padx=8)
            return

        car_safe     = car.lower().replace(" ", "_")
        lap_filename = f"{car_safe}_{ts}.json"
        lap_path     = folder / lap_filename
        with open(lap_path, "w") as f:
            json.dump(data, f, indent=2)
        session.laps_saved.append(data)
        self.log_msg(f"Saved: laps/{track}/{lap_filename}")
        self._add_lap_to_history(lap_num, new_time, track, car, incomplete=False)

        ref_time = 0.0
        if ref.exists():
            try:
                with open(ref) as f:
                    ref_time = float(json.load(f).get("lap_time_s") or 0)
            except Exception:
                ref_time = 0.0

        if ref_time <= 0 or new_time < ref_time:
            with open(ref, "w") as f:
                json.dump(data, f, indent=2)
            diff_str = (f"  (new best by {ref_time - new_time:.3f}s)"
                        if ref_time > 0 else "  (first lap)")
            self.log_msg(f"Reference updated -> reference_lap.json{diff_str}")

        fuel_used = samples[0].get("fuel_remaining", 0) - samples[-1].get("fuel_remaining", 0)
        if fuel_used > 0:
            self._fuel_per_lap = fuel_used

    # ─────────────────────────────────────────────────────────────────────────
    # Lap history row
    # ─────────────────────────────────────────────────────────────────────────
    def _add_lap_to_history(self, lap_num, lap_time_s, track, car, incomplete=False):
        self._saved_laps += 1
        time_str = ms_to_laptime(int(lap_time_s * 1000))
        if incomplete:
            time_str = f"({time_str})"
        track_d = track[:9]
        car_d   = car[:9]
        display_num = lap_num if lap_num > 0 else self._saved_laps
        row_id = self.hist_tree.insert(
            "", 0, values=(display_num, time_str, track_d, car_d))
        self.hist_tree.tag_configure("newest", foreground="#4fc3f7")
        self.hist_tree.item(row_id, tags=("newest",))
        children = self.hist_tree.get_children()
        if len(children) > 1:
            self.hist_tree.item(children[1], tags=())

    # ─────────────────────────────────────────────────────────────────────────
    # Export session
    # ─────────────────────────────────────────────────────────────────────────
    def _export_session(self):
        if not session.laps_saved:
            self.log_msg("No laps recorded this session.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"gt7_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        if not path:
            return
        with open(path, "w") as f:
            json.dump({
                "exported_at": datetime.now().isoformat(),
                "laps":        session.laps_saved,
            }, f, indent=2)
        self.log_msg(f"Session exported: {Path(path).name}")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
