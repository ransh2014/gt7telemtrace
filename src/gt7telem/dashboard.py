# dashboard.py — TRACE Live Dashboard
#   - Live readouts for everything GT7's telemetry packet carries
#   - Mini track map, tyre/fuel alert banners, lap history, live delta
#   - Record Lap / Record Race (auto-starts on a detected race)
#   - Spoken fuel/tyre alerts, live gap to the leaderboard's top lap, after-lap
#     time-loss summary, and a Preferences row of on/off switches

import bisect
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import (
    __version__,
    coach,
    engineer,
    ghost,
    keepawake,
    lapclean,
    leaderboard,
    metrics_server,
    notify,
    theme,
    trackdetect,
    updates,
    voice,
    webui_server,
)
from . import cars as car_db
from . import config as runtime_config
from . import labels as L
from . import tracks as track_db
from . import udp as telem
from .config import KNOWN_IPS
from .config import PS_IP as PS4_IP
from .core import recorder as rec_core
from .core.live import tyre_alerts
from .core.recorder import (  # noqa: F401  (re-exported: tests and callers use these names from here)
    AUTO_LAP_ARM_HOLD_S,
    AUTO_LAP_LEAVE_HOLD_S,
    AUTO_LAP_MIN_KPH,
    RECORD_QUEUE_MAX,
    RECORD_TAP_SLACK_S,
    Session,
    auto_lap_can_arm,
    auto_lap_in_session,
    lap_clock_running,
    session,
)
from .core.recorder import next_due as _next_due  # noqa: F401  (tests import it from here)
from .core.recorder import thin_accept as _thin_accept

runtime_config.install_mac_buttons()   # macOS ignores Button colours; no-op elsewhere

# ─────────────────────────────────────────────────────────────────────────────
# Recording sample rate
# ─────────────────────────────────────────────────────────────────────────────
# Options the user can pick for how often samples are recorded during
# Record Lap / Record Race. GT7 streams about 60 telemetry packets a second, so
# 60 Hz means "every packet". Samples are chosen per packet on the UDP receive
# thread (see _on_packet_tap and _thin_accept, below), so the rate does not
# depend on the Tk timer or on how busy the UI is. If the console delivers
# packets slower than the chosen rate, we record what arrives -- we never
# fabricate or interpolate values to hit a higher rate (no upsampling).
RECORD_RATE_OPTIONS = [10, 20, 30, 60]

# A race is buffered entirely in memory until it ends, so the sample count is
# the only thing bounding RAM. Measured at ~1.6 KB per sample, which is fine
# for a 20-minute race but not for a long one at a high rate:
#     20 min @ 10Hz =  12k samples ~  18 MB
#      1 h  @ 60Hz  = 216k samples ~ 328 MB   (json.dump then doubles it on save)
#      3 h  @ 60Hz  = 648k samples ~ 984 MB   -- enough to thrash a small laptop
# Warn once on the way up, and hard-stop (saving what we have) before the
# save itself becomes the thing that fails.
RACE_SAMPLE_WARN = 150_000   # ~230 MB
RACE_SAMPLE_MAX  = 400_000   # ~610 MB
GHOST_SETTLE_S = 3.0   # car + track must be unchanged this long before the top-lap lookup is made
_RECORD_TICK_MS = 1000 // max(RECORD_RATE_OPTIONS)  # how often the UI thread drains the recording queue

# Where a recording goes when its normal place under LAPS_FOLDER can't be
# written (disk full, folder not writable, path gone), so a finished race
# isn't lost just because its folder was.
_FALLBACK_SAVE_DIR = Path.home() / "TRACE" / "unsaved"

FROM_MENU = False   # set by the launcher: the window then offers a "Menu" button to go back

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
    if val <= 0:   return theme.DIM
    if val < cold: return theme.TEAL_2
    if val < good: return theme.GREEN
    if val < hot:  return theme.GOLD
    return theme.RED_2

def slip_color(val):
    if val <= 0:   return theme.DIM
    if val < 0.95: return theme.RED_2
    if val < 1.05: return theme.GREEN
    if val < 1.15: return theme.GOLD
    return theme.RED_2

# ─────────────────────────────────────────────────────────────────────────────
# Recording state lives in core/recorder.py (shared with the terminal UI)
# ─────────────────────────────────────────────────────────────────────────────
# Returned by _save_lap/_save_race when a recording was deliberately not saved
# (junk or duplicate), so callers don't mistake it for a failed save.
_SKIPPED = rec_core.SKIPPED


# Low-battery warning: while a lap/race is armed or recording on battery power
BATTERY_WARN_PCT = 20.0
BATTERY_CRIT_PCT = 10.0
BATTERY_ALARM_AFTER_S = 30.0   # unacknowledged this long -> loud repeating alarm
BATTERY_CHECK_S = 2.0


def read_battery():
    """(percent, plugged_in) or None when there is no battery or it can't be read."""
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is not None:
            return float(b.percent), bool(b.power_plugged)
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes

            class _SPS(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                            ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
            s = _SPS()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
                return None
            if s.BatteryFlag == 128 or s.BatteryLifePercent == 255:
                return None
            return float(s.BatteryLifePercent), s.ACLineStatus == 1
        except Exception:
            return None
    return None


def battery_needs_warning(batt, active, warn_pct=BATTERY_WARN_PCT):
    """True when a lap/race is in progress, the laptop is on battery and low."""
    if not active or batt is None:
        return False
    pct, plugged = batt
    return (not plugged) and pct < warn_pct


def battery_alarm_due(warned_since, now, acked):
    """Loud alarm once the warning has gone unacknowledged for a while."""
    return (not acked) and warned_since is not None and now - warned_since >= BATTERY_ALARM_AFTER_S


def unknown_car_label(car_id):
    """CAR box text for a car ID missing from car_ids.csv."""
    return f"Unknown car (ID {car_id})"


def car_box_autofill(car_id, current, last_autofilled_id):
    """What the CAR box should be set to for the car GT7 reports, or None to
    leave it alone. A new car ID always replaces the box -- with its name, or
    "Unknown car (ID N)" when car_ids.csv doesn't know it, never the previous
    car's name. The same ID leaves a non-empty box alone, so a manual edit sticks."""
    if car_id <= 0 or (car_id == last_autofilled_id and current.strip()):
        return None
    return car_db.get_car_name(car_id) or unknown_car_label(car_id)


def _tk_text(s):
    """Tk 8.5 (system Python on older macOS) raises on characters above the
    BMP, e.g. emoji -- drop them there, keep them everywhere else."""
    if tk.TkVersion >= 8.6:
        return s
    return "".join(c for c in s if ord(c) <= 0xFFFF)


def _grab_when_visible(win):
    """grab_set before the window is mapped raises TclError on Linux; a failed
    grab must never break the dialog."""
    try:
        win.wait_visibility()
        win.grab_set()
    except tk.TclError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Main App
# ─────────────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(L.window_title(L.TOOL_LIVE, __version__))
        self.configure(bg=theme.BG)
        theme.fit_window(self, 1280, 920, 1100, 700)
        self._fuel_per_lap      = 0.0
        self._fuel_at_lap_start = 0.0
        self._strategy          = telem.StintTracker()   # live fuel / tyre-stint estimates
        self._strat_car_id      = None
        self._announcer         = voice.AlertAnnouncer()   # spoken fuel / tyre alerts
        self._live_coach        = coach.LiveCoach()        # spoken corner cues, set after each lap vs the reference
        self._radio             = {"text": "", "until": 0.0, "lap": None}   # last coach / pit call, shown on the web UI
        self._ghosts            = {}      # (car, track) -> ghost.Ghost, or None when there is none to show
        self._ghost_fetching    = set()   # (car, track) pairs already asked about this run
        self._ghost_stable      = {"key": None, "since": 0.0}   # when the current pair last changed
        self._track_pts  = deque(maxlen=3000)
        self._flash_tick = 0
        self._flash_on   = False
        self._saved_laps = 0
        self._last_autofilled_car_id = None
        self._submitted_unknown_car_ids = set()
        self._submitted_unknown_tracks  = set()
        self._session_start_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_summary  = []
        self._unsaved_races     = []   # races whose save failed everywhere -- Export Session includes them
        self._connect_gen       = 0    # bumped per connect attempt; a superseded attempt stays quiet
        self._last_poll_error   = None
        self._last_record_error = None
        self._uiq               = queue.SimpleQueue()   # worker/UDP threads -> Tk thread
        self._closing           = False
        self._save_lock         = threading.RLock()
        self._save_threads      = []
        self._unsaved_laps      = []   # full lap data whose save failed everywhere

        # ── Live delta-vs-reference-lap state ────────────────────────────────
        self._delta_ref_cache  = {"key": None, "samples": None, "positions": None, "mtime": None, "checked": 0.0}
        self._delta_prev_lap   = 0
        self._delta_lap_start_t = None

        # ── Recording sample rate ────────────────────────────────────────────
        try:
            _saved_rate = int(runtime_config.SAMPLE_RATE or 0)
        except (TypeError, ValueError):
            _saved_rate = 0
        if _saved_rate not in RECORD_RATE_OPTIONS:
            _saved_rate = 10
        self._record_rate            = _saved_rate
        self._rec_due      = 0.0
        self._rec_due_race = 0.0
        self._rec_q        = deque(maxlen=RECORD_QUEUE_MAX)   # (kind, arrival time, packet) from the UDP thread
        self._lap_rec_since  = 0.0   # packets that arrived before a recording started are ignored
        self._race_rec_since = 0.0
        self._auto_lap_since     = None    # when the "driving in a non-race session" state began
        self._auto_lap_off_since = None    # when the session was left, while an auto lap is armed
        self._auto_lap_active    = False   # the current Record Lap arming was done automatically
        self._auto_lap_block     = False   # user stopped an auto lap: stay off until the session is left
        self._batt_next      = 0.0     # next battery check (monotonic)
        self._batt_warned_at = None    # when the low-battery warning first showed
        self._batt_ack       = False   # user pressed OK on the warning bar
        self._batt_crit      = False   # re-alarmed once at the critical level
        self._batt_bar       = None    # the red warning bar (built on first use)
        self._batt_lbl       = None
        self._beeping        = False
        self._last_lap_sig   = None    # identity of the last saved lap, to refuse a double save
        self._save_unknown_ok = False  # user chose "save as unknown": don't ask again this run of laps
        self._td_store       = trackdetect.FingerprintStore()   # learned + shared start-line fingerprints
        self._td_lap         = -1      # lap number we already tried to detect a track for
        self._td_bar         = None    # the "Looks like X" offer bar (built on first use)
        self._td_lbl         = None
        self._td_guess       = ""
        self._td_rejected    = set()   # layouts the user said "Not this" to
        self._td_race_reset()
        threading.Thread(target=self._td_fetch_shared, daemon=True).start()

        self._build()
        telem.set_packet_tap(self._on_packet_tap)

        if runtime_config.METRICS_ENABLED:
            addr = "0.0.0.0" if runtime_config.METRICS_BIND_ALL else "127.0.0.1"
            if not self._safe_metrics_start(addr):
                self.log_msg(f"Metrics export: failed to bind port {runtime_config.METRICS_PORT} (already in use?)")

        if runtime_config.WEBUI_ENABLED and not self._webui_start():
            runtime_config.WEBUI_ENABLED = False
            self.webui_var.set(False)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        telem.register_event("race_start", self._on_race_start)
        telem.register_event("race_end",   self._on_race_end)
        telem.register_event("pause",      self._on_pause)
        telem.register_event("resume",     self._on_resume)
        telem.register_event("debug_state", self._on_debug_state)
        keepawake.set_awake(bool(runtime_config.KEEP_AWAKE_ENABLED))   # awake from launch until the window closes
        self._start_telem()
        self.after(200, self._poll)
        self.after(_RECORD_TICK_MS, self._record_tick)
        self.after(50, self._drain_uiq)
        self.after(500, self._webui_auth_refresh)

    # ─────────────────────────────────────────────────────────────────────────
    # Build UI
    # ─────────────────────────────────────────────────────────────────────────
    def _build(self):
        theme.style_ttk(self)
        BG  = theme.BG
        PNL = theme.BG1
        ACC = theme.RED_2
        HI  = theme.TEAL_2
        FG  = theme.TXT
        DIM = theme.DIM

        # ── Header ───────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=theme.BG2, pady=6)
        hdr.pack(fill="x")

        if FROM_MENU:
            tk.Button(hdr, text=L.MENU_BUTTON, command=self._go_menu, bg=theme.BG3, fg=FG, relief="flat", activebackground=theme.LINE2, activeforeground=FG, highlightthickness=1, highlightbackground=theme.LINE2, bd=0,
                      font=(theme.MONO, 10), padx=10, pady=3, cursor="hand2").pack(side="right", padx=(4, 12))
        brand = tk.Frame(hdr, bg=theme.BG2)   # brand + tool name, stacked in the top-left corner
        brand.pack(side="left", padx=(10, 12))
        tk.Label(brand, text=L.HEADER_BRAND, fg=ACC, bg=theme.BG2, font=(theme.MONO, 8, "bold")).pack(anchor="w")
        tk.Label(brand, text=L.TITLE_LIVE, fg=theme.TEAL_2, bg=theme.BG2,
                 font=(theme.DISPLAY, 14, "bold")).pack(anchor="w")
        self.conn_dot = tk.Label(hdr, text=L.STATUS_OFFLINE, fg=DIM, bg=theme.BG2,
                                 font=(theme.MONO, 10))
        self.conn_dot.pack(side="left", padx=8)

        tk.Button(hdr, text=L.EXPORT_SESSION,
                  command=self._export_session,
                  bg=theme.BG3, fg=FG, relief="flat", activebackground=theme.LINE2, activeforeground=FG, highlightthickness=1, highlightbackground=theme.LINE2, bd=0,
                  font=(theme.MONO, 10), padx=12, pady=3).pack(side="right", padx=4)
        tk.Button(hdr, text=L.SESSION_SUMMARY,
                  command=self._show_session_summary,
                  bg=theme.BG3, fg=FG, relief="flat", activebackground=theme.LINE2, activeforeground=FG, highlightthickness=1, highlightbackground=theme.LINE2, bd=0,
                  font=(theme.MONO, 10), padx=12, pady=3).pack(side="right", padx=4)

        self.rec_btn = tk.Button(hdr, text=L.RECORD_LAP,
                                 command=self._toggle_record,
                                 bg=theme.BG3, fg=FG, relief="flat", activebackground=theme.LINE2, activeforeground=FG, highlightthickness=1, highlightbackground=theme.LINE2, bd=0,
                                 font=(theme.MONO, 10, "bold"), padx=12, pady=3)
        self.rec_btn.pack(side="right", padx=6)

        self.race_btn = tk.Button(hdr, text=L.RECORD_RACE,
                                  command=self._toggle_record_race,
                                  bg=theme.BG3, fg=FG, relief="flat", activebackground=theme.LINE2, activeforeground=FG, highlightthickness=1, highlightbackground=theme.LINE2, bd=0,
                                  font=(theme.MONO, 10, "bold"), padx=12, pady=3)
        self.race_btn.pack(side="right", padx=6)

        # ── Header row 2: IP / Track / Car ── own row so it can never get
        # squeezed off-window by the button row above growing wider.
        hdr2 = tk.Frame(self, bg=theme.BG2, pady=4)
        hdr2.pack(fill="x")

        tk.Label(hdr2, text=L.CONSOLE_IP, fg=DIM, bg=theme.BG2,
                 font=(theme.MONO, 8)).pack(side="left", padx=(16, 2))
        self.ip_var = tk.StringVar(value=PS4_IP)
        _ip_values = list(KNOWN_IPS)
        if PS4_IP and PS4_IP not in _ip_values:
            _ip_values.insert(0, PS4_IP)
        st_ip = ttk.Style()
        st_ip.configure("Ip.TCombobox", fieldbackground=theme.BG3,
                        background=theme.BG3, foreground=HI)
        ip_entry = ttk.Combobox(hdr2, textvariable=self.ip_var, values=_ip_values,
                                font=(theme.MONO, 9), width=15, style="Ip.TCombobox")
        ip_entry.pack(side="left", padx=(0, 12))
        self.ip_combo_widget = ip_entry
        ip_entry.bind("<Return>",     lambda e: self._on_ip_change(force=True))
        ip_entry.bind("<FocusOut>",   lambda e: self._on_ip_change())
        ip_entry.bind("<<ComboboxSelected>>", lambda e: self._on_ip_change())

        self.discover_btn = tk.Button(hdr2, text=L.AUTO_DETECT,
                                       command=self._on_discover_ip,
                                       bg=theme.BG3, fg=HI, relief="flat",
                                       font=(theme.MONO, 8), padx=6, pady=1)
        self.discover_btn.pack(side="left", padx=(0, 12))

        tk.Label(hdr2, text=L.TRACK, fg=DIM, bg=theme.BG2,
                 font=(theme.MONO, 9)).pack(side="left", padx=(8, 2))
        self.track_var = tk.StringVar(value="")
        st_track = ttk.Style()
        st_track.configure("Track.TCombobox", fieldbackground=theme.BG3,
                           background=theme.BG3, foreground=FG)
        track_entry = ttk.Combobox(hdr2, textvariable=self.track_var,
                                   values=track_db.all_track_names(),
                                   font=(theme.MONO, 10), width=20,
                                   style="Track.TCombobox")
        track_entry.pack(side="left", padx=(0, 12))

        tk.Label(hdr2, text=L.CAR, fg=DIM, bg=theme.BG2,
                 font=(theme.MONO, 9)).pack(side="left", padx=(8, 2))
        self.car_var = tk.StringVar(value="")
        tk.Entry(hdr2, textvariable=self.car_var, bg=theme.BG3, fg=FG,
                 font=(theme.MONO, 10), width=18, relief="flat",
                 insertbackground=FG).pack(side="left", padx=(0, 4))

        # ── Header row 3: preferences. Their own row, so the console / track / car
        # row above never loses its controls to a window that is too narrow.
        hdr3 = tk.Frame(self, bg=theme.BG2, pady=2)
        hdr3.pack(fill="x")
        tk.Label(hdr3, text=L.PREFERENCES, fg=DIM, bg=theme.BG2,
                 font=(theme.MONO, 8)).pack(side="left", padx=(16, 2))

        self.debug_var = tk.BooleanVar(value=bool(runtime_config.DEBUG_LOG))
        tk.Checkbutton(hdr3, text=L.PREF_DEBUG_LOG, variable=self.debug_var,
                       command=self._on_debug_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 16))

        self.analytics_var = tk.BooleanVar(value=bool(runtime_config.ANALYTICS_ENABLED))
        tk.Checkbutton(hdr3, text=L.PREF_SHARE_USAGE_DATA, variable=self.analytics_var,
                       command=self._on_analytics_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        self.update_var = tk.BooleanVar(value=bool(runtime_config.UPDATE_CHECK_ENABLED))
        if not updates.is_store_install():   # the Microsoft Store handles updates itself
            tk.Checkbutton(hdr3, text=L.PREF_CHECK_FOR_UPDATES, variable=self.update_var,
                           command=self._on_update_check_toggle, bg=theme.BG2, fg=DIM,
                           selectcolor=theme.BG3, activebackground=theme.BG2,
                           font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        self.ghost_var = tk.BooleanVar(value=bool(runtime_config.GHOST_ENABLED))
        tk.Checkbutton(hdr3, text=L.PREF_TOP_LAP_GHOST, variable=self.ghost_var,
                       command=self._on_ghost_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        self.notify_var = tk.BooleanVar(value=bool(runtime_config.NOTIFY_ENABLED))
        tk.Checkbutton(hdr3, text=L.PREF_NOTIFICATIONS, variable=self.notify_var,
                       command=self._on_notify_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        self.voice_var = tk.BooleanVar(value=bool(runtime_config.VOICE_ENABLED))
        tk.Checkbutton(hdr3, text=L.PREF_VOICE_ALERTS, variable=self.voice_var,
                       command=self._on_voice_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        self.auto_lap_var = tk.BooleanVar(value=bool(runtime_config.AUTO_LAP_ENABLED))
        tk.Checkbutton(hdr3, text=L.PREF_AUTO_LAP, variable=self.auto_lap_var,
                       command=self._on_auto_lap_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        self.keep_awake_var = tk.BooleanVar(value=bool(runtime_config.KEEP_AWAKE_ENABLED))
        tk.Checkbutton(hdr3, text=L.PREF_KEEP_AWAKE, variable=self.keep_awake_var,
                       command=self._on_keep_awake_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        self.metrics_var = tk.BooleanVar(value=bool(runtime_config.METRICS_ENABLED))
        tk.Checkbutton(hdr2, text=L.METRICS_FMT.format(port=runtime_config.METRICS_PORT), variable=self.metrics_var,
                       command=self._on_metrics_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        self.metrics_bind_var = tk.BooleanVar(value=bool(runtime_config.METRICS_BIND_ALL))
        tk.Checkbutton(hdr2, text=L.ALLOW_REMOTE_METRICS, variable=self.metrics_bind_var,
                       command=self._on_metrics_bind_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        self.webui_url_lbl = tk.Label(hdr2, text="", fg=ACC, bg=theme.BG2, cursor="hand2",
                                      font=(theme.MONO, 8, "underline"))
        self.webui_url_lbl.bind("<Button-1>", lambda _e: self._copy_webui_url())
        self.webui_url_lbl.pack(side="right", padx=(4, 4))
        self.webui_bind_var = tk.BooleanVar(value=bool(runtime_config.WEBUI_BIND_ALL))
        tk.Checkbutton(hdr2, text=L.ALLOW_REMOTE_WEBUI, variable=self.webui_bind_var,
                       command=self._on_webui_bind_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))
        self.webui_var = tk.BooleanVar(value=bool(runtime_config.WEBUI_ENABLED))
        tk.Checkbutton(hdr2, text=L.WEBUI_FMT.format(port=runtime_config.WEBUI_PORT), variable=self.webui_var,
                       command=self._on_webui_toggle, bg=theme.BG2, fg=DIM,
                       selectcolor=theme.BG3, activebackground=theme.BG2,
                       font=(theme.MONO, 8)).pack(side="right", padx=(4, 4))

        # ── Recording sample rate ────────────────────────────────────────────
        self.rate_var = tk.StringVar(value=f"{self._record_rate} Hz")
        st_rate = ttk.Style()
        st_rate.configure("Rate.TCombobox", fieldbackground=theme.BG3,
                          background=theme.BG3, foreground=HI)
        rate_combo = ttk.Combobox(hdr2, textvariable=self.rate_var,
                                  values=[f"{r} Hz" for r in RECORD_RATE_OPTIONS],
                                  font=(theme.MONO, 9), width=6, state="readonly",
                                  style="Rate.TCombobox")
        rate_combo.pack(side="right", padx=(0, 4))
        rate_combo.bind("<<ComboboxSelected>>", lambda e: self._on_rate_change())
        tk.Label(hdr2, text=L.REC_RATE, fg=DIM, bg=theme.BG2,
                 font=(theme.MONO, 8)).pack(side="right", padx=(8, 2))

        # ── WEB UI passcode panel: code, QR and LOG OUT ALL DEVICES next to the address. Shown only while
        # WEB UI and ALLOW REMOTE (WEB UI) are both on (_webui_auth_refresh packs / unpacks it under hdr3).
        self._hdr3 = hdr3
        self.webui_auth_row = tk.Frame(self, bg=theme.BG2, pady=6)
        self.webui_qr = tk.Canvas(self.webui_auth_row, width=1, height=1, bg="#ffffff", highlightthickness=0)
        self.webui_qr.pack(side="left", padx=(16, 12))
        _code_col = tk.Frame(self.webui_auth_row, bg=theme.BG2)
        _code_col.pack(side="left", padx=(0, 16))
        tk.Label(_code_col, text=L.WEBUI_CODE, fg=DIM, bg=theme.BG2, font=(theme.MONO, 8)).pack(anchor="w")
        self.webui_code_lbl = tk.Label(_code_col, text="", fg=FG, bg=theme.BG2, font=(theme.MONO, 28, "bold"))
        self.webui_code_lbl.pack(anchor="w")
        tk.Label(_code_col, text=L.WEBUI_SCAN, fg=DIM, bg=theme.BG2, font=(theme.MONO, 8)).pack(anchor="w")
        self.webui_auth_url_lbl = tk.Label(_code_col, text="", fg=ACC, bg=theme.BG2, font=(theme.MONO, 9))
        self.webui_auth_url_lbl.pack(anchor="w")
        tk.Button(self.webui_auth_row, text=L.LOG_OUT_ALL_DEVICES, command=self._on_webui_logout_all,
                  bg=theme.BG3, fg=ACC, relief="flat", activebackground=theme.LINE2, activeforeground=FG,
                  highlightthickness=1, highlightbackground=theme.LINE2, bd=0, font=(theme.MONO, 9, "bold"),
                  padx=12, pady=4, cursor="hand2").pack(side="left", padx=4)
        self._webui_qr_url = ""

        for _row in (hdr, hdr2, hdr3):
            theme.reflow(_row)
        self._add_tooltips(hdr, hdr2, hdr3, self.webui_auth_row)

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
        # Wheel scrolling only while the pointer is over the left panel --
        # bind_all on its own also scrolled it from the log and lap history.
        # (sign of delta only: it is 120/notch on Windows, ~1 on macOS, tiny on touchpads)
        def _wheel_on(_e=None):
            _lcanvas.bind_all("<MouseWheel>",
                              lambda e: _lcanvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
            _lcanvas.bind_all("<Button-4>", lambda e: _lcanvas.yview_scroll(-1, "units"))
            _lcanvas.bind_all("<Button-5>", lambda e: _lcanvas.yview_scroll(1, "units"))
        def _wheel_off(_e=None):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                _lcanvas.unbind_all(seq)
        left_outer.bind("<Enter>", _wheel_on)
        left_outer.bind("<Leave>", _wheel_off)

        right = tk.Frame(body, bg=BG, width=theme.px(300))
        right.pack(side="right", fill="y", padx=(6, 0))
        right.pack_propagate(False)

        # ── Speed + Gear ──────────────────────────────────────────────────────
        sg = tk.Frame(left, bg=PNL, pady=8)
        sg.pack(fill="x", pady=(0, 4))
        self.speed_lbl = tk.Label(sg, text="0", fg=HI, bg=PNL,
                                  font=(theme.IMPACT, 64))
        self.speed_lbl.pack(side="left", padx=20)
        tk.Label(sg, text=L.SPEED_UNIT, fg=DIM, bg=PNL,
                 font=(theme.MONO, 14)).pack(side="left", pady=(24, 0))
        sg_mid = tk.Frame(sg, bg=PNL)
        sg_mid.pack(side="left", expand=True)
        tk.Label(sg_mid, text=L.SUGGEST, fg=DIM, bg=PNL, font=(theme.MONO, 8)).pack()
        self.sug_gear_lbl = tk.Label(sg_mid, text="--", fg=theme.GOLD, bg=PNL,
                                     font=(theme.MONO, 20, "bold"))
        self.sug_gear_lbl.pack()
        self.gear_lbl = tk.Label(sg, text="N", fg=theme.GOLD, bg=PNL,
                                 font=(theme.IMPACT, 64))
        self.gear_lbl.pack(side="right", padx=20)
        tk.Label(sg, text="gear", fg=DIM, bg=PNL,
                 font=(theme.MONO, 14)).pack(side="right", pady=(24, 0))

        # ── Lap info ──────────────────────────────────────────────────────────
        lap_f = tk.Frame(left, bg=PNL, pady=6)
        lap_f.pack(fill="x", pady=(0, 4))
        for attr, label, col in [
            ("lap_lbl", "LAP", FG), ("cur_lbl", "CURRENT", HI),
            ("best_lbl", "BEST", theme.GREEN), ("last_lbl", "LAST", FG),
            ("pos_lbl", "GRID", FG), ("topspd_lbl", "TOP SPD", HI),
            ("delta_lbl", "DELTA", FG), ("ghost_lbl", "VS TOP", FG),
        ]:
            col_f = tk.Frame(lap_f, bg=PNL)
            col_f.pack(side="left", expand=True)
            tk.Label(col_f, text=label, fg=DIM, bg=PNL, font=(theme.MONO, 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=col, bg=PNL, font=(theme.MONO, 13, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Progress bars ─────────────────────────────────────────────────────
        def bar_row(parent, label, av, al, fc):
            f = tk.Frame(parent, bg=BG)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=label, fg=DIM, bg=BG,
                     font=(theme.MONO, 9), width=9).pack(side="left", padx=4)
            var = tk.DoubleVar()
            ttk.Progressbar(f, variable=var, maximum=100, length=theme.px(300), mode="determinate",
                            style=theme.bar_style(self, label.replace(" ", ""), fc)).pack(
                side="left", fill="x", expand=True, padx=4)
            lbl = tk.Label(f, text="0", fg=fc, bg=BG, font=(theme.MONO, 10), width=10)
            lbl.pack(side="left")
            setattr(self, av, var)
            setattr(self, al, lbl)

        bar_row(left, "RPM",      "rpm_var", "rpm_lbl", theme.RED_2)
        bar_row(left, "THROTTLE", "thr_var", "thr_lbl", theme.GREEN)
        bar_row(left, "BRAKE",    "brk_var", "brk_lbl", theme.RED_2)
        bar_row(left, "CLUTCH",   "clt_var", "clt_lbl", theme.GOLD)

        # ── Tyres ─────────────────────────────────────────────────────────────
        tyre_f = tk.LabelFrame(left, text=" TYRES ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        tyre_f.pack(fill="x", pady=4, padx=2)
        for label, ta, sa in [("FL", "tyre_fl", "slip_fl"), ("FR", "tyre_fr", "slip_fr"),
                               ("RL", "tyre_rl", "slip_rl"), ("RR", "tyre_rr", "slip_rr")]:
            col_f = tk.Frame(tyre_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=8, pady=4)
            tk.Label(col_f, text=label, fg=DIM, bg=BG, font=(theme.MONO, 9)).pack()
            t_lbl = tk.Label(col_f, text="--C", fg=HI, bg=BG, font=(theme.MONO, 13, "bold"))
            t_lbl.pack()
            s_lbl = tk.Label(col_f, text="slip --", fg=DIM, bg=BG, font=(theme.MONO, 8))
            s_lbl.pack()
            setattr(self, ta, t_lbl)
            setattr(self, sa, s_lbl)

        # ── Suspension ────────────────────────────────────────────────────────
        susp_f = tk.LabelFrame(left, text=" SUSPENSION (mm) ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        susp_f.pack(fill="x", pady=4, padx=2)
        for label, attr in [("FL", "susp_fl_lbl"), ("FR", "susp_fr_lbl"),
                             ("RL", "susp_rl_lbl"), ("RR", "susp_rr_lbl")]:
            col_f = tk.Frame(susp_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=8, pady=4)
            tk.Label(col_f, text=label, fg=DIM, bg=BG, font=(theme.MONO, 9)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 12, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Driver aids ───────────────────────────────────────────────────────
        aids_f = tk.LabelFrame(left, text=" DRIVER AIDS ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        aids_f.pack(fill="x", pady=4, padx=2)
        self._aid_lbls = {}
        for key, label in [
            ("handbrake_active", "HANDBRAKE"), ("asm_active", "ASM"),
            ("tcs_active", "TCS"), ("rev_limit_alert", "REV LIMIT"),
            ("lights_active", "LIGHTS"), ("high_beams", "HIGH BEAM"),
        ]:
            col_f = tk.Frame(aids_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=label, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack()
            lbl = tk.Label(col_f, text="NO", fg=DIM, bg=BG, font=(theme.MONO, 11, "bold"))
            lbl.pack()
            self._aid_lbls[key] = lbl

        # ── Engine and Fluids ─────────────────────────────────────────────────
        eng_f = tk.LabelFrame(left, text=" ENGINE AND FLUIDS ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        eng_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("FUEL", "fuel_lbl"), ("FUEL LAPS", "fuellaps_lbl"),
                             ("BOOST bar", "boost_lbl"), ("OIL C*", "oil_lbl"),
                             ("WATER C*", "water_lbl"), ("OIL bar", "oilp_lbl"),
                             ("RIDE mm", "ride_lbl")]:
            col_f = tk.Frame(eng_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 12, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)
        tk.Label(eng_f, text="* GT7 sends fixed values for these (85 / 110 C) -- not simulated",
                 fg=DIM, bg=BG, font=(theme.MONO, 7)).pack(side="bottom", anchor="w", padx=6)

        # ── Strategy (live estimates) ─────────────────────────────────────────
        # GT7 doesn't send tyre wear, so the tyre figures are estimates built
        # from lap-time drop-off (see udp.StintTracker); fuel is measured.
        strat_f = tk.LabelFrame(left, text=" STRATEGY (est.) ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        strat_f.pack(fill="x", pady=4, padx=2)
        for row_items in (
            [("FUEL/LAP", "st_fpl_lbl"), ("FUEL LAPS", "st_laps_lbl"),
             ("FINISH", "st_finish_lbl"), ("PIT BY", "st_pit_lbl")],
            [("STINT", "st_stint_lbl"), ("PACE LOSS", "st_loss_lbl"),
             ("TREND", "st_trend_lbl"), ("TYRES", "st_tyre_lbl")],
        ):
            row_f = tk.Frame(strat_f, bg=BG)
            row_f.pack(fill="x")
            for title, attr in row_items:
                col_f = tk.Frame(row_f, bg=BG)
                col_f.pack(side="left", expand=True, padx=6, pady=3)
                tk.Label(col_f, text=title, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack()
                lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 12, "bold"))
                lbl.pack()
                setattr(self, attr, lbl)
        self.st_heat_lbl = tk.Label(strat_f, text="Tyre temps last lap: --", fg=DIM, bg=BG,
                                    font=(theme.MONO, 8), anchor="w")
        self.st_heat_lbl.pack(fill="x", padx=6)
        foot_f = tk.Frame(strat_f, bg=BG)
        foot_f.pack(fill="x", padx=6, pady=(0, 3))
        tk.Label(foot_f, text="Estimates from live data. GT7 sends no tyre wear; fuel burn-off hides some pace loss.",
                 fg=DIM, bg=BG, font=(theme.MONO, 7), anchor="w").pack(side="left")
        new_tyres = tk.Label(foot_f, text=L.NEW_TYRES, fg=ACC, bg=BG, cursor="hand2",
                             font=(theme.MONO, 8, "bold"))
        new_tyres.pack(side="right")
        new_tyres.bind("<Button-1>", lambda _e: self._on_new_tyres())
        # ── Dynamics ─────────────────────────────────────────────────────────
        dyn_f = tk.LabelFrame(left, text=" DYNAMICS ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        dyn_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("VEL X", "velx_lbl"), ("VEL Y", "vely_lbl"),
                             ("VEL Z", "velz_lbl"), ("ANG X", "angx_lbl"),
                             ("ANG Y", "angy_lbl"), ("ANG Z", "angz_lbl"),
                             ("HEADING", "hdg_lbl")]:
            col_f = tk.Frame(dyn_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=4, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Position ──────────────────────────────────────────────────────────
        pos_f = tk.LabelFrame(left, text=" POSITION ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        pos_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("WORLD X", "wx_lbl"), ("WORLD Y", "wy_lbl"),
                             ("WORLD Z", "wz_lbl"), ("TRACK POS", "tpos_lbl")]:
            col_f = tk.Frame(pos_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Drivetrain extras ─────────────────────────────────────────────────
        drv_f = tk.LabelFrame(left, text=" DRIVETRAIN EXTRAS ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        drv_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("STEERING", "steer_lbl"), ("CLUTCH ENG", "clteng_lbl"),
                             ("RPM WARN", "rpmwarn_lbl"), ("RPM LIM", "rpmlim_lbl"),
                             ("RPM@CLUTCH", "rpmclt_lbl")]:
            col_f = tk.Frame(drv_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        gr_f = tk.Frame(left, bg=BG)
        gr_f.pack(fill="x", pady=(0, 4), padx=4)
        tk.Label(gr_f, text=L.GEAR_RATIOS, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack(side="left", padx=4)
        self.gr_lbl = tk.Label(gr_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 8))
        self.gr_lbl.pack(side="left")

        # ── Extended (Packet B/~/C) ───────────────────────────────────────────
        ext_f = tk.LabelFrame(left, text=" EXTENDED (B/~/C) ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        ext_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("CUR LAP", "extcurlap_lbl"), ("CATEGORY", "extcat_lbl"),
                             ("WHEELBASE", "extwb_lbl"), ("SWAY", "extsway_lbl"),
                             ("HEAVE", "extheave_lbl"), ("SURGE", "extsurge_lbl"),
                             ("ENERGY RECOV", "extenergy_lbl")]:
            col_f = tk.Frame(ext_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        surf_f = tk.LabelFrame(left, text=" SURFACE (per tyre) ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        surf_f.pack(fill="x", pady=4, padx=2)
        self._surf_lbls = {}
        for label, key in [("FL", "fl"), ("FR", "fr"), ("RL", "rl"), ("RR", "rr")]:
            col_f = tk.Frame(surf_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=8, pady=4)
            tk.Label(col_f, text=label, fg=DIM, bg=BG, font=(theme.MONO, 9)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 13, "bold"))
            lbl.pack()
            self._surf_lbls[key] = lbl

        steer_f = tk.LabelFrame(left, text=" WHEEL STEERING ANGLE ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        steer_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("FRONT L", "extsteerl_lbl"), ("FRONT R", "extsteerr_lbl")]:
            col_f = tk.Frame(steer_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # ── Session misc ──────────────────────────────────────────────────────
        misc_f = tk.LabelFrame(left, text=" SESSION / MISC ", fg=ACC, bg=BG, font=(theme.MONO, 9),
                                   bd=0, relief="flat", highlightthickness=1, highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
        misc_f.pack(fill="x", pady=4, padx=2)
        for title, attr in [("TIME OF DAY", "tod_lbl"), ("IS EV", "isev_lbl"),
                             ("CAR ID", "carid_lbl"), ("FLAG 8E", "f8e_lbl"),
                             ("FLAG 8F", "f8f_lbl"), ("FLAG 93", "f93_lbl")]:
            col_f = tk.Frame(misc_f, bg=BG)
            col_f.pack(side="left", expand=True, padx=6, pady=4)
            tk.Label(col_f, text=title, fg=DIM, bg=BG, font=(theme.MONO, 8)).pack()
            lbl = tk.Label(col_f, text="--", fg=FG, bg=BG, font=(theme.MONO, 11, "bold"))
            lbl.pack()
            setattr(self, attr, lbl)

        # =====================================================================
        # RIGHT PANEL  -  alerts / track map / lap history / log
        # =====================================================================

        # ── Alerts row ───────────────────────────────────────────────────────
        af = tk.Frame(right, bg=BG)
        af.pack(fill="x", pady=(0, 4))

        self.alert_hot  = tk.Label(af, text=L.ALERT_TYRE_HOT,  bg=theme.RED_2, fg="#fff",
                                   font=(theme.MONO, 8, "bold"), padx=5, pady=2)
        self.alert_cold = tk.Label(af, text=L.ALERT_TYRE_COLD, bg=theme.TEAL, fg=theme.TEAL_2,
                                   font=(theme.MONO, 8, "bold"), padx=5, pady=2)
        self.alert_fuel = tk.Label(af, text=L.ALERT_FUEL_LOW,  bg=theme.GOLD, fg="#000",
                                   font=(theme.MONO, 8, "bold"), padx=5, pady=2)
        # hidden until triggered — _poll calls grid / grid_remove
        self.alert_hot.grid( in_=af, row=0, column=0, padx=2, pady=1)
        self.alert_cold.grid(in_=af, row=0, column=1, padx=2, pady=1)
        self.alert_fuel.grid(in_=af, row=0, column=2, padx=2, pady=1)
        self.alert_hot.grid_remove()
        self.alert_cold.grid_remove()
        self.alert_fuel.grid_remove()

        # ── Track map ────────────────────────────────────────────────────────
        tk.Label(right, text=L.TRACK_MAP, fg=ACC, bg=BG,
                 font=(theme.MONO, 9, "bold")).pack(anchor="w", padx=2)
        self.map_canvas = tk.Canvas(right, bg=theme.CBG, width=theme.px(240), height=theme.px(180),
                                    highlightthickness=1, highlightbackground=DIM)
        self.map_canvas.pack(pady=(0, 4))
        self.map_canvas.create_text(theme.px(120), theme.px(90), text=L.WAITING_FOR_DATA,
                                    fill=theme.BG3, font=(theme.MONO, 9))

        # ── Lap history ──────────────────────────────────────────────────────
        tk.Label(right, text=L.LAP_HISTORY, fg=ACC, bg=BG,
                 font=(theme.MONO, 9, "bold")).pack(anchor="w", padx=2)

        hist_outer = tk.Frame(right, bg=BG)
        hist_outer.pack(fill="x", pady=(0, 4))

        st = ttk.Style()
        st.configure("Hist.Treeview",
                     background=theme.CBG, foreground=theme.TXT,
                     fieldbackground=theme.CBG,
                     font=(theme.MONO, 8), rowheight=theme.px(18))
        st.configure("Hist.Treeview.Heading",
                     background=theme.BG1, foreground=ACC,
                     font=(theme.MONO, 8, "bold"))
        st.map("Hist.Treeview",
               background=[("selected", theme.RED_DIM)],
               foreground=[("selected", theme.TEAL_2)])

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
        self.hist_tree.column("lap",   width=theme.px(26), minwidth=theme.px(26), stretch=False)
        self.hist_tree.column("time",  width=theme.px(94), minwidth=theme.px(94), stretch=False)
        self.hist_tree.column("track", width=theme.px(70), minwidth=theme.px(50), stretch=True)
        self.hist_tree.column("car",   width=theme.px(66), minwidth=theme.px(50), stretch=True)

        hist_sb = ttk.Scrollbar(hist_outer, orient="vertical",
                                 command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=hist_sb.set)
        hist_sb.pack(side="right", fill="y")
        self.hist_tree.pack(side="left", fill="both", expand=True)

        # ── Log ──────────────────────────────────────────────────────────────
        tk.Label(right, text=L.LOG, fg=ACC, bg=BG,
                 font=(theme.MONO, 9, "bold")).pack(anchor="w", pady=(2, 2), padx=2)
        log_box = tk.Frame(right, bg=BG)
        log_box.pack(fill="both", expand=True)
        log_sb = ttk.Scrollbar(log_box, orient="vertical")
        log_sb.pack(side="right", fill="y")
        self.log = tk.Text(log_box, bg=theme.CBG, fg=theme.TEAL_2, font=(theme.MONO, 8), relief="flat",
                           wrap="word", yscrollcommand=log_sb.set, highlightthickness=0)
        log_sb.configure(command=self.log.yview)
        self.log.pack(side="left", fill="both", expand=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Track map draw
    # ─────────────────────────────────────────────────────────────────────────
    def _draw_track_map(self):
        c = self.map_canvas
        c.delete("all")
        pts = list(self._track_pts)

        if len(pts) < 2:
            c.create_text(theme.px(120), theme.px(90), text=L.WAITING_FOR_DATA,
                          fill=theme.BG3, font=(theme.MONO, 9))
            return

        W, H, PAD = theme.px(240), theme.px(180), theme.px(14)
        xs = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        min_x = min(xs); max_x = max(xs)
        min_z = min(zs); max_z = max(zs)
        rng_x = max_x - min_x or 1
        rng_z = max_z - min_z or 1
        # One scale for both axes (and centred), so the track keeps its real
        # shape -- scaling x and z separately stretched it to fill the box.
        scale = min((W - 2 * PAD) / rng_x, (H - 2 * PAD) / rng_z)
        off_x = (W - rng_x * scale) / 2
        off_y = (H - rng_z * scale) / 2

        def to_cv(x, z):
            return off_x + (x - min_x) * scale, off_y + (z - min_z) * scale

        # Full trace downsampled to max 400 pts
        step = max(1, len(pts) // 400)
        sparse = pts[::step]
        if len(sparse) >= 2:
            coords = []
            for x, z in sparse:
                coords.extend(to_cv(x, z))
            c.create_line(*coords, fill=theme.RED_DIM, width=2, smooth=True)

        # Recent 80 pts in bright cyan
        recent = pts[-80:]
        if len(recent) >= 2:
            rc = []
            for x, z in recent:
                rc.extend(to_cv(x, z))
            c.create_line(*rc, fill=theme.TEAL_2, width=2, smooth=True)

        # Current position dot
        cx, cy = to_cv(*pts[-1])
        c.create_oval(cx - theme.px(5), cy - theme.px(5), cx + theme.px(5), cy + theme.px(5),
                      fill=theme.RED_2, outline="#fff", width=1)

    # ─────────────────────────────────────────────────────────────────────────
    # IP change
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _reference_path(track_sanitized, car_safe):
        """Reference lap for the live DELTA: one per track *and car* (shared rule: core/recorder.py)."""
        return rec_core.reference_path(runtime_config.LAPS_FOLDER, track_sanitized, car_safe)

    def _get_reference_samples(self, track_sanitized, car_safe):
        """Cached read of this track+car's reference lap samples, sorted by
        track_position. Returns None if there is no (usable) reference yet.
        The file is re-checked every couple of seconds by mtime, so a
        reference written later (this app or the analyst) is picked up, and
        an unreadable one isn't re-parsed on every poll."""
        key = (track_sanitized, car_safe)
        c = self._delta_ref_cache
        now = time.monotonic()
        if c["key"] == key and now - c.get("checked", 0.0) < 2.0:
            return c["samples"]
        ref_path = self._reference_path(track_sanitized, car_safe)
        try:
            mtime = ref_path.stat().st_mtime
        except OSError:
            mtime = None
        if c["key"] == key and c.get("mtime") == mtime:
            c["checked"] = now
            return c["samples"]
        samples = positions = None
        if mtime is not None:
            try:
                with open(ref_path, encoding="utf-8-sig") as f:
                    ref_data = json.load(f)
                ss = sorted(
                    (s for s in ref_data.get("samples", []) if isinstance(s, dict)),
                    key=lambda s: float(s.get("track_position") or 0))
                # A reference lap from an older GT7 physics era would
                # show a meaningless delta -- treat it as absent.
                if ss and leaderboard.is_current_era(ref_data):
                    samples = ss
                    positions = [float(s.get("track_position") or 0) for s in ss]
            except Exception:
                samples = positions = None
        c.update(key=key, samples=samples, positions=positions, mtime=mtime, checked=now)
        return samples

    def _post(self, fn, *args):
        """Queue fn(*args) to run on the Tk thread. Safe from any thread."""
        self._uiq.put((fn, args))

    def _drain_uiq(self):
        try:
            while True:
                try:
                    fn, args = self._uiq.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn(*args)
                except Exception as e:
                    try:
                        self._log_append(f"UI error: {type(e).__name__}: {e}")
                    except Exception:
                        pass
        finally:
            if not self._closing:
                try:
                    self.after(50, self._drain_uiq)
                except tk.TclError:
                    pass

    def _safe_metrics_start(self, addr):
        try:
            return bool(metrics_server.start(runtime_config.METRICS_PORT, addr=addr))
        except Exception as e:
            self.log_msg(f"Metrics export error: {type(e).__name__}: {e}")
            return False

    # ── LAN web dashboard (see webui_server.py) ──────────────────────────────
    def _webui_start(self):
        """Start the web UI; update the URL label and log. True if running."""
        addr = "0.0.0.0" if runtime_config.WEBUI_BIND_ALL else "127.0.0.1"
        port = runtime_config.WEBUI_PORT
        try:
            ok = bool(webui_server.start(port, addr=addr, hz=runtime_config.WEBUI_HZ))
        except Exception as e:
            self.log_msg(f"Web UI error: {type(e).__name__}: {e}")
            ok = False
        if not ok:
            why = webui_server.last_error[0]
            try:
                import websockets  # noqa: F401
                reason = f"port {port} unavailable ({why})" if why else f"could not start on port {port}"
            except Exception:
                reason = "the 'websockets' package is not installed (pip install websockets)"
            self.log_msg(f"Web UI: failed to start -- {reason}")
            self.webui_url_lbl.config(text="")
            return False
        host = (webui_server.lan_ip() or "this-pc-ip") if runtime_config.WEBUI_BIND_ALL else "127.0.0.1"
        self._webui_url = f"http://{host}:{port}"
        self.webui_url_lbl.config(text=self._webui_url + "  (click to copy)")
        self.log_msg(f"Web UI on {self._webui_url}" + (
            " -- other devices need the code shown under the header, or scan its QR" if runtime_config.WEBUI_BIND_ALL
            else " -- this PC only (tick ALLOW REMOTE to open it from a phone)"))
        self._webui_auth_refresh(reschedule=False)
        return True

    def _webui_auth_refresh(self, reschedule=True):
        """Show / hide the passcode panel and redraw the QR when its one-time token changes (used or 5 min old)."""
        try:
            if webui_server.auth_required():
                host = webui_server.lan_ip() or "this-pc-ip"
                port = runtime_config.WEBUI_PORT
                code = webui_server.code()
                self.webui_code_lbl.config(text=f"{code[:3]} {code[3:]}")
                self.webui_auth_url_lbl.config(text=f"http://{host}:{port}")
                url = webui_server.qr_url(host, port) if webui_server.lan_ip() else ""
                if url != self._webui_qr_url:
                    self._webui_qr_url = url
                    self._draw_qr(url)
                if not self.webui_auth_row.winfo_ismapped():
                    self.webui_auth_row.pack(fill="x", after=self._hdr3)
            elif self.webui_auth_row.winfo_ismapped() or self._webui_qr_url:
                self.webui_auth_row.pack_forget()
                self._webui_qr_url = ""
                self.webui_code_lbl.config(text="")
        except tk.TclError:
            return
        if reschedule:
            self.after(2000, self._webui_auth_refresh)

    def _draw_qr(self, url):
        c = self.webui_qr
        c.delete("all")
        if not url:
            c.config(width=1, height=1)
            return
        from . import qr
        m = qr.encode(url)
        cell, quiet = 3, 4
        side = (len(m) + 2 * quiet) * cell
        c.config(width=side, height=side)
        for y, row in enumerate(m):
            for x, dark in enumerate(row):
                if dark:
                    x0, y0 = (x + quiet) * cell, (y + quiet) * cell
                    c.create_rectangle(x0, y0, x0 + cell, y0 + cell, fill="#000000", width=0)

    def _on_webui_logout_all(self):
        webui_server.logout_all()
        self.log_msg("Web UI: logged out all devices -- each one needs the code again")

    def _copy_webui_url(self):
        url = getattr(self, "_webui_url", "")
        if url:
            try:
                self.clipboard_clear()
                self.clipboard_append(url)
                self.log_msg(f"Copied {url}")
            except tk.TclError:
                pass

    def _on_webui_toggle(self):
        runtime_config.WEBUI_ENABLED = self.webui_var.get()
        runtime_config.save(WEBUI_ENABLED=runtime_config.WEBUI_ENABLED)
        if runtime_config.WEBUI_ENABLED:
            if not self._webui_start():
                self.webui_var.set(False)
                runtime_config.WEBUI_ENABLED = False
                runtime_config.save(WEBUI_ENABLED=False)
        else:
            webui_server.stop()
            self.webui_url_lbl.config(text="")
            self._webui_auth_refresh(reschedule=False)
            self.log_msg("Web UI disabled")

    def _on_webui_bind_toggle(self):
        runtime_config.WEBUI_BIND_ALL = self.webui_bind_var.get()
        runtime_config.save(WEBUI_BIND_ALL=runtime_config.WEBUI_BIND_ALL)
        if runtime_config.WEBUI_ENABLED:
            webui_server.stop()
            if not self._webui_start():
                self.webui_var.set(False)
                runtime_config.WEBUI_ENABLED = False
                runtime_config.save(WEBUI_ENABLED=False)
            self._webui_auth_refresh(reschedule=False)
        else:
            self.log_msg("Web UI will bind to your LAN next time it's enabled" if runtime_config.WEBUI_BIND_ALL
                         else "Web UI will bind to localhost only next time it's enabled")

    def _on_new_tyres(self):
        """[ NEW TYRES ] -- start a fresh tyre stint (a refuel already does this)."""
        self._strategy.reset_stint()
        self.log_msg("Strategy: new tyre stint started")

    def _update_strategy_labels(self, st, cur_lap, unit):
        """Paint the STRATEGY panel from a StintTracker snapshot. A leading
        '~' marks a figure based on fewer than 3 laps of data."""
        GRN, AMB, RED = theme.GREEN, theme.GOLD, theme.RED_2
        FGC, DIMC = theme.TXT, theme.DIM
        rough = "~" if st["fuel_basis_laps"] < 3 else ""

        fpl = st["fuel_per_lap"]
        self.st_fpl_lbl.config(text=f"{rough}{fpl:.2f}{unit}" if fpl else "--",
                               fg=FGC if fpl else DIMC)
        lof = st["laps_of_fuel"]
        self.st_laps_lbl.config(text=f"{rough}{lof:.1f}" if lof is not None else "--",
                                fg=(RED if lof < 2 else AMB if lof < 4 else FGC) if lof is not None else DIMC)

        spare = st["spare_laps"]
        if spare is None:
            self.st_finish_lbl.config(text="--", fg=DIMC)
        elif spare < 0:
            self.st_finish_lbl.config(text=f"-{abs(spare):.1f} laps", fg=RED)
        elif st["stop_needed"]:
            self.st_finish_lbl.config(text="TIGHT", fg=AMB)
        else:
            self.st_finish_lbl.config(text=f"+{spare:.1f} laps", fg=GRN)

        pit = st["pit_by_lap"]
        if pit is not None:
            self.st_pit_lbl.config(text=f"LAP {pit}", fg=RED if pit <= cur_lap else AMB)
        elif st["stop_needed"] is False:
            self.st_pit_lbl.config(text="NO STOP", fg=GRN)
        else:
            self.st_pit_lbl.config(text="--", fg=DIMC)

        self.st_stint_lbl.config(text=f"{st['stint_age']} laps", fg=FGC)
        loss, trend, state = st["pace_loss_s"], st["trend_s_per_lap"], st["tyre_state"]
        self.st_loss_lbl.config(text=f"+{loss:.2f}s" if loss is not None else "--",
                                fg=FGC if loss is not None else DIMC)
        self.st_trend_lbl.config(text=f"{trend:+.2f}s/lap" if trend is not None else "--",
                                 fg=(AMB if trend > 0.15 else FGC) if trend is not None else DIMC)
        state_col = {"Fresh": GRN, "Good": GRN, "Fading": AMB, "Worn": RED}
        self.st_tyre_lbl.config(text=state if state else "--", fg=state_col.get(state, DIMC))

        heat = st["heat"]
        if heat:
            self.st_heat_lbl.config(
                text=(f"Tyre temps last lap: front {heat['front_avg']:.0f}C / rear {heat['rear_avg']:.0f}C"
                      f"  | hot {heat['pct_hot']:.0f}%  cold {heat['pct_cold']:.0f}% of the lap"))
        else:
            self.st_heat_lbl.config(text="Tyre temps last lap: --")
    def _on_ip_change(self, force=False):
        """force=True (Enter) reconnects even to the same IP, as a manual
        retry. FocusOut and dropdown selection only act when the IP actually
        changed: FocusOut fires every time the box loses focus, and each call
        used to drop the live connection, log a spurious "Connection
        dropped" and start another 60 s connect attempt."""
        new_ip = self.ip_var.get().strip()
        if not new_ip:
            return
        changed = new_ip != telem.get_ip()
        if not (changed or force):
            return
        if changed:
            runtime_config.save(PS_IP=new_ip)
            self.log_msg(f"IP changed to {new_ip}")
        self._start_telem()

    def _on_discover_ip(self):
        """Optional convenience: broadcast for a PS4/PS5 on the local network
        instead of typing its IP in by hand. Purely additive -- manual entry
        and the KNOWN_IPS dropdown both still work exactly as before."""
        self.discover_btn.config(state="disabled", text="Searching...")
        self.log_msg("Auto-detect: searching for a PS4/PS5 on your network...")
        threading.Thread(target=self._discover_ip_thread, daemon=True).start()

    def _discover_ip_thread(self):
        found_ip = None
        try:
            found_ip = telem.discover_ps_ip()
        except Exception as e:
            self.log_msg(f"Auto-detect error: {type(e).__name__}: {e}")
        finally:
            self._post(self._on_discover_done, found_ip)

    def _on_discover_done(self, found_ip):
        self.discover_btn.config(state="normal", text=L.AUTO_DETECT)
        if found_ip:
            self.ip_var.set(found_ip)
            self.log_msg(f"Auto-detect: found a console at {found_ip}")
            # force=True: a manual action like this should always (re)try the
            # connection, even if it happens to find the IP already in the
            # box (e.g. retrying after a non-IP connection problem).
            self._on_ip_change(force=True)
        else:
            self.log_msg("Auto-detect: no PS4/PS5 responded on the local network "
                          "-- make sure the console is on and on the same "
                          "Wi-Fi/network, then try again or enter the IP manually.")

    def _on_debug_toggle(self):
        runtime_config.DEBUG_LOG = self.debug_var.get()
        runtime_config.save(DEBUG_LOG=runtime_config.DEBUG_LOG)
        self.log_msg(f"Debug log {'enabled' if runtime_config.DEBUG_LOG else 'disabled'}")

    def _on_analytics_toggle(self):
        runtime_config.ANALYTICS_ENABLED = self.analytics_var.get()
        runtime_config.save(ANALYTICS_ENABLED=runtime_config.ANALYTICS_ENABLED)
        state = "enabled" if runtime_config.ANALYTICS_ENABLED else "disabled"
        self.log_msg(f"Anonymous usage analytics {state} (see gt7trace.netlify.app/privacy.html)")

    def _on_ghost_toggle(self):
        runtime_config.GHOST_ENABLED = self.ghost_var.get()
        runtime_config.save(GHOST_ENABLED=runtime_config.GHOST_ENABLED)
        if runtime_config.GHOST_ENABLED:
            self.log_msg("Top-lap ghost on -- fetches the leaderboard's fastest lap for your car and track")
        else:
            self.log_msg("Top-lap ghost off -- no leaderboard requests from the Dashboard")
            self.ghost_lbl.config(text="--", fg=theme.TXT)

    def _ghost_key(self):
        car, track = self.car_var.get().strip(), self.track_var.get().strip()
        return (car, track) if car and track else None

    def _maybe_fetch_ghost(self, now=None):
        """Once per car+track per run, ask the leaderboard for its fastest lap on
        a worker thread. Stays quiet if it's switched off, offline, or nobody has
        set a lap yet. The CAR and TRACK boxes are editable, so the pair must stay
        put for GHOST_SETTLE_S first -- typing a name by hand mustn't send a
        request per letter."""
        key = self._ghost_key()
        if key is None or not runtime_config.GHOST_ENABLED or key in self._ghost_fetching:
            return
        now = time.monotonic() if now is None else now
        if self._ghost_stable["key"] != key:
            self._ghost_stable.update(key=key, since=now)
            return
        if now - self._ghost_stable["since"] < GHOST_SETTLE_S:
            return
        self._ghost_fetching.add(key)

        def work():
            g = None
            try:
                row = leaderboard.get_top_lap_ghost(*key)
                if row:
                    g = ghost.build_ghost(row.get("samples"), row.get("lap_time_ms"))
                    if g is not None:
                        by = f" by {row['psn_name']}" if row.get("psn_name") else ""
                        self.log_msg(f"Top-lap ghost loaded: {ms_to_laptime(int(row['lap_time_ms']))}{by}")
            except Exception:
                g = None
            self._post(self._ghosts.__setitem__, key, g)

        threading.Thread(target=work, daemon=True).start()

    def _on_notify_toggle(self):
        """Desktop toast + sound on a new PB and when a race recording ends
        (notify.py checks config.NOTIFY_ENABLED each time, so it applies at once).
        Spoken alerts have their own VOICE ALERTS switch."""
        runtime_config.NOTIFY_ENABLED = self.notify_var.get()
        runtime_config.save(NOTIFY_ENABLED=runtime_config.NOTIFY_ENABLED)
        state = "on" if runtime_config.NOTIFY_ENABLED else "off"
        self.log_msg(f"Notifications {state} -- new personal best and race-end pop-ups and sounds")

    def _on_voice_toggle(self):
        runtime_config.VOICE_ENABLED = self.voice_var.get()
        runtime_config.save(VOICE_ENABLED=runtime_config.VOICE_ENABLED)
        if runtime_config.VOICE_ENABLED:
            how = "the system voice" if voice.available() else "the alert sound (no system voice found)"
            self.log_msg(f"Voice alerts on -- fuel and tyre alerts will use {how}")
            self._announcer.reset()
        else:
            self.log_msg("Voice alerts muted")

    def _speak_alerts(self, has_fuel, has_hot, has_cold, fuel_laps):
        """Say the fuel / tyre alerts that just turned on (or are due a repeat).
        voice.speak() hands the work to a background thread, so this never
        blocks the 10 Hz display refresh."""
        if not runtime_config.VOICE_ENABLED or session.paused:
            return
        active = {k for k, on in (("fuel", has_fuel), ("hot", has_hot), ("cold", has_cold)) if on}
        for line in self._announcer.update(active, time.monotonic(), fuel_laps=fuel_laps):
            voice.speak(line)

    def _speak_race_calls(self, d, snap):
        """Corner coaching cues (from last lap's tips) and the race engineer's pit call. Both go through the same
        VOICE ALERTS switch as the fuel and tyre alerts and never raise into the display refresh."""
        try:
            now = time.monotonic()
            msg = None
            if not session.paused:
                msg = self._live_coach.update(float(d.get("track_position") or 0), float(d.get("speed_kmh") or 0))
                lap, total = int(d.get("lap_number") or 0), int(d.get("total_laps") or 0)
                if not msg and total > 0 and lap > 0:
                    call = engineer.pit_call(lap, total, snap, last_called=self._radio["lap"])
                    if call:
                        msg = call[0]
                        self._radio["lap"] = lap
            if msg:
                self._radio.update(text=msg, until=now + 6.0)
                if runtime_config.VOICE_ENABLED:
                    voice.speak(msg)
        except Exception:
            pass

    def _on_update_check_toggle(self):
        # launcher.py reads config.UPDATE_CHECK_ENABLED at startup, so this
        # takes effect the next time TRACE is opened.
        runtime_config.UPDATE_CHECK_ENABLED = self.update_var.get()
        runtime_config.save(UPDATE_CHECK_ENABLED=runtime_config.UPDATE_CHECK_ENABLED)
        state = "on" if runtime_config.UPDATE_CHECK_ENABLED else "off"
        self.log_msg(f"Update check {state} -- applies the next time TRACE starts "
                     "(it only asks GitHub for the latest release number)")

    def _on_metrics_toggle(self):
        runtime_config.METRICS_ENABLED = self.metrics_var.get()
        runtime_config.save(METRICS_ENABLED=runtime_config.METRICS_ENABLED)
        if runtime_config.METRICS_ENABLED:
            addr = "0.0.0.0" if runtime_config.METRICS_BIND_ALL else "127.0.0.1"
            if self._safe_metrics_start(addr):
                where = f"http://{addr}:{runtime_config.METRICS_PORT}/metrics"
                self.log_msg(f"Metrics export enabled on {where}")
            else:
                self.metrics_var.set(False)
                runtime_config.METRICS_ENABLED = False
                runtime_config.save(METRICS_ENABLED=False)
                self.log_msg(f"Metrics export: failed to bind port {runtime_config.METRICS_PORT} (already in use?)")
        else:
            metrics_server.stop()
            self.log_msg("Metrics export disabled")

    def _on_metrics_bind_toggle(self):
        runtime_config.METRICS_BIND_ALL = self.metrics_bind_var.get()
        runtime_config.save(METRICS_BIND_ALL=runtime_config.METRICS_BIND_ALL)
        addr = "0.0.0.0" if runtime_config.METRICS_BIND_ALL else "127.0.0.1"
        if runtime_config.METRICS_ENABLED:
            metrics_server.stop()
            if self._safe_metrics_start(addr):
                if runtime_config.METRICS_BIND_ALL:
                    self.log_msg("Metrics now reachable from your LAN (0.0.0.0) -- "
                                  "anyone on your network can read live telemetry")
                else:
                    self.log_msg("Metrics restricted back to localhost only")
            else:
                self.log_msg(f"Metrics export: failed to bind port {runtime_config.METRICS_PORT} (already in use?)")
        else:
            state = "will bind to your LAN (0.0.0.0)" if runtime_config.METRICS_BIND_ALL else "will bind to localhost only"
            self.log_msg(f"Metrics export {state} next time it's enabled")

    def _on_auto_lap_toggle(self):
        runtime_config.AUTO_LAP_ENABLED = self.auto_lap_var.get()
        runtime_config.save(AUTO_LAP_ENABLED=runtime_config.AUTO_LAP_ENABLED)
        if runtime_config.AUTO_LAP_ENABLED:
            self.log_msg("Auto lap on -- in time trial / practice, laps record by themselves from the next start/finish crossing")
        else:
            self.log_msg("Auto lap off")

    def _on_keep_awake_toggle(self):
        runtime_config.KEEP_AWAKE_ENABLED = self.keep_awake_var.get()
        runtime_config.save(KEEP_AWAKE_ENABLED=runtime_config.KEEP_AWAKE_ENABLED)
        if runtime_config.KEEP_AWAKE_ENABLED:
            keepawake.set_awake(True)
            self.log_msg("Keep awake on -- the PC won't sleep or hibernate while the Dashboard is open")
        else:
            keepawake.set_awake(False)   # _poll turns it back on while telemetry is flowing
            self.log_msg("Keep awake off -- the PC stays awake only while telemetry is flowing")

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
        # set_ip and the Tk-variable read both happen here, on the Tk thread,
        # so a slow-to-start worker can't re-apply an IP that has since been
        # changed again. The generation number lets a superseded attempt
        # finish quietly instead of reporting on an IP no longer selected.
        ip = self.ip_var.get().strip()
        telem.set_ip(ip)
        self._connect_gen += 1
        threading.Thread(target=self._connect_thread,
                         args=(ip, self._connect_gen), daemon=True).start()

    def _connect_thread(self, ip, gen):
        self.log_msg(f"Connecting to {ip}...")
        result = telem.wait_for_connection(timeout=60)
        if gen != self._connect_gen:
            return
        if result is not None:
            self.log_msg("Connected! Telemetry live.")
            self._post(self._remember_good_ip, ip)
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
        """10 Hz display refresh. Reschedules itself in `finally` so one bad
        frame can't stop the display updating for the rest of the session --
        which an uncaught exception here used to do, silently in the
        windowed builds."""
        try:
            self._poll_once()
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if err != self._last_poll_error:
                self._last_poll_error = err
                self.log_msg(f"Display update error (still running): {err}")
        finally:
            self.after(100, self._poll)

    def _poll_once(self):
        d  = telem.get_snapshot()
        ok = telem.is_connected()

        self.conn_dot.config(
            text=L.STATUS_ONLINE if ok else "● OFFLINE",
            fg=theme.GREEN if ok else theme.DIM
        )

        if not ok:
            # Log once, right when a previously-live connection drops, so a
            # console reboot / cable pull / network switch shows up in the
            # log panel instead of silently sitting at "OFFLINE".
            if getattr(self, "_was_connected", False) and not getattr(self, "_drop_logged", False):
                self._drop_logged = True
                reason = telem.get_last_error() or "no packets received"
                self.log_msg(f"Connection dropped: {reason}")
                if session.recording or session.race_recording:
                    what = "race" if session.race_recording else "lap"
                    self.log_msg(f"WARNING: packets stopped mid-{what} -- the recording will be "
                                 "incomplete. If your PC went to sleep/hibernate, turn that off "
                                 "(Power & sleep -> Never) while you race.")
            keepawake.set_awake(bool(runtime_config.KEEP_AWAKE_ENABLED))
            self._was_connected = False
            for _a in (self.alert_hot, self.alert_cold, self.alert_fuel):
                _a.grid_remove()
            return
        self._was_connected = True
        self._drop_logged = False
        keepawake.set_awake(True)

        if runtime_config.METRICS_ENABLED:
            metrics_server.update(d)

        FG = theme.TXT

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

        # Driver aids -- YES in accent color when active, NO dimmed otherwise
        for key, lbl in self._aid_lbls.items():
            active = bool(d.get(key))
            lbl.config(text="YES" if active else "NO",
                       fg=theme.RED_2 if active else theme.DIM)

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

        self._auto_lap_step(d, time.monotonic())
        self._battery_step(time.monotonic())
        self.lap_lbl.config(text=f"{cur_lap}/{tot_lap}")
        self.best_lbl.config(text=ms_to_laptime(best_ms))
        self.last_lbl.config(text=ms_to_laptime(last_ms))
        # GT7 only sends the starting-grid slot (and -1 once the race starts),
        # not a live race position -- so this is labelled GRID and blanks out.
        self.pos_lbl.config(text=f"P{cur_pos}/{tot_pos}" if cur_pos > 0 and tot_pos > 0 else "--")
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
            getattr(self, attr).config(text=f"{float(d.get(key) or 0) * 1000:.0f}")  # metres -> mm

        # Engine and Fluids
        fuel_r = float(d.get("fuel_remaining") or 0)
        fuel_c = float(d.get("fuel_capacity") or 0)
        is_ev  = bool(d.get("is_ev"))
        # GT7 reports fuel against a capacity of 100 for nearly every car
        # (i.e. a percentage), 5 for karts and 0 for EVs -- not litres.
        if fuel_c == 100 or is_ev:
            fuel_str = f"{fuel_r:.1f}%" + (" batt" if is_ev else "")
        else:
            fuel_str = f"{fuel_r:.1f}/{fuel_c:.0f}"
        self.fuel_lbl.config(text=fuel_str)

        # Fuel & tyre strategy: forget everything on a car change, then feed
        # every snapshot to the tracker (it works out laps/stints itself).
        _strat_car = d.get("car_id")
        if _strat_car and _strat_car != self._strat_car_id:
            if self._strat_car_id is not None:
                self._strategy.reset_all()
            self._strat_car_id = _strat_car
        self._strategy.update(
            cur_lap, tot_lap, fuel_r, last_ms,
            (d.get("tyre_temp_fl"), d.get("tyre_temp_fr"),
             d.get("tyre_temp_rl"), d.get("tyre_temp_rr")), spd)
        _st = self._strategy.snapshot()
        self._update_strategy_labels(_st, cur_lap, "%" if (fuel_c == 100 or is_ev) else "")

        # FUEL LAPS: the tracker's estimate; before it has seen a clean lap,
        # fall back to the burn of the last lap that was recorded/saved
        _fl = _st["laps_of_fuel"]
        if _fl is None and fuel_r > 0 and self._fuel_per_lap > 0:
            _fl = fuel_r / self._fuel_per_lap
        _fuel_laps_num = _fl if _fl is not None else 999
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
                f"G{i+1}:{r:.3f}" for i, r in enumerate(ratios)
                if isinstance(r, (int, float)) and r > 0
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
            fg=theme.GREEN if d.get("is_ev") else FG)
        _car_id_val = int(d.get("car_id") or 0)
        self.carid_lbl.config(text=str(_car_id_val))
        _auto_name = car_box_autofill(_car_id_val, self.car_var.get(), self._last_autofilled_car_id)
        if _auto_name is not None:
            # a name typed before any car was auto-filled is the user's guess for an unknown ID
            guessed = (self.car_var.get().strip() or None) if self._last_autofilled_car_id is None else None
            self.car_var.set(_auto_name)
            self._last_autofilled_car_id = _car_id_val
            if (_auto_name == unknown_car_label(_car_id_val)
                    and _car_id_val not in self._submitted_unknown_car_ids):
                self._submitted_unknown_car_ids.add(_car_id_val)
                threading.Thread(
                    target=leaderboard.submit_car_id,
                    args=(_car_id_val,),
                    kwargs={"guessed_name": guessed},
                    daemon=True,
                ).start()
                self.log_msg(f"Unrecognized car ID {_car_id_val} -- submitted to community inbox")
        self._maybe_fetch_ghost()
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
                      "S": "Sand", "s": "Snow"}
        for i, key in enumerate(["fl", "fr", "rl", "rr"]):
            code = surface[i] if surface and i < len(surface) else None
            self._surf_lbls[key].config(
                text=surf_names.get(code, code or "--"),
                fg=theme.GREEN if code == "T" else (theme.GOLD if code else FG))

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

        has_hot, has_cold = tyre_alerts(tyre_temps_live)
        has_fuel = _fuel_laps_num < 2

        if has_hot:
            self.alert_hot.config(bg=theme.RED_2 if self._flash_on else theme.RED_DIM)
            self.alert_hot.grid()
        else:
            self.alert_hot.grid_remove()

        if has_cold:
            self.alert_cold.config(bg=theme.TEAL if self._flash_on else theme.BG2)
            self.alert_cold.grid()
        else:
            self.alert_cold.grid_remove()

        if has_fuel:
            self.alert_fuel.config(bg=theme.GOLD if self._flash_on else theme.GOLD_DIM)
            self.alert_fuel.grid()
        else:
            self.alert_fuel.grid_remove()

        self._speak_alerts(has_fuel, has_hot, has_cold, _fuel_laps_num)
        self._speak_race_calls(d, _st)

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
        # Matches on `track_position`: metres driven since GT7's lap counter
        # last changed (udp.py integrates it from world position and restarts
        # it every lap). It's the same field stored per sample in the
        # track+car reference lap file, so the two are directly comparable.
        if cur_lap != self._delta_prev_lap:
            self._delta_lap_start_t = time.monotonic()
            self._delta_prev_lap    = cur_lap

        cur_track_pos = float(d.get("track_position") or 0)

        cur_lap_ms_live = d.get("current_lap_ms")
        _in_lap = lap_clock_running(cur_lap, tot_lap, session.paused,
                                    bool(d.get("car_on_track", True)), d.get("loading"))
        if not _in_lap:
            # menu / garage / loading: no live lap clock
            cur_elapsed = 0.0
            self._delta_lap_start_t = None
        elif cur_lap_ms_live is not None and cur_lap_ms_live > 0:
            cur_elapsed = cur_lap_ms_live / 1000.0
        elif self._delta_lap_start_t is not None:
            cur_elapsed = time.monotonic() - self._delta_lap_start_t
        else:
            cur_elapsed = 0.0

        self.cur_lbl.config(text=ms_to_laptime(int(cur_elapsed * 1000)) if cur_elapsed > 0 else "--")

        delta_text, delta_color = "--", FG
        _delta_val = None
        track_name = self.track_var.get().strip()
        if track_name and cur_elapsed > 0.5 and cur_track_pos > 0:
            ref_samples = self._get_reference_samples(
                telem.sanitize(track_name), telem.sanitize(self.car_var.get().strip() or "unknown"))
            if ref_samples:
                # Binary search on the pre-sorted positions instead of a
                # linear min() over every reference sample, 10x a second.
                pos = self._delta_ref_cache["positions"]
                j = bisect.bisect_left(pos, cur_track_pos)
                cands = [k for k in (j - 1, j) if 0 <= k < len(pos)]
                nearest = ref_samples[min(cands, key=lambda k: abs(pos[k] - cur_track_pos))]
                ref_t = nearest.get("t")
                if ref_t is not None:
                    delta = cur_elapsed - ref_t
                    _delta_val  = delta
                    delta_text  = f"{delta:+.2f}s"
                    delta_color = theme.RED_2 if delta > 0 else theme.GREEN
        self.delta_lbl.config(text=delta_text, fg=delta_color)

        # ── Live gap to the leaderboard's fastest lap ────────────────────────
        top = self._ghosts.get(self._ghost_key()) if runtime_config.GHOST_ENABLED else None
        gap = top.gap(cur_track_pos, cur_elapsed) if top is not None and cur_elapsed > 0.5 else None
        self.ghost_lbl.config(text=ghost.format_gap(gap)[0], fg=ghost.format_gap(gap)[1])

        if runtime_config.WEBUI_ENABLED:
            try:
                _diag = telem.get_diagnostics()
                _rx = _diag.get("packets_received") or 0
                _bad = (_diag.get("decrypt_failures") or 0) + (_diag.get("parse_failures") or 0)
                webui_server.update(d, {
                    "connected": True, "cur_ms": int(cur_elapsed * 1000) if cur_elapsed > 0 else None,
                    "delta": _delta_val, "vs_top": gap, "strategy": _st,
                    "car": self.car_var.get().strip(), "track": track_name,
                    "coach": self._radio["text"] if time.monotonic() < self._radio["until"] else "",
                    "packet_loss": (100.0 * _bad / (_rx + _bad)) if (_rx + _bad) else 0.0})
            except Exception:
                pass   # the web UI is optional: never let it break the Dashboard poll

    # ─────────────────────────────────────────────────────────────────────────
    # Shutdown
    # ─────────────────────────────────────────────────────────────────────────
    # Hover help for the header controls. Matched by the start of each control's label.
    _TIPS = (
        ("\u2190 Menu", "Back to the launcher to pick another tool. Asks first if a recording is unsaved."),
        ("Record Race", "Record a whole race. Races with a set number of laps start and stop by themselves; "
                        "for a timed race press it yourself, and again to stop."),
        ("Record Lap", "Arm a lap recording: it starts when you cross the start line. Press again to stop."),
        ("Session Summary", "A table of this session's laps and times."),
        ("Export Session", "Save this session's laps and results to a JSON file."),
        ("Auto-Detect", "Find your console on the network automatically."),
        ("METRICS", "Serve live gauges for Grafana or Prometheus on this PC. Off by default."),
        ("ALLOW REMOTE (METRICS)", "Let other devices on your network read the metrics. There is no password, "
                                   "so only do this on a network you trust."),
        ("ALLOW REMOTE (WEB UI)", "Let phones and tablets on your network open the web dashboard. They need the "
                                  "6-digit code (or the QR) shown here; this PC itself never does."),
        ("LOG OUT ALL DEVICES", "Sign every phone and tablet out of the web dashboard, including remembered ones."),
        ("WEB UI", "Serve a live, read-only dashboard to phones and tablets on your Wi-Fi. Off by default."),
        ("KEEP AWAKE", "Stop this PC from sleeping while the Dashboard is open."),
        ("AUTO LAP", "In time trial and practice, record laps by themselves from the next start/finish crossing."),
        ("VOICE ALERTS", "Speak fuel-low and tyre hot/cold warnings out loud."),
        ("NOTIFICATIONS", "A small pop-up on a new personal best and when a race recording ends."),
        ("TOP-LAP GHOST", "Show your gap to the fastest public lap for this car and track "
                          "(one read-only request)."),
        ("CHECK FOR UPDATES", "Let the Launcher look for a newer TRACE release when it starts."),
        ("SHARE USAGE DATA", "Send anonymous usage statistics (tool, version, OS). See Privacy."),
        ("DEBUG LOG", "Show extra diagnostic detail in the log panel."),
        ("REC RATE", "How many samples per second a recording keeps."),
    )

    def _add_tooltips(self, *frames):
        for frame in frames:
            for w in frame.winfo_children():
                try:
                    label = str(w.cget("text"))
                except tk.TclError:
                    continue
                for prefix, tip in self._TIPS:
                    if label.startswith(prefix):
                        theme.tooltip(w, tip)
                        break

    def _go_menu(self):
        """Back to the launcher menu (same close path as the window's X: unsaved recordings are asked about)."""
        self._to_menu = True
        self._on_close()
        try:
            if self.winfo_exists():
                self._to_menu = False   # the close was cancelled: stay here
        except tk.TclError:
            pass

    def _on_close(self):
        """Window-close handler. The Dashboard used to have no
        WM_DELETE_WINDOW protocol at all (unlike Lap/Race Analyst, which both
        bind _quit), which meant two things went wrong silently: an
        in-progress recording lives only in session.samples/race_samples
        until a lap or race completes, so closing mid-race threw away
        everything with no prompt; and metrics_server.stop() was only ever
        called from the toggle, so the exporter was left running."""
        keepawake.set_awake(False)
        pending = []
        lap_pending  = (session.recording or session.waiting) and bool(session.samples)
        race_pending = session.race_recording and bool(session.race_samples)
        if lap_pending:
            pending.append(f"a lap in progress ({len(session.samples)} samples)")
        if race_pending:
            pending.append(f"a race in progress ({len(session.race_samples)} samples)")

        if pending:
            answer = messagebox.askyesnocancel(
                "Still recording",
                "TRACE is still recording:\n\n  - " + "\n  - ".join(pending) +
                "\n\nSave before closing?\n\n"
                "Yes  = save, then close\n"
                "No   = discard, then close\n"
                "Cancel = keep recording",
                parent=self)
            if answer is None:
                return  # keep the window (and the recording) alive
            if answer:
                # Save the race first: it is usually the longer recording.
                # Both saves log and return None on failure instead of
                # raising, so ask before throwing a recording away.
                failed = []
                if race_pending:
                    session.race_recording = False
                    if self._save_race(list(session.race_samples), final=False) is None:
                        failed.append("race")
                if lap_pending:
                    session.recording = session.waiting = False
                    if self._save_lap(list(session.samples), lap_time_ms=None,
                                      incomplete=True, prompt=False,
                                      final=False) is None:
                        failed.append("lap")
                if failed and not messagebox.askyesno(
                        "Save failed",
                        f"Couldn't save the in-progress {' or '.join(failed)} anywhere "
                        "-- the log panel says why.\n\nClose anyway and lose it?",
                        parent=self):
                    # Keep the window, and resume whatever didn't save so Stop
                    # can retry once the problem (e.g. a full disk) is fixed.
                    if "race" in failed:
                        session.race_recording = True
                    if "lap" in failed:
                        session.recording = True
                    return

        session.recording = session.waiting = session.race_recording = False
        for t in list(self._save_threads):
            t.join(timeout=10)
        try:
            self._show_close_recap()
        except Exception:
            pass  # a recap problem must never keep the window open
        try:
            metrics_server.stop()
        except Exception:
            pass  # never block the close on exporter teardown
        try:
            webui_server.stop()
        except Exception:
            pass
        self._closing = True
        self.destroy()

    def _show_close_recap(self):
        """Quick recap shown once, right before the window actually closes --
        not to be confused with _show_session_summary (the per-race
        summary opened from its own toolbar button). Reads session.laps_saved,
        which _save_lap's _do_save() appends to for every lap saved this
        session regardless of where the file landed, so no disk re-scan is
        needed. Skipped entirely if nothing was recorded -- no point showing
        an empty recap on every close."""
        laps = session.laps_saved
        if not laps:
            return

        complete = [d for d in laps if not d.get("incomplete") and (d.get("lap_time_s") or 0) > 0]
        if complete:
            times = [d["lap_time_s"] for d in complete]
            best_str = ms_to_laptime(int(min(times) * 1000))
            avg_str  = ms_to_laptime(int(sum(times) / len(times) * 1000))
        else:
            best_str = avg_str = "--"

        fuel_used = sum(float(d.get("fuel_used") or 0) for d in laps)

        dlg = tk.Toplevel(self)
        dlg.title("Session Recap")
        dlg.configure(bg=theme.BG)
        dlg.resizable(False, False)
        dlg.transient(self)
        _grab_when_visible(dlg)

        tk.Label(dlg, text=_tk_text("🏁 Session Recap"), fg=theme.RED_2, bg=theme.BG,
                 font=(theme.MONO, 12, "bold")).pack(pady=(14, 10), padx=24)

        rows = [
            ("Laps completed", str(len(complete))),
            ("Best lap",       best_str),
            ("Average lap",    avg_str),
            ("Fuel used",      f"{fuel_used:.1f}%" if fuel_used > 0 else "--"),
        ]
        body = tk.Frame(dlg, bg=theme.BG)
        body.pack(padx=24, pady=(0, 10))
        for label, value in rows:
            r = tk.Frame(body, bg=theme.BG)
            r.pack(fill="x", pady=2)
            tk.Label(r, text=label, fg=theme.MID, bg=theme.BG,
                     font=(theme.MONO, 10), width=15, anchor="w").pack(side="left")
            tk.Label(r, text=value, fg=theme.TXT, bg=theme.BG,
                     font=(theme.MONO, 10, "bold"), anchor="w").pack(side="left")

        tk.Button(dlg, text="Close", command=dlg.destroy, bg=theme.RED_2, fg="#000",
                  font=(theme.MONO, 10, "bold"), relief="flat", padx=16, pady=4,
                  cursor="hand2").pack(pady=(6, 16))

        dlg.update_idletasks()
        dlg.geometry(f"+{self.winfo_rootx() + 60}+{self.winfo_rooty() + 60}")
        try:
            dlg.wait_window()
        except tk.TclError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Recording. Samples are chosen on the UDP receive thread, one decision per
    # packet (_on_packet_tap), and queued with their arrival time. The Tk timer
    # below only drains that queue on the UI thread, where lap detection,
    # saving and button updates happen. Recording used to read
    # telem.get_snapshot() from a Tk timer instead, which on Windows fires every
    # 15.6-31 ms and slows further when the UI is busy, so a "60 Hz" recording
    # came out at 35-48 Hz. Nothing is invented or interpolated between real
    # packets (no upsampling).
    # ─────────────────────────────────────────────────────────────────────────
    def _on_packet_tap(self, d, mono):
        """Receive thread, once per good packet (~60/s). Keep it cheap and free
        of Tk calls: decide whether this packet is a sample at the chosen rate
        and queue a copy for _record_tick."""
        if self._closing or session.paused:
            return
        dt = 1.0 / max(self._record_rate, 1)
        if session.recording or session.waiting:
            ok, self._rec_due = _thin_accept(self._rec_due, mono, dt)
            if ok:
                self._rec_q.append(("lap", mono, dict(d)))
        if session.race_recording:
            ok, self._rec_due_race = _thin_accept(self._rec_due_race, mono, dt)
            if ok:
                self._rec_q.append(("race", mono, dict(d)))

    def _record_tick(self):
        try:
            q = self._rec_q
            while q:
                try:
                    kind, mono, d = q.popleft()
                except IndexError:
                    break
                if kind == "lap":
                    if (session.recording or session.waiting) and mono >= self._lap_rec_since:
                        self._record_sample(d, mono)
                elif session.race_recording and mono >= self._race_rec_since:
                    self._record_race_sample(d, mono)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if err != self._last_record_error:
                self._last_record_error = err
                self.log_msg(f"Recording error: {err}")
        finally:
            # Always reschedule. An exception used to skip this line and stop
            # recording for the rest of the session, silently in windowed builds.
            if not self._closing:
                self.after(_RECORD_TICK_MS, self._record_tick)

    # ─────────────────────────────────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────────────────────────────────
    def log_msg(self, msg):
        self._uiq.put((self._log_append, (msg,)))

    def _log_append(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")

    # ─────────────────────────────────────────────────────────────────────────
    # Record
    # ─────────────────────────────────────────────────────────────────────────
    # ── Low battery ──────────────────────────────────────────────────────────
    def _battery_beep(self, loud):
        """Short alert (loud=False) or a harsh siren burst (loud=True), off the UI thread."""
        if self._beeping:
            return
        self._beeping = True

        def run():
            try:
                try:
                    import winsound
                except ImportError:
                    winsound = None
                if winsound is None:
                    return
                if loud:
                    for _ in range(3):
                        winsound.Beep(2800, 300)
                        winsound.Beep(1500, 300)
                else:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
            finally:
                self._beeping = False
        threading.Thread(target=run, daemon=True).start()

    def _show_battery_bar(self, pct):
        if self._batt_bar is None:
            bar = tk.Frame(self, bg=theme.RED)
            self._batt_lbl = tk.Label(bar, text="", bg=theme.RED, fg="white",
                                      font=(theme.MONO, 11, "bold"), pady=4)
            self._batt_lbl.pack(side="left", padx=10)
            tk.Button(bar, text="OK, I'll charge", command=self._battery_ack, relief="flat",
                      bg=theme.BG3, fg="white", font=(theme.MONO, 9),
                      padx=8).pack(side="right", padx=10, pady=3)
            self._batt_bar = bar
        self._batt_lbl.config(text=f"LOW BATTERY ({pct:.0f}%) - plug in your charger now, "
                                   f"or this lap/race will be lost when the laptop dies")
        if not self._batt_bar.winfo_ismapped():
            first = [w for w in self.pack_slaves() if w is not self._batt_bar]
            if first:
                self._batt_bar.pack(fill="x", side="top", before=first[0])
            else:
                self._batt_bar.pack(fill="x", side="top")

    def _hide_battery_bar(self):
        if self._batt_bar is not None and self._batt_bar.winfo_ismapped():
            self._batt_bar.pack_forget()

    def _battery_ack(self):
        self._batt_ack = True
        self._hide_battery_bar()

    def _battery_step(self, now):
        """~10 Hz caller, throttled. Warns (bar, then siren if ignored) while a lap or
        race is in progress on low battery."""
        if now < self._batt_next:
            return
        self._batt_next = now + BATTERY_CHECK_S
        batt = read_battery()
        active = bool(session.recording or session.waiting or session.race_recording)
        if not battery_needs_warning(batt, active):
            if self._batt_warned_at is not None:
                self._hide_battery_bar()
                self._batt_warned_at = None
                self._batt_ack = False
                self._batt_crit = False
            return
        pct = batt[0]
        if self._batt_warned_at is None:
            self._batt_warned_at = now
            self._batt_ack = False
            self._show_battery_bar(pct)
            self.log_msg(f"WARNING: battery at {pct:.0f}% and not charging -- plug in so the recording isn't lost")
            self._battery_beep(False)
            return
        if pct < BATTERY_CRIT_PCT and not self._batt_crit:
            self._batt_crit = True
            self._batt_ack = False
            self._batt_warned_at = now - BATTERY_ALARM_AFTER_S
            self.log_msg(f"CRITICAL: battery at {pct:.0f}% -- charge NOW")
        if not self._batt_ack:
            self._show_battery_bar(pct)
        if battery_alarm_due(self._batt_warned_at, now, self._batt_ack):
            self._battery_beep(True)

    # ── Track selection ──────────────────────────────────────────────────────
    def _warn_no_track(self, modal):
        """Called when a recording starts. Never blocks recording; the track can still
        be chosen when the lap/race is saved."""
        self._save_unknown_ok = False
        if self.track_var.get().strip():
            return
        self.log_msg("WARNING: no track selected -- pick one in the TRACK box "
                     "(you'll be asked again when it is saved)")
        if modal:
            messagebox.showwarning(
                "No track selected",
                "No track is selected.\n\nPick one in the TRACK box so this recording is "
                "filed under the right track. You can still record, and you'll be asked "
                "again when it is saved.", parent=self)
        else:
            self.bell()

    # ── track auto-detect ────────────────────────────────────────────────────
    def _td_fetch_shared(self):
        rows = trackdetect.fetch_shared()
        if rows is not None:
            self._td_store.set_shared(rows)

    def _td_race_reset(self):
        self._td_race_done = False     # offer made or given up for this race
        self._td_race_idx = []         # sample indexes where the lap counter ticked up
        self._td_race_tried = set()
        self._td_race_lap = None

    def _td_race_step(self):
        """Races start on the grid, so recognise the track at a start-line crossing
        instead: when the lap counter ticks up the car is on the line."""
        if self._td_race_done:
            return
        smp = session.race_samples
        if not smp:
            return
        if self.track_var.get().strip():
            self._td_race_done = True
            return
        try:
            lap = int(smp[-1].get("lap_number"))
        except (TypeError, ValueError):
            return
        if self._td_race_lap is not None and lap > self._td_race_lap and len(self._td_race_idx) < 2:
            self._td_race_idx.append(len(smp) - 1)
        self._td_race_lap = lap
        for i in self._td_race_idx:
            if i in self._td_race_tried:
                continue
            fp = trackdetect.fingerprint_of(smp[i:i + 1500])
            if fp is None:
                continue
            self._td_race_tried.add(i)
            for name, _d in self._td_store.match(fp):
                if name not in self._td_rejected:
                    self._td_race_done = True
                    self._show_track_offer(name)
                    return
        if len(self._td_race_tried) >= 2:
            self._td_race_done = True

    def _td_step(self, lap):
        """Called per recorded sample. Once per lap, when no track is set and the car
        has driven ~20 m from the line, offer the layout whose learned start matches."""
        if self._td_lap == lap or not session.recording:
            return
        if self.track_var.get().strip():
            self._td_lap = lap
            return
        fp = trackdetect.fingerprint_of(session.samples)
        if fp is None:
            if len(session.samples) > 900:
                self._td_lap = lap
            return
        self._td_lap = lap
        for name, _d in self._td_store.match(fp):
            if name not in self._td_rejected:
                self._show_track_offer(name)
                return

    def _show_track_offer(self, name):
        self._td_guess = name
        if self._td_bar is None:
            bar = tk.Frame(self, bg=theme.TEAL)
            self._td_lbl = tk.Label(bar, text="", bg=theme.TEAL, fg="white",
                                    font=(theme.MONO, 11, "bold"), pady=4)
            self._td_lbl.pack(side="left", padx=10)
            tk.Button(bar, text="Not this", command=self._td_decline, relief="flat",
                      bg=theme.BG3, fg="white", font=(theme.MONO, 9),
                      padx=8).pack(side="right", padx=(0, 10), pady=3)
            tk.Button(bar, text="Use it", command=self._td_accept, relief="flat",
                      bg=theme.GREEN, fg="white", font=(theme.MONO, 9, "bold"),
                      padx=8).pack(side="right", padx=4, pady=3)
            self._td_bar = bar
        self._td_lbl.config(text=f"Looks like {name} - use it as the track?")
        if not self._td_bar.winfo_ismapped():
            first = [w for w in self.pack_slaves() if w is not self._td_bar]
            if first:
                self._td_bar.pack(fill="x", side="top", before=first[0])
            else:
                self._td_bar.pack(fill="x", side="top")

    def _td_hide(self):
        if self._td_bar is not None and self._td_bar.winfo_ismapped():
            self._td_bar.pack_forget()

    def _td_accept(self):
        if self._td_guess and not self.track_var.get().strip():
            self.track_var.set(self._td_guess)
            self.log_msg(f"Track set to {self._td_guess} (detected from the start line)")
        self._td_hide()

    def _td_decline(self):
        if self._td_guess:
            self._td_rejected.add(self._td_guess)
        self._td_hide()

    def _td_learn(self, ui_track, samples, incomplete):
        """After a confirmed, complete lap on a real layout, remember its start-line
        fingerprint locally (and share it when usage sharing is on)."""
        try:
            if incomplete or not ui_track or not trackdetect.can_learn(ui_track, samples):
                return
            fp = trackdetect.fingerprint_of(samples)
            if fp is None or not self._td_store.add(ui_track, fp):
                return
            if runtime_config.ANALYTICS_ENABLED:
                length = trackdetect.lap_length(samples)
                threading.Thread(target=trackdetect.submit_shared,
                                 args=(ui_track, fp, length), daemon=True).start()
        except Exception:
            pass

    def _ask_track_for_save(self, what, samples=None):
        """No track set at save time: confirm, and let the user pick the track they drove.
        Returns the chosen track name, or '' to save as unknown (closing the box counts).
        With samples, likely tracks (start-line match, then lap length) go first."""
        if self._save_unknown_ok:
            return ""
        hint = []
        if samples:
            try:
                if what == "race":
                    fps = trackdetect.race_fingerprints(samples)
                    lengths = trackdetect.race_lap_lengths(samples)[:2]
                else:
                    fps = [trackdetect.fingerprint_of(samples)]
                    lengths = [trackdetect.lap_length(samples)]
                for fp in fps:
                    for n, _d in self._td_store.match(fp):
                        if n not in hint:
                            hint.append(n)
                for ln in lengths:
                    for n in trackdetect.shortlist_by_length(ln):
                        if n not in hint:
                            hint.append(n)
            except Exception:
                hint = []
        dlg = tk.Toplevel(self)
        dlg.title("No track selected")
        dlg.configure(bg=theme.BG3)
        dlg.transient(self)
        choice = {"v": ""}
        tk.Label(dlg, text=f"No track is selected, so this {what} would be saved as 'unknown'.\n"
                           "Are you sure? Pick the track you drove on:",
                 bg=theme.BG3, fg="white", justify="left",
                 font=(theme.MONO, 10)).pack(padx=16, pady=(14, 8))
        var = tk.StringVar(value=hint[0] if hint else "")
        names = track_db.all_track_names()
        ttk.Combobox(dlg, textvariable=var, values=hint + [n for n in names if n not in hint],
                     width=40).pack(padx=16, pady=4)
        if hint:
            tk.Label(dlg, text="Best guesses are listed first.", bg=theme.BG3, fg=theme.MID,
                     font=(theme.MONO, 8)).pack()
        row = tk.Frame(dlg, bg=theme.BG3)
        row.pack(pady=12)

        def use():
            choice["v"] = var.get().strip()
            dlg.destroy()

        def unknown():
            choice["v"] = ""
            dlg.destroy()
        tk.Button(row, text="Save to this track", command=use, bg=theme.GREEN, fg="white",
                  relief="flat", padx=10).pack(side="left", padx=6)
        tk.Button(row, text="Save as unknown", command=unknown, bg=theme.BG3, fg="white",
                  relief="flat", padx=10).pack(side="left", padx=6)
        dlg.protocol("WM_DELETE_WINDOW", unknown)
        dlg.update_idletasks()
        try:
            dlg.geometry(f"+{self.winfo_rootx() + 120}+{self.winfo_rooty() + 120}")
            dlg.grab_set()
        except tk.TclError:
            pass
        self.wait_window(dlg)
        if choice["v"]:
            self.track_var.set(choice["v"])
        else:
            self._save_unknown_ok = True
        return choice["v"]

    def _auto_lap_disarm(self, why):
        """Drop an auto-armed lap without saving: leaving a session mid-lap would
        otherwise leave a stray partial lap behind."""
        n = len(session.samples)
        session.recording = False
        session.waiting   = False
        session.samples   = []
        self._auto_lap_active = False
        self._auto_lap_since = self._auto_lap_off_since = None
        self.rec_btn.config(text=L.RECORD_LAP, bg=theme.BG3)
        self.log_msg(f"Auto lap: {why}" + (f" -- partial lap ({n} samples) discarded" if n else ""))

    def _auto_lap_step(self, d, now):
        """~10 Hz, UI thread. Arms Record Lap while driving in a non-race session
        and disarms it when the session is left. Never touches a lap the user armed.
        The rules are core.recorder.auto_lap_decide, shared with the terminal Recorder."""
        st = rec_core.AutoLapState()
        st.since, st.off_since = self._auto_lap_since, self._auto_lap_off_since
        st.active, st.block = self._auto_lap_active, self._auto_lap_block
        act = rec_core.auto_lap_decide(session, st, d, now, bool(runtime_config.AUTO_LAP_ENABLED))
        self._auto_lap_since, self._auto_lap_off_since = st.since, st.off_since
        self._auto_lap_active, self._auto_lap_block = st.active, st.block
        if act is None:
            return
        if act[0] == "disarm":
            self._auto_lap_disarm(act[1])
        else:
            self._start_record()
            self.log_msg("Auto lap: armed -- recording starts at the next start/finish crossing")
            self._warn_no_track(False)

    def _toggle_record(self):
        if session.recording or session.waiting:
            self._stop_record()
        else:
            self._start_record()
            self._warn_no_track(True)

    def _start_record(self):
        self._rec_due = 0.0
        self._lap_rec_since = time.monotonic()
        session.waiting   = True
        session.recording = False
        session.samples   = []
        session.cur_lap   = telem.get_int("lap_number")
        session.prev_lap  = session.cur_lap
        self.rec_btn.config(text=L.WAITING_FOR_START_LINE, bg=theme.GOLD)
        self.log_msg("Armed -- cross the start line to begin recording")

    def _stop_record(self):
        if self._auto_lap_active:
            # the user stopped an auto-armed lap: stay off until they leave this session
            self._auto_lap_active = False
            self._auto_lap_block = True
        session.recording = False
        session.waiting   = False
        self.rec_btn.config(text=L.RECORD_LAP, bg=theme.BG3)
        samples, session.samples = session.samples, []
        if samples:
            self._save_lap(samples, lap_time_ms=None, incomplete=True)
        else:
            self.log_msg("Stopped -- no samples recorded.")

    # ─────────────────────────────────────────────────────────────────────────
    # Record race (continuous, multi-lap, until manually stopped)
    # ─────────────────────────────────────────────────────────────────────────
    def _on_race_start(self, parsed):
        self._post(self._handle_race_start)

    def _handle_race_start(self):
        if not session.race_recording:
            self._start_record_race()
            self.log_msg("Auto: race start detected -- recording started")
            self._warn_no_track(False)

    def _on_race_end(self, parsed):
        self._post(self._handle_race_end)

    def _handle_race_end(self):
        if session.race_recording:
            self.log_msg("Auto: race end detected -- stopping recording")
            self._stop_record_race(notify_end=True)

    def _on_pause(self, parsed):
        self._post(self._handle_pause)

    def _handle_pause(self):
        if not session.paused:
            session.paused_at = time.monotonic()
        session.paused = True
        self.log_msg("Auto: game paused -- recording frozen")

    def _on_resume(self, parsed):
        self._post(self._handle_resume)

    def _handle_resume(self):
        # Shift the recording clocks forward by the pause length, so sample
        # `t`, the elapsed-time lap fallback and race_duration_s all exclude
        # time spent in the pause menu instead of showing a gap / inflating it.
        gap = rec_core.pause_shift(session, time.monotonic())
        if gap and self._delta_lap_start_t is not None:
            self._delta_lap_start_t += gap
        self.log_msg("Auto: game resumed -- recording continues")

    def _on_debug_state(self, snap):
        self._post(self._handle_debug_state, snap)

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
            self._warn_no_track(True)

    def _start_record_race(self):
        rec_core.race_begin(session, time.monotonic())
        self._race_rec_since   = session.race_start_t
        self._rec_due_race = 0.0
        self._td_race_reset()
        self.race_btn.config(text=L.STOP_RACE_RECORDING, bg=theme.RED_2)
        self.log_msg("Race recording started")

    def _stop_record_race(self, notify_end=False):
        self.race_btn.config(text=L.RECORD_RACE, bg=theme.BG3)
        samples = rec_core.race_end(session)
        if samples:
            self._save_race(samples, background=True, notify_end=notify_end)
        else:
            self.log_msg("Race recording stopped -- no samples recorded.")
            if notify_end:
                notify.notify("🏁 Race Ended", "No samples were recorded.", root=self)

    def _record_race_sample(self, d, now=None):
        # race_step re-checks the flag: _stop_record_race() can fire from inside this very method, and an
        # already-queued tick must not append (or re-trip the limit) after the race has been saved.
        # The append / warn / limit rules are core.recorder.race_step, shared with the terminal Recorder.
        rec_core.race_step(session, d, now, self._record_rate, self.log_msg, self._stop_record_race,
                           on_sample=getattr(self, "_td_race_step", None))

    def _maybe_submit_unknown_track(self, ui_track):
        """GT7 never exposes a track ID over telemetry, so the only way to
        spot a track missing from course_ids.csv is comparing the typed
        TRACK field against the known name list. Submits at most once per
        unique name per session -- leaderboard.submit_track_name() is
        fire-and-forget, stdlib-only, never raises."""
        if not ui_track or ui_track in self._submitted_unknown_tracks:
            return
        known = {n.lower() for n in track_db.all_track_names()}
        if ui_track.lower() in known:
            return
        self._submitted_unknown_tracks.add(ui_track)
        threading.Thread(
            target=leaderboard.submit_track_name, args=(ui_track,), daemon=True
        ).start()
        self.log_msg(f"New track name '{ui_track}' -- submitted to community inbox")

    def _run_save(self, fn, background):
        """Run a save under the save lock. background=True moves it off the Tk
        thread (big JSON dumps used to freeze the dashboard for seconds)."""
        def safe():
            try:
                with self._save_lock:
                    return fn()
            except Exception as e:
                self.log_msg(f"SAVE ERROR: {type(e).__name__}: {e}")
                return None
        if not background:
            return safe()
        self._save_threads = [t for t in self._save_threads if t.is_alive()]
        t = threading.Thread(target=safe, daemon=True)
        self._save_threads.append(t)
        t.start()
        return None

    def _save_race(self, samples, background=False, final=True, notify_end=False):
        """final=False (window-close path) leaves no bookkeeping behind when
        the save fails, so a retry after the user keeps the window open can't
        duplicate the race in the summary / export."""
        why = lapclean.junk_reason(samples)
        if why:
            self.log_msg(f"Race not saved -- {why}")
            return _SKIPPED
        ui_track = self.track_var.get().strip()
        if not ui_track and final:
            ui_track = self._ask_track_for_save("race", samples)
        ui_car   = self.car_var.get().strip()
        track    = telem.sanitize(ui_track)
        car      = ui_car or "unknown"
        car_safe = telem.sanitize(car)
        self._maybe_submit_unknown_track(ui_track)

        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = Path(runtime_config.LAPS_FOLDER) / track / "races"

        paused_for = 0.0
        if session.paused and session.paused_at is not None:
            paused_for = max(0.0, time.monotonic() - session.paused_at)
        race_duration = max(0.0, time.monotonic() - session.race_start_t - paused_for)
        gear_ratios = list(telem.get("gear_ratios") or [])

        def _work():
            data = {
                "recorded_at":     ts,
                "track":           track,
                "car":             car,
                "track_display":   ui_track or track,
                "car_display":     ui_car or car,
                "race_duration_s": round(race_duration, 3),
                "physics_era":     leaderboard.PHYSICS_ERA,
                "total_samples":   len(samples),
                # See the matching note in _save_lap -- per-car constant, stored
                # once here instead of on every sample.
                "gear_ratios":     gear_ratios,
                "samples":         samples,
            }
            saved = self._save_json(folder / f"race_{car_safe}_{ts}.json", data, "race")
            if saved is not None:
                self.log_msg(f"Race saved: {self._display_path(saved)}  "
                             f"({len(samples)} samples, {race_duration:.1f}s)")
            else:
                if final:
                    self._unsaved_races.append(data)
                self.log_msg(f"Race NOT saved ({len(samples)} samples) -- it's still in memory; "
                             "use Export Session to write it somewhere else.")
            if saved is not None or final:
                self._append_session_summary(data)
            if notify_end:
                msg = "Recording saved." if saved is not None else \
                      "Recording could NOT be saved -- check the log."
                self._post(lambda: notify.notify("🏁 Race Ended", msg, root=self))
            return saved

        return self._run_save(_work, background)

    def _append_session_summary(self, race_data):
        samples = race_data["samples"]
        if not samples:
            return
        laps       = sorted(set(int(s.get("lap_number", 0)) for s in samples))
        fuel_start = samples[0].get("fuel_remaining", 0)
        fuel_end   = samples[-1].get("fuel_remaining", 0)
        # GT7's position field is the starting-grid slot, -1 once racing --
        # take the first valid one; there is no finishing position to read.
        grid_pos   = next((s.get("current_position") for s in samples
                           if (s.get("current_position") or 0) > 0), None)

        summary = {
            "recorded_at":        race_data["recorded_at"],
            "track":              race_data["track"],
            "car":                race_data["car"],
            "duration_s":         race_data["race_duration_s"],
            "laps":               len(laps),
            # GT7 reports fuel against a capacity of 100 for nearly every car
            # (a percentage), 5 for karts, 0 for EVs.
            "fuel_used":          round(fuel_start - fuel_end, 2),
            "fuel_unit":          "%" if samples[-1].get("fuel_capacity") in (100, 0) else "",
            "grid_position":      grid_pos,
        }
        self._session_summary.append(summary)

        session_path = (Path(runtime_config.LAPS_FOLDER) / "_session"
                        / f"session_{self._session_start_ts}.json")
        self._save_json(session_path, {
            "session_started": self._session_start_ts,
            "races":           self._session_summary,
        }, "session summary", fallback=False)

        self.log_msg(f"Session summary updated ({len(self._session_summary)} race(s) this session)")

    def _show_session_summary(self):
        if not self._session_summary:
            self.log_msg("No races recorded this session yet.")
            return

        dlg = tk.Toplevel(self)
        dlg.title("Session Summary")
        dlg.configure(bg=theme.BG)
        dlg.geometry(f"{theme.px(760)}x{theme.px(320)}")

        cols = ("race", "track", "car", "laps", "duration", "fuel", "pos")
        headers = {"race": "#", "track": "Track", "car": "Car", "laps": "Laps",
                   "duration": "Duration", "fuel": "Fuel Used", "pos": "Grid"}

        st = ttk.Style()
        st.configure("Summary.Treeview",
                     background=theme.CBG, foreground=theme.TXT,
                     fieldbackground=theme.CBG, font=(theme.MONO, 9), rowheight=theme.px(20))
        st.configure("Summary.Treeview.Heading",
                     background=theme.BG1, foreground=theme.RED_2,
                     font=(theme.MONO, 9, "bold"))

        tree = ttk.Treeview(dlg, columns=cols, show="headings", height=10,
                            style="Summary.Treeview")
        for c in cols:
            tree.heading(c, text=headers[c])
            tree.column(c, width=theme.px(85), anchor="center")
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        for idx, s in enumerate(self._session_summary, 1):
            tree.insert("", "end", values=(
                idx, s["track"], s["car"], s["laps"],
                f"{s['duration_s']:.1f}s", f"{s['fuel_used']:.1f} {s['fuel_unit']}",
                f"P{s['grid_position']}" if s.get("grid_position") else "--",
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # Sample building (shared by lap recording and race recording)
    # ─────────────────────────────────────────────────────────────────────────
    def _sample_dict(self, d, start_t, now=None):
        return rec_core.sample_dict(d, start_t, now)

    def _record_sample(self, d, now=None):
        if now is None:
            now = time.monotonic()

        def on_state(state):
            if state == "recording":
                self.rec_btn.config(text=L.STOP_RECORDING, bg=theme.RED_2)
            else:
                self.rec_btn.config(text=L.WAITING_FOR_START_LINE, bg=theme.GOLD)

        def save(samples, lap_time_ms, incomplete):
            if incomplete:
                self._save_lap(samples, lap_time_ms=None, incomplete=True, prompt=False, background=True)
            else:
                self._save_lap(samples, lap_time_ms=lap_time_ms, background=True)

        # The state machine is shared with the terminal Recorder (core/recorder.py), so the two cannot drift.
        # No telem.reset_lap() on a lap change: udp.py restarts track_position itself at the exact packet.
        rec_core.lap_step(session, d, now, self.log_msg, on_state, save)
        if session.recording and not session.waiting:
            step = getattr(self, "_td_step", None)
            if step is not None and session.samples:
                lap_raw = d.get("lap_number")
                try:
                    lap = int(lap_raw) if lap_raw is not None else session.prev_lap
                except (TypeError, ValueError):
                    lap = session.prev_lap
                step(lap)

    # ─────────────────────────────────────────────────────────────────────────
    # Save lap
    # ─────────────────────────────────────────────────────────────────────────
    def _save_json(self, path, data, what, fallback=True):
        """Write a recording to disk; return the path actually written, or
        None. Never raises. Saves used to be bare mkdir/open/json.dump calls
        on the Tk thread: any OSError (disk full, folder not writable) was
        an uncaught exception that lost the recording -- and, raised from
        the record tick, stopped recording for the rest of the session. Now
        it's logged, the write goes through a temp file so a failure can't
        leave a half-written JSON behind, and with fallback=True the file is
        tried again under _FALLBACK_SAVE_DIR before giving up."""
        targets = [path] + ([_FALLBACK_SAVE_DIR / path.name] if fallback else [])
        for target in targets:
            tmp = target.with_name(target.name + ".tmp")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, separators=(",", ":"))
                os.replace(tmp, target)
            except Exception as e:
                self.log_msg(f"SAVE FAILED: couldn't write {what} to {target.parent} ({e})")
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            if target != path:
                self.log_msg(f"{what.capitalize()} saved to the fallback folder instead: {target}")
            return target
        return None

    def _log_time_loss(self, samples, ref_samples):
        """After a lap: name the (up to) three corners that cost the most time
        against the track+car reference lap. Runs inside the save, i.e. off the
        Tk thread for background saves; pandas is imported here, not at startup."""
        try:
            from . import corners
            lap_df = corners.frame_from_samples(samples)
            ref_df = corners.frame_from_samples(ref_samples)
            if lap_df is None or ref_df is None:
                return
            _a, _b, deltas = corners.match_corners(lap_df, ref_df)
            if deltas:
                self.log_msg(f"{corners.describe_time_loss(deltas, 3)}  (vs your reference lap)")
        except Exception:
            pass   # a corner summary is a nicety; it must never break a save

    def _coach_lap(self, samples, ref_samples):
        """After a lap: log the coach's tips against the reference and arm the spoken cues for the next lap. Like
        _log_time_loss it runs inside the save and must never break it."""
        try:
            from . import corners
            lap_df = corners.frame_from_samples(samples)
            ref_df = corners.frame_from_samples(ref_samples)
            if lap_df is None or ref_df is None:
                return
            tips = coach.analyse(lap_df, ref_df)
            self.log_msg(coach.describe(tips))
            self._live_coach.set_tips(tips)    # spoken before those corners on the next lap
        except Exception:
            pass

    def _display_path(self, path):
        """laps/<...> for files under LAPS_FOLDER (as the log always showed
        them), the full path for anything else (e.g. the fallback folder)."""
        try:
            return f"laps/{path.relative_to(Path(runtime_config.LAPS_FOLDER)).as_posix()}"
        except ValueError:
            return str(path)

    @staticmethod
    def _lap_distance(samples):
        return rec_core.lap_distance(samples)

    def _save_lap(self, samples, lap_time_ms=None, incomplete=False, prompt=True,
                  background=False, final=True):
        """prompt=False saves an incomplete lap straight to disk instead of
        asking Save/Discard (window-close path). background=True does the
        file work on a worker thread. final=False: on failure leave no
        in-memory bookkeeping, so a retry can't duplicate the lap."""
        _gt7 = bool(lap_time_ms and lap_time_ms > 0)
        _secs = (lap_time_ms / 1000.0) if _gt7 else max(0.0, time.monotonic() - session.start_t)
        why = lapclean.junk_reason(samples, _secs, self._lap_distance(samples) if len(samples) > 1 else 0.0)
        if why:
            self.log_msg(f"Lap not saved -- {why}")
            return _SKIPPED
        _sig = lapclean.samples_signature("", "", _secs, samples)
        if _sig is not None and _sig == self._last_lap_sig:
            self.log_msg("Lap not saved -- identical to the lap just saved (duplicate)")
            return _SKIPPED
        ui_track = self.track_var.get().strip()
        if not ui_track and prompt and final:
            ui_track = self._ask_track_for_save("lap", samples)
        ui_car   = self.car_var.get().strip()
        track    = telem.sanitize(ui_track)
        car      = ui_car or "unknown"
        car_safe = telem.sanitize(car)
        self._maybe_submit_unknown_track(ui_track)

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        lap_path = Path(runtime_config.LAPS_FOLDER) / track / f"{car_safe}_{ts}.json"
        ref      = self._reference_path(track, car_safe)

        gt7_time    = bool(lap_time_ms and lap_time_ms > 0)
        new_time    = (lap_time_ms / 1000.0) if gt7_time \
                      else max(0.0, time.monotonic() - session.start_t)
        time_source = "GT7" if gt7_time else "elapsed"

        self.log_msg(
            f"Lap time: {ms_to_laptime(int(new_time * 1000))}  "
            f"({time_source}, {len(samples)} samples"
            f"{'  INCOMPLETE' if incomplete else ''})"
        )

        lap_num = int(samples[-1].get("lap_number", 0)) if samples else 0
        gear_ratios = list(telem.get("gear_ratios") or [])
        fuel_used = (samples[0].get("fuel_remaining", 0) -
                     samples[-1].get("fuel_remaining", 0)) if samples else 0.0
        if not incomplete and fuel_used > 0:
            self._fuel_per_lap = fuel_used

        def _work():
            data = {
                "recorded_at":    ts,
                "track":          track,
                "car":            car,
                "track_display":  ui_track or track,
                "car_display":    ui_car or car,
                "lap_time_s":     round(new_time, 3),
                "lap_distance_m": round(self._lap_distance(samples), 1),
                "total_samples":  len(samples),
                "incomplete":     incomplete,
                "physics_era":    leaderboard.PHYSICS_ERA,
                # Per-car constant, so it lives here rather than on every sample.
                "gear_ratios":    gear_ratios,
                "tyre_radius_m":  round(float(telem.get("tyre_radius_m") or 0.0), 4),
                "samples":        samples,
            }
            # Only a lap timed by GT7 itself can be a PB / reference: the
            # elapsed-time fallback is approximate.
            is_pb = (not incomplete and gt7_time and
                     self._is_new_personal_best(track, car_safe, new_time))
            saved = self._save_json(lap_path, data, "lap")
            # Samples are NOT kept in memory once on disk (a long session held
            # every lap's full sample list); Export Session re-reads the files.
            meta = {k: v for k, v in data.items() if k != "samples"}
            meta["fuel_used"] = round(max(0.0, fuel_used), 2)
            if saved is None:
                self.log_msg("Lap NOT saved -- it's still in memory; use Export Session "
                             "to write it somewhere else.")
                if final:
                    self._unsaved_laps.append(data)
                    session.laps_saved.append(meta)
                return None
            meta["file"] = str(saved)
            session.laps_saved.append(meta)
            self._last_lap_sig = _sig
            self._td_learn(ui_track, samples, incomplete)
            self.log_msg(f"Saved: {self._display_path(saved)}")
            self._post(self._add_lap_to_history, lap_num, new_time, track, car, incomplete, is_pb)
            if incomplete or saved != lap_path:
                # Incomplete, or the laps folder isn't writable (fallback used):
                # no PB / reference.
                return saved

            if is_pb:
                self._write_personal_best(track, car_safe, ui_car or car, new_time, ts)
                self.log_msg(f"New personal best for {ui_car or car} @ {track}!")
                m = int(new_time // 60); s = new_time % 60
                self._post(lambda: notify.notify(
                    "🏆 New Personal Best", f"{ui_car or car} @ {track}\n{m}:{s:06.3f}", root=self))

            if not gt7_time:
                return saved
            ref_time = 0.0
            ref_samples = None
            if ref.exists():
                try:
                    with open(ref, encoding="utf-8-sig") as f:
                        ref_data = json.load(f)
                    # A reference from before the current physics era is
                    # replaced by the first lap driven under the new physics.
                    if leaderboard.is_current_era(ref_data):
                        ref_time = float(ref_data.get("lap_time_s") or 0)
                        ref_samples = ref_data.get("samples")
                except Exception:
                    ref_time = 0.0

            if ref_samples:
                self._log_time_loss(samples, ref_samples)
                self._coach_lap(samples, ref_samples)

            if (ref_time <= 0 or new_time < ref_time) and \
                    self._save_json(ref, data, "reference lap", fallback=False):
                diff_str = (f"  (new best by {ref_time - new_time:.3f}s)"
                            if ref_time > 0 else "  (first lap)")
                self.log_msg(f"Reference updated -> {ref.name}{diff_str}")
                # Invalidate the live-delta cache so the new best is picked up.
                self._delta_ref_cache["key"] = None
            return saved

        if incomplete and not prompt:
            return self._run_save(_work, background)

        if incomplete:
            dlg = tk.Toplevel(self)
            dlg.title("Incomplete Lap")
            dlg.configure(bg=theme.BG)
            dlg.resizable(False, False)
            tk.Label(dlg,
                     text=f"Incomplete lap ({new_time:.1f}s, {len(samples)} samples) -- save or discard?",
                     fg=theme.GOLD, bg=theme.BG, font=(theme.MONO, 11),
                     pady=12, padx=16).pack()
            bf = tk.Frame(dlg, bg=theme.BG)
            bf.pack(pady=(0, 12))
            tk.Button(bf, text="Save", bg=theme.GOLD, fg="#000",
                      font=(theme.MONO, 10, "bold"), relief="flat", padx=10,
                      command=lambda: [self._run_save(_work, True), dlg.destroy()]
                      ).pack(side="left", padx=8)
            tk.Button(bf, text="Discard", bg=theme.BG3, fg=theme.MID,
                      font=(theme.MONO, 10), relief="flat", padx=10,
                      command=dlg.destroy).pack(side="left", padx=8)
            return None

        return self._run_save(_work, background)

    # ─────────────────────────────────────────────────────────────────────────
    # Personal bests (per track+car combo; only the lap time -- the full
    # reference lap for the live delta lives in reference_<car>.json)
    # ─────────────────────────────────────────────────────────────────────────
    def _personal_bests_path(self, track):
        return rec_core.pb_path(runtime_config.LAPS_FOLDER, track)

    def _load_personal_bests(self, track):
        return rec_core.load_pbs(runtime_config.LAPS_FOLDER, track)

    def _is_new_personal_best(self, track, car_safe, new_time):
        return rec_core.is_new_pb(runtime_config.LAPS_FOLDER, track, car_safe, new_time)

    def _write_personal_best(self, track, car_safe, car_display, new_time, ts):
        rec_core.write_pb(runtime_config.LAPS_FOLDER, track, car_safe, car_display, new_time, ts, self.log_msg)

    # ─────────────────────────────────────────────────────────────────────────
    # Lap history row
    # ─────────────────────────────────────────────────────────────────────────
    def _add_lap_to_history(self, lap_num, lap_time_s, track, car, incomplete=False, is_pb=False):
        self._saved_laps += 1
        time_str = ms_to_laptime(int(lap_time_s * 1000))
        if incomplete:
            time_str = f"({time_str})"
        if is_pb:
            time_str = _tk_text(f"\U0001F3C6{time_str}")
        track_d = track[:9]
        car_d   = car[:9]
        display_num = lap_num if lap_num > 0 else self._saved_laps
        row_id = self.hist_tree.insert(
            "", 0, values=(display_num, time_str, track_d, car_d))
        self.hist_tree.tag_configure("newest", foreground=theme.TEAL_2)
        # Gold "pb" tag takes over from "newest" for a PB row -- and, unlike
        # "newest" (cleared below as soon as the next lap comes in), sticks
        # around so the badge stays visible for the rest of the session.
        self.hist_tree.tag_configure("pb", foreground=theme.GOLD)
        self.hist_tree.item(row_id, tags=("pb",) if is_pb else ("newest",))
        children = self.hist_tree.get_children()
        if len(children) > 1:
            prev_tags = self.hist_tree.item(children[1], "tags")
            if "pb" not in prev_tags:
                self.hist_tree.item(children[1], tags=())

    # ─────────────────────────────────────────────────────────────────────────
    # Export session
    # ─────────────────────────────────────────────────────────────────────────
    def _export_session(self):
        if not session.laps_saved and not self._unsaved_races and not self._unsaved_laps:
            self.log_msg("No laps recorded this session.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"gt7_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        if not path:
            return
        laps = []
        for d in list(session.laps_saved):
            f = d.get("file")
            if f:
                try:
                    with open(f, encoding="utf-8-sig") as fh:
                        laps.append(json.load(fh))
                    continue
                except Exception:
                    pass
            laps.append(d)   # metadata only (file moved/unreadable, or unsaved)
        payload = {"exported_at": datetime.now().isoformat(), "laps": laps}
        if self._unsaved_laps:
            payload["unsaved_laps"] = list(self._unsaved_laps)
        if self._unsaved_races:
            payload["unsaved_races"] = list(self._unsaved_races)
        tmp = str(path) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
            os.replace(tmp, path)
        except Exception as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            messagebox.showerror("Export failed", f"Couldn't write {path}:\n{e}", parent=self)
            return
        self.log_msg(f"Session exported: {Path(path).name}")

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    theme.enable_dpi_awareness()
    app = App()
    app.mainloop()
