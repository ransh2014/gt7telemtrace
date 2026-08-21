# gt7telem2.py — GT7 Live Telemetry Viewer (v2)
# New in v2:
#   - Live mini track map canvas (right panel, 240x180)
#   - Tyre hot / cold / fuel-low alert banners (flashing)
#   - Lap history Treeview (scrollable, right panel)

import json
import math
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk

from . import cars as car_db
from . import config as runtime_config
from . import tracks as track_db
from . import udp as telem
from .config import KNOWN_IPS, LAPS_FOLDER
from .config import PS_IP as PS4_IP

# ─────────────────────────────────────────────────────────────────────────────
# Recording sample rate
# ─────────────────────────────────────────────────────────────────────────────
# Options the user can pick for how often samples are recorded during
# Record Lap / Record Race. gt7udp.py doesn't document GT7's actual telemetry
# packet rate anywhere (no constant/comment gives a number), so we don't
# claim one -- 60 Hz is used as a conservative ceiling. Recording is driven
# by its own timer (_record_tick, below) that runs independently of the
# fixed 100ms/10Hz GUI redraw poll (_poll), so it isn't limited to that
# 10Hz UI-only artifact. If the console is actually delivering fresh
# packets slower than the chosen rate, telem.get_snapshot() will just
# return the same latest packet more than once -- we never fabricate or
# interpolate values to hit a higher rate (no upsampling).
RECORD_RATE_OPTIONS = [10, 20, 30, 60]
_RECORD_TICK_MS = 1000 // max(RECORD_RATE_OPTIONS)  # fast enough to thin down to any option above

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
    race_recording = False
    race_samples   = []
    race_start_t   = 0.0
    paused         = False   # True while GT7 reports the game paused -- recording freezes

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
        self._last_autofilled_car_id = None
        self._session_start_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_summary  = []
        self._incident_timeline = []   # flat list of incident dicts across the whole session

        # ── Live delta-vs-reference-lap state ────────────────────────────────
        self._delta_ref_cache  = {"track": None, "samples": None}
        self._delta_prev_lap   = 0
        self._delta_lap_start_t = None

        # ── Recording sample rate ────────────────────────────────────────────
        _saved_rate = int(runtime_config.SAMPLE_RATE or 0)
        if _saved_rate not in RECORD_RATE_OPTIONS:
            _saved_rate = 10
        self._record_rate            = _saved_rate
        self._last_rec_sample_t      = 0.0
        self._last_rec_race_sample_t = 0.0

        self._build()
        telem.register_event("race_start", self._on_race_start)
        telem.register_event("race_end",   self._on_race_end)
        telem.register_event("pause",      self._on_pause)
        telem.register_event("resume",     self._on_resume)
        telem.register_event("debug_state", self._on_debug_state)
        self._start_telem()
        self.after(200, self._poll)
        self.after(_RECORD_TICK_MS, self._record_tick)

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

        tk.Button(hdr, text="Export Session",
                  command=self._export_session,
                  bg="#16213e", fg=FG, relief="flat",
                  font=("Consolas", 10), padx=12, pady=3).pack(side="right", padx=4)
        tk.Button(hdr, text="Session Summary",
                  command=self._show_session_summary,
                  bg="#16213e", fg=FG, relief="flat",
                  font=("Consolas", 10), padx=12, pady=3).pack(side="right", padx=4)
        tk.Button(hdr, text="Incidents",
                  command=self._show_incident_timeline,
                  bg="#16213e", fg=FG, relief="flat",
                  font=("Consolas", 10), padx=12, pady=3).pack(side="right", padx=4)

        self.rec_btn = tk.Button(hdr, text="Record Lap",
                                 command=self._toggle_record,
                                 bg="#16213e", fg=FG, relief="flat",
                                 font=("Consolas", 10, "bold"), padx=12, pady=3)
        self.rec_btn.pack(side="right", padx=6)

        self.race_btn = tk.Button(hdr, text="Record Race",
                                  command=self._toggle_record_race,
                                  bg="#16213e", fg=FG, relief="flat",
                                  font=("Consolas", 10, "bold"), padx=12, pady=3)
        self.race_btn.pack(side="right", padx=6)

        # ── Header row 2: IP / Track / Car ── own row so it can never get
        # squeezed off-window by the button row above growing wider.
        hdr2 = tk.Frame(self, bg="#0f3460", pady=4)
        hdr2.pack(fill="x")

        tk.Label(hdr2, text="PS4 IP", fg=DIM, bg="#0f3460",
                 font=("Consolas", 8)).pack(side="left", padx=(16, 2))
        self.ip_var = tk.StringVar(value=PS4_IP)
        _ip_values = list(KNOWN_IPS)
        if PS4_IP and PS4_IP not in _ip_values:
            _ip_values.insert(0, PS4_IP)
        st_ip = ttk.Style()
        st_ip.configure("Ip.TCombobox", fieldbackground="#16213e",
                        background="#16213e", foreground=HI)
        ip_entry = ttk.Combobox(hdr2, textvariable=self.ip_var, values=_ip_values,
                                font=("Consolas", 9), width=15, style="Ip.TCombobox")
        ip_entry.pack(side="left", padx=(0, 12))
        self.ip_combo_widget = ip_entry
        ip_entry.bind("<Return>",     lambda e: self._on_ip_change())
        ip_entry.bind("<FocusOut>",   lambda e: self._on_ip_change())
        ip_entry.bind("<<ComboboxSelected>>", lambda e: self._on_ip_change())

        tk.Label(hdr2, text="TRACK", fg=DIM, bg="#0f3460",
                 font=("Consolas", 9)).pack(side="left", padx=(8, 2))
        self.track_var = tk.StringVar(value="")
        st_track = ttk.Style()
        st_track.configure("Track.TCombobox", fieldbackground="#16213e",
                           background="#16213e", foreground=FG)
        track_entry = ttk.Combobox(hdr2, textvariable=self.track_var,
                                   values=track_db.all_track_names(),
                                   font=("Consolas", 10), width=20,
                                   style="Track.TCombobox")
        track_entry.pack(side="left", padx=(0, 12))

        tk.Label(hdr2, text="CAR", fg=DIM, bg="#0f3460",
                 font=("Consolas", 9)).pack(side="left", padx=(8, 2))
        self.car_var = tk.StringVar(value="")
        tk.Entry(hdr2, textvariable=self.car_var, bg="#16213e", fg=FG,
                 font=("Consolas", 10), width=18, relief="flat",
                 insertbackground=FG).pack(side="left", padx=(0, 4))

        # ── Advanced / debug (hidden by default) ────────────────────────────
        self.debug_var = tk.BooleanVar(value=bool(runtime_config.DEBUG_LOG))
        tk.Checkbutton(hdr2, text="DEBUG LOG", variable=self.debug_var,
                       command=self._on_debug_toggle, bg="#0f3460", fg=DIM,
                       selectcolor="#16213e", activebackground="#0f3460",
                       font=("Consolas", 8)).pack(side="right", padx=(4, 16))

        # ── Recording sample rate ────────────────────────────────────────────
        self.rate_var = tk.StringVar(value=f"{self._record_rate} Hz")
        st_rate = ttk.Style()
        st_rate.configure("Rate.TCombobox", fieldbackground="#16213e",
                          background="#16213e", foreground=HI)
        rate_combo = ttk.Combobox(hdr2, textvariable=self.rate_var,
                                  values=[f"{r} Hz" for r in RECORD_RATE_OPTIONS],
                                  font=("Consolas", 9), width=6, state="readonly",
                                  style="Rate.TCombobox")
        rate_combo.pack(side="right", padx=(0, 4))
        rate_combo.bind("<<ComboboxSelected>>", lambda e: self._on_rate_change())
        tk.Label(hdr2, text="REC RATE", fg=DIM, bg="#0f3460",
                 font=("Consolas", 8)).pack(side="right", padx=(8, 2))

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
            ("delta_lbl", "DELTA", FG),
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

        # ── Driver aids ───────────────────────────────────────────────────────
        aids_f = tk.LabelFrame(left, text=" DRIVER AIDS ", fg=ACC, bg=BG, font=("Consolas", 9))
        aids_f.pack(fill="x", pady=4, padx=2)
        self._aid_lbls = {}
        for key, label in [
            ("handbrake_active", "HANDBRAKE"), ("asm_active", "ASM"),
            ("tcs_active", "TCS"), ("rev_limit_alert", "REV LIMIT"),
            ("lights_active", "LIGHTS"), ("high_beams", "HIGH BEAM"),
        ]:
            col_f = tk.Frame(aids_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=label, fg=DIM, bg=BG, font=("Consolas", 8)).pack()
            lbl = tk.Label(col_f, text="NO", fg=DIM, bg=BG, font=("Consolas", 11, "bold"))
            lbl.pack()
            self._aid_lbls[key] = lbl

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

        # ── Extended (Packet B/~/C) ───────────────────────────────────────────
        ext_f = tk.LabelFrame(left, text=" EXTENDED (B/~/C) ", fg=ACC, bg=BG, font=("Consolas", 9))
        ext_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("CUR LAP", "extcurlap_lbl"), ("CATEGORY", "extcat_lbl"),
                             ("WHEELBASE", "extwb_lbl"), ("SWAY", "extsway_lbl"),
                             ("HEAVE", "extheave_lbl"), ("SURGE", "extsurge_lbl"),
                             ("ENERGY RECOV", "extenergy_lbl")]:
            col_f = tk.Frame(ext_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=("Consolas", 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=("Consolas", 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        surf_f = tk.LabelFrame(left, text=" SURFACE (per tyre) ", fg=ACC, bg=BG, font=("Consolas", 9))
        surf_f.pack(fill="x", pady=4, padx=2)
        self._surf_lbls = {}
        for label, key in [("FL", "fl"), ("FR", "fr"), ("RL", "rl"), ("RR", "rr")]:
            col_f = tk.Frame(surf_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=8, pady=4)
            tk.Label(col_f, text=label, fg=DIM, bg=BG, font=("Consolas", 9)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=("Consolas", 13, "bold"))
            lbl.pack()
            self._surf_lbls[key] = lbl

        steer_f = tk.LabelFrame(left, text=" WHEEL STEERING ANGLE ", fg=ACC, bg=BG, font=("Consolas", 9))
        steer_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("FRONT L", "extsteerl_lbl"), ("FRONT R", "extsteerr_lbl")]:
            col_f = tk.Frame(steer_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=("Consolas", 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=("Consolas", 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

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
    def _get_reference_samples(self, track_sanitized):
        """Cached read of laps/<track>/reference_lap.json's sample list,
        sorted by track_position for nearest-by-distance lookups. Returns
        None (no crash) if there's no reference lap for this track yet."""
        if self._delta_ref_cache["track"] != track_sanitized:
            self._delta_ref_cache["track"]   = track_sanitized
            self._delta_ref_cache["samples"] = None
            ref_path = Path(LAPS_FOLDER) / track_sanitized / "reference_lap.json"
            try:
                if ref_path.exists():
                    with open(ref_path) as f:
                        ref_data = json.load(f)
                    samples = sorted(
                        ref_data.get("samples", []),
                        key=lambda s: s.get("track_position", 0))
                    if samples:
                        self._delta_ref_cache["samples"] = samples
            except Exception:
                self._delta_ref_cache["samples"] = None
        return self._delta_ref_cache["samples"]

    def _on_ip_change(self):
        new_ip = self.ip_var.get().strip()
        if not new_ip:
            return
        telem.set_ip(new_ip)
        runtime_config.save(PS_IP=new_ip)
        self.log_msg(f"IP changed to {new_ip}  reconnecting...")
        threading.Thread(target=self._connect_thread, daemon=True).start()

    def _on_debug_toggle(self):
        runtime_config.DEBUG_LOG = self.debug_var.get()
        runtime_config.save(DEBUG_LOG=runtime_config.DEBUG_LOG)
        self.log_msg(f"Debug log {'enabled' if runtime_config.DEBUG_LOG else 'disabled'}")

    def _on_rate_change(self):
        try:
            rate = int(self.rate_var.get().split()[0])
        except (ValueError, IndexError):
            rate = 10
        if rate not in RECORD_RATE_OPTIONS:
            rate = 10
        self._record_rate = rate
        runtime_config.SAMPLE_RATE = rate
        runtime_config.save(SAMPLE_RATE=rate)
        self.log_msg(f"Recording sample rate set to {rate} Hz")

    def _remember_good_ip(self, ip):
        """Called only after a connection is actually confirmed -- adds `ip`
        to the remembered-good list and refreshes the combobox's dropdown."""
        known = runtime_config.remember_good_ip(ip)
        if hasattr(self, "ip_combo_widget"):
            self.ip_combo_widget["values"] = known

    # ─────────────────────────────────────────────────────────────────────────
    # Telemetry start
    # ─────────────────────────────────────────────────────────────────────────
    def _start_telem(self):
        threading.Thread(target=self._connect_thread, daemon=True).start()

    def _connect_thread(self):
        telem.set_ip(self.ip_var.get().strip())
        self.log_msg(f"Connecting to {telem._ps4_ip}...")
        result = telem.wait_for_connection(timeout=60)
        if result is not None:
            self.log_msg("Connected! Telemetry live.")
            self.after(0, self._remember_good_ip, telem._ps4_ip)
        else:
            reason = telem.get_last_error() or "unknown error -- check PS4/PS5 IP"
            self.log_msg(f"Connection failed: {reason}")
            diag = telem.get_diagnostics()
            self.log_msg(
                f"  diagnostics: heartbeats_sent={diag['heartbeats_sent']} "
                f"heartbeat_errors={diag['heartbeat_errors']} "
                f"packets_received={diag['packets_received']} "
                f"decrypt_failures={diag['decrypt_failures']} "
                f"parse_failures={diag['parse_failures']}"
            )

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
            # Log once, right when a previously-live connection drops, so a
            # console reboot / cable pull / network switch shows up in the
            # log panel instead of silently sitting at "OFFLINE".
            if getattr(self, "_was_connected", False) and not getattr(self, "_drop_logged", False):
                self._drop_logged = True
                reason = telem.get_last_error() or "no packets received"
                self.log_msg(f"Connection dropped: {reason}")
            self._was_connected = False
            self.after(100, self._poll)
            return
        self._was_connected = True
        self._drop_logged = False

        FG = "#c0c0e0"

        # Speed / Gear
        spd  = float(d.get("speed_kmh") or 0)
        gear = int(d.get("gear") or 0)
        sug  = int(d.get("suggested_gear") or 0)
        if sug == 15:  # GT7 uses 15 (0xF) as a "no suggestion" sentinel, not a real gear
            sug = 0
        rpm  = float(d.get("rpm") or 0)
        mrpm = float(d.get("max_rpm") or 1) or 1
        thr  = float(d.get("throttle") or 0)
        brk  = float(d.get("brake") or 0)
        clt  = float(d.get("clutch") or 0)

        self.speed_lbl.config(text=f"{spd:.0f}")
        self.gear_lbl.config(text=str(gear) if gear > 0 else "N")
        self.sug_gear_lbl.config(text=str(sug) if sug > 0 else "--")
        self.inpit_lbl.config(text="PIT" if d.get("in_pit") else "")

        # Driver aids -- YES in accent color when active, NO dimmed otherwise
        for key, lbl in self._aid_lbls.items():
            active = bool(d.get(key))
            lbl.config(text="YES" if active else "NO",
                       fg="#e94560" if active else "#555566")

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
        _car_id_val = int(d.get("car_id") or 0)
        self.carid_lbl.config(text=str(_car_id_val))
        if _car_id_val > 0 and (
            _car_id_val != self._last_autofilled_car_id
            or not self.car_var.get().strip()
        ):
            _auto_name = car_db.get_car_name(_car_id_val)
            if _auto_name:
                self.car_var.set(_auto_name)
                self._last_autofilled_car_id = _car_id_val
        self.f8e_lbl.config(text=f"0x{int(d.get('flags_8e') or 0):02X}")
        self.f8f_lbl.config(text=f"0x{int(d.get('flags_8f') or 0):02X}")
        self.f93_lbl.config(text=f"0x{int(d.get('flags_93') or 0):02X}")

        # Extended (Packet B/~/C) -- fields are None on plain packet A
        cur_lap_ms = d.get("current_lap_ms")
        self.extcurlap_lbl.config(
            text=ms_to_laptime(cur_lap_ms) if cur_lap_ms is not None else "--")
        self.extcat_lbl.config(text=d.get("car_category") or "--")
        wheel_base = d.get("wheel_base")
        self.extwb_lbl.config(text=f"{wheel_base:.3f}m" if wheel_base else "--")
        sway  = d.get("sway");  self.extsway_lbl.config(text=f"{sway:+.3f}"  if sway  is not None else "--")
        heave = d.get("heave"); self.extheave_lbl.config(text=f"{heave:+.3f}" if heave is not None else "--")
        surge = d.get("surge"); self.extsurge_lbl.config(text=f"{surge:+.3f}" if surge is not None else "--")
        energy_rec = d.get("energy_recovery")
        self.extenergy_lbl.config(text=f"{energy_rec:+.3f}" if energy_rec is not None else "--")

        surface = d.get("surface_type")
        surf_names = {"T": "Tarmac", "C": "Curb", "D": "Dirt", "G": "Grass",
                      "S": "Sand", "s": "Gravel"}
        for i, key in enumerate(["fl", "fr", "rl", "rr"]):
            code = surface[i] if surface and i < len(surface) else None
            self._surf_lbls[key].config(
                text=surf_names.get(code, code or "--"),
                fg="#2ecc71" if code == "T" else ("#f39c12" if code else FG))

        wsa = d.get("wheel_steering_angle")
        if wsa:
            self.extsteerl_lbl.config(text=f"{math.degrees(wsa[0]):+.1f}deg")
            self.extsteerr_lbl.config(text=f"{math.degrees(wsa[1]):+.1f}deg")
        else:
            self.extsteerl_lbl.config(text="--")
            self.extsteerr_lbl.config(text="--")

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

        # ── Live delta vs. reference lap ────────────────────────────────────
        # Uses GT7's own `track_position` field (arc-length distance along
        # the current lap, in metres) rather than approximating it from
        # world-position deltas -- it's the same field already stored per
        # sample in reference_lap.json, so the two are directly comparable.
        if cur_lap != self._delta_prev_lap:
            self._delta_lap_start_t = time.time()
            self._delta_prev_lap    = cur_lap

        cur_track_pos = float(d.get("track_position") or 0)

        cur_lap_ms_live = d.get("current_lap_ms")
        if cur_lap_ms_live is not None and cur_lap_ms_live > 0:
            cur_elapsed = cur_lap_ms_live / 1000.0
        elif self._delta_lap_start_t is not None:
            cur_elapsed = time.time() - self._delta_lap_start_t
        else:
            cur_elapsed = 0.0

        delta_text, delta_color = "--", FG
        track_name = self.track_var.get().strip()
        if track_name and cur_elapsed > 0.5 and cur_track_pos > 0:
            ref_samples = self._get_reference_samples(telem.sanitize(track_name))
            if ref_samples:
                nearest = min(ref_samples,
                              key=lambda s: abs(s.get("track_position", 0) - cur_track_pos))
                ref_t = nearest.get("t")
                if ref_t is not None:
                    delta = cur_elapsed - ref_t
                    delta_text  = f"{delta:+.2f}s"
                    delta_color = "#e74c3c" if delta > 0 else "#2ecc71"
        self.delta_lbl.config(text=delta_text, fg=delta_color)

        self.after(100, self._poll)

    # ─────────────────────────────────────────────────────────────────────────
    # Recording tick -- runs on its own timer, independent of the 10Hz GUI
    # redraw poll above, so the user-selected recording rate (self._record_rate,
    # up to RECORD_RATE_OPTIONS' max) isn't capped by that UI-only cadence.
    # Ticks at _RECORD_TICK_MS and thins samples down to the selected rate by
    # skipping ticks that land inside the same 1/rate window as the last
    # recorded sample -- it never invents/upsamples data between real packets.
    # ─────────────────────────────────────────────────────────────────────────
    def _record_tick(self):
        if not session.paused and telem.is_connected():
            min_dt = 1.0 / self._record_rate
            now = time.time()

            if (session.recording or session.waiting) and \
               (now - self._last_rec_sample_t >= min_dt):
                self._record_sample(telem.get_snapshot())
                self._last_rec_sample_t = now

            if session.race_recording and \
               (now - self._last_rec_race_sample_t >= min_dt):
                self._record_race_sample(telem.get_snapshot())
                self._last_rec_race_sample_t = now

        self.after(_RECORD_TICK_MS, self._record_tick)

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
        self._last_rec_sample_t = 0.0
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

    # ─────────────────────────────────────────────────────────────────────────
    # Record race (continuous, multi-lap, until manually stopped)
    # ─────────────────────────────────────────────────────────────────────────
    def _on_race_start(self, parsed):
        self.after(0, self._handle_race_start)

    def _handle_race_start(self):
        if not session.race_recording:
            self._start_record_race()
            self.log_msg("Auto: race start detected -- recording started")

    def _on_race_end(self, parsed):
        self.after(0, self._handle_race_end)

    def _handle_race_end(self):
        if session.race_recording:
            self._stop_record_race()
            self.log_msg("Auto: race end detected -- recording saved")

    def _on_pause(self, parsed):
        self.after(0, self._handle_pause)

    def _handle_pause(self):
        session.paused = True
        self.log_msg("Auto: game paused -- recording frozen")

    def _on_resume(self, parsed):
        self.after(0, self._handle_resume)

    def _handle_resume(self):
        session.paused = False
        self.log_msg("Auto: game resumed -- recording continues")

    def _on_debug_state(self, snap):
        self.after(0, self._handle_debug_state, snap)

    def _handle_debug_state(self, snap):
        # Raw internal-state dumps are noisy and meaningless to end users --
        # only show them when DEBUG_LOG is explicitly turned on (settings.json
        # or the debug checkbox), defaults to off.
        if not runtime_config.DEBUG_LOG:
            return
        self.log_msg(
            f"[DEBUG] loading={snap['loading']} on_track={snap['car_on_track']} "
            f"paused={snap['paused']} speed={snap['speed_kmh']} "
            f"lap={snap['lap_number']}/{snap['total_laps']} "
            f"race_active={snap['race_active']} grid_armed={snap['grid_armed']}"
        )

    def _toggle_record_race(self):
        if session.race_recording:
            self._stop_record_race()
        else:
            self._start_record_race()

    def _start_record_race(self):
        session.race_recording = True
        session.race_samples   = []
        session.race_start_t   = time.time()
        self._last_rec_race_sample_t = 0.0
        self.race_btn.config(text="Stop Race Recording", bg="#e94560")
        self.log_msg("Race recording started")

    def _stop_record_race(self):
        session.race_recording = False
        self.race_btn.config(text="Record Race", bg="#16213e")
        if session.race_samples:
            self._save_race(list(session.race_samples))
        else:
            self.log_msg("Race recording stopped -- no samples recorded.")

    def _record_race_sample(self, d):
        session.race_samples.append(self._sample_dict(d, session.race_start_t))

    def _save_race(self, samples):
        ui_track = self.track_var.get().strip()
        ui_car   = self.car_var.get().strip()
        track    = telem.sanitize(ui_track)
        car      = ui_car or "unknown"
        car_safe = telem.sanitize(car)

        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = Path(LAPS_FOLDER) / track / "races"
        folder.mkdir(parents=True, exist_ok=True)

        race_duration = time.time() - session.race_start_t
        incidents = telem.get_incidents()
        data = {
            "recorded_at":     ts,
            "track":           track,
            "car":             car,
            "race_duration_s": round(race_duration, 3),
            "total_samples":   len(samples),
            "incidents":       incidents,
            "samples":         samples,
        }

        race_filename = f"race_{car_safe}_{ts}.json"
        race_path     = folder / race_filename
        with open(race_path, "w") as f:
            json.dump(data, f, indent=2)
        self.log_msg(f"Race saved: laps/{track}/races/{race_filename}  "
                     f"({len(samples)} samples, {race_duration:.1f}s, "
                     f"{len(incidents)} incidents)")
        self._incident_timeline.extend(incidents)
        self._append_session_summary(data, incidents)

    def _append_session_summary(self, race_data, incidents):
        samples = race_data["samples"]
        if not samples:
            return
        laps       = sorted(set(int(s.get("lap_number", 0)) for s in samples))
        fuel_start = samples[0].get("fuel_remaining", 0)
        fuel_end   = samples[-1].get("fuel_remaining", 0)
        pos_start  = samples[0].get("current_position", 0)
        pos_end    = samples[-1].get("current_position", 0)

        incident_counts = {}
        for inc in incidents:
            incident_counts[inc["type"]] = incident_counts.get(inc["type"], 0) + 1

        summary = {
            "recorded_at":        race_data["recorded_at"],
            "track":              race_data["track"],
            "car":                race_data["car"],
            "duration_s":         race_data["race_duration_s"],
            "laps":               len(laps),
            "fuel_used_pct":      round(fuel_start - fuel_end, 2),
            "position_start":     pos_start,
            "position_end":       pos_end,
            "incident_count":     len(incidents),
            "incident_breakdown": incident_counts,
        }
        self._session_summary.append(summary)

        session_folder = Path(LAPS_FOLDER) / "_session"
        session_folder.mkdir(parents=True, exist_ok=True)
        session_path = session_folder / f"session_{self._session_start_ts}.json"
        with open(session_path, "w") as f:
            json.dump({
                "session_started": self._session_start_ts,
                "races":           self._session_summary,
            }, f, indent=2)

        self.log_msg(f"Session summary updated ({len(self._session_summary)} race(s) this session)")

    def _show_session_summary(self):
        if not self._session_summary:
            self.log_msg("No races recorded this session yet.")
            return

        dlg = tk.Toplevel(self)
        dlg.title("Session Summary")
        dlg.configure(bg="#0a0a12")
        dlg.geometry("760x320")

        cols = ("race", "track", "car", "laps", "duration", "fuel", "pos", "incidents")
        headers = {"race": "#", "track": "Track", "car": "Car", "laps": "Laps",
                   "duration": "Duration", "fuel": "Fuel %", "pos": "Pos",
                   "incidents": "Incidents"}

        st = ttk.Style()
        st.configure("Summary.Treeview",
                     background="#050510", foreground="#c0c0e0",
                     fieldbackground="#050510", font=("Consolas", 9), rowheight=20)
        st.configure("Summary.Treeview.Heading",
                     background="#0f0f1e", foreground="#e94560",
                     font=("Consolas", 9, "bold"))

        tree = ttk.Treeview(dlg, columns=cols, show="headings", height=10,
                            style="Summary.Treeview")
        for c in cols:
            tree.heading(c, text=headers[c])
            tree.column(c, width=85, anchor="center")
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        for idx, s in enumerate(self._session_summary, 1):
            tree.insert("", "end", values=(
                idx, s["track"], s["car"], s["laps"],
                f"{s['duration_s']:.1f}s", f"{s['fuel_used_pct']:.1f}%",
                f"{s['position_start']}->{s['position_end']}", s["incident_count"],
            ))

    def _show_incident_timeline(self):
        """Simple in-memory incident timeline for the session -- tyre-slip
        spikes, sway/heave/surge body-motion events, suspected fuel-mixture
        changes -- collected as races finish. Replaces dumping raw
        [DEBUG] state lines into the log for every user."""
        timeline = list(self._incident_timeline)
        # also fold in incidents from the race currently in progress (if any)
        if session.race_recording:
            timeline = timeline + telem.get_incidents()

        dlg = tk.Toplevel(self)
        dlg.title("Incident Timeline")
        dlg.configure(bg="#0a0a12")
        dlg.geometry("560x320")

        if not timeline:
            tk.Label(dlg, text="No incidents recorded this session.",
                     fg="#c0c0e0", bg="#0a0a12", font=("Consolas", 10),
                     pady=20).pack()
            return

        cols = ("time", "type", "lap", "speed", "value")
        headers = {"time": "Time", "type": "Type", "lap": "Lap",
                   "speed": "Speed", "value": "Value"}

        st = ttk.Style()
        st.configure("Incidents.Treeview",
                     background="#050510", foreground="#c0c0e0",
                     fieldbackground="#050510", font=("Consolas", 9), rowheight=20)
        st.configure("Incidents.Treeview.Heading",
                     background="#0f0f1e", foreground="#e94560",
                     font=("Consolas", 9, "bold"))

        tree = ttk.Treeview(dlg, columns=cols, show="headings", height=12,
                            style="Incidents.Treeview")
        for c in cols:
            tree.heading(c, text=headers[c])
            tree.column(c, width=100, anchor="center")
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        for inc in sorted(timeline, key=lambda x: x.get("t", 0)):
            t_str = datetime.fromtimestamp(inc.get("t", 0)).strftime("%H:%M:%S")
            value = inc.get("value", inc.get("ratio", ""))
            tree.insert("", "end", values=(
                t_str, inc.get("type", "?"), inc.get("lap_number", "--"),
                f"{inc.get('speed_kmh', '')}", value,
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # Sample building (shared by lap recording and race recording)
    # ─────────────────────────────────────────────────────────────────────────
    def _sample_dict(self, d, start_t):
        def _f(k):
            try: return round(float(d.get(k) or 0), 4)
            except: return 0.0
        def _i(k):
            try: return int(d.get(k) or 0)
            except: return 0
        def _b(k):
            try: return bool(d.get(k))
            except: return False
        def _f_tuple(tup, idx):
            try: return round(float(tup[idx]), 4) if tup else 0.0
            except: return 0.0

        return {
            "t":                round(time.time() - start_t, 3),
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
            # Extended (Packet B/~/C) -- 0/blank when console only sends packet A
            "wheel_rotation":            _f("wheel_rotation"),
            "steering_angular_velocity": _f("steering_angular_velocity"),
            "sway":                      _f("sway"),
            "heave":                     _f("heave"),
            "surge":                     _f("surge"),
            "torque_vectors":            d.get("torque_vectors") or [],
            "energy_recovery":           _f("energy_recovery"),
            "surface_type":              "".join(d.get("surface_type") or []),
            "current_lap_ms":            _i("current_lap_ms"),
            "wheel_steering_angle_l":    _f_tuple(d.get("wheel_steering_angle"), 0),
            "wheel_steering_angle_r":    _f_tuple(d.get("wheel_steering_angle"), 1),
            "wheel_base":                _f("wheel_base"),
            "car_category":              d.get("car_category") or "",
        }

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

        session.samples.append(self._sample_dict(d, session.start_t))

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
            # No GPS coords for this lap (older recording / signal loss) --
            # fall back to integrating speed over each sample's own recorded
            # elapsed time (distance = speed * dt). Using each sample's real
            # `t` (rather than a fixed/assumed interval like the old
            # SAMPLE_RATE-based formula did) keeps this correct in metres no
            # matter what recording rate was in effect for this lap.
            lap_dist = sum(
                samples[i].get("speed_kmh", 0) / 3.6 *
                max(0.0, samples[i].get("t", 0) - samples[i - 1].get("t", 0))
                for i in range(1, len(samples))
            )

        ui_track = self.track_var.get().strip()
        ui_car   = self.car_var.get().strip()
        track    = telem.sanitize(ui_track)
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
            car_safe = telem.sanitize(car)
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

        car_safe     = telem.sanitize(car)
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
            # Invalidate the live-delta reference cache so the new best is
            # picked up on the next poll instead of the stale one.
            self._delta_ref_cache["track"] = None

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
