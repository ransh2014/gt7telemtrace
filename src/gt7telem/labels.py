"""labels.py -- every piece of UI wording the desktop and the Android app share, in ONE place.

The desktop windows import these constants instead of repeating string literals, and the Android app keeps the
very same text in ``android/app/src/main/res/values/strings.xml`` (resource name = the constant's name in lower
case). ``tests/test_android_parity.py`` fails when a constant here is missing from, or differs in, strings.xml,
or when the Android code stops using it -- so a desktop label change breaks the build until Android matches.

Placeholders are written ``{name}`` here; on Android they become ``%1$s``, ``%2$s`` ... in order of appearance.
Never imports tkinter (headless-safe).
"""
from __future__ import annotations

# ── Window / header pattern ───────────────────────────────────────────────────
WINDOW_TITLE_FMT = "TRACE {version} - {tool}"
HEADER_BRAND = "GT7 TRACE"
MENU_BUTTON = "← Menu"

TOOL_LIVE = "Live Dashboard"
TOOL_LAP = "Lap Analyst"
TOOL_RACE = "Race Analyst"
TITLE_LIVE = "LIVE DASHBOARD"
TITLE_LAP = "LAP ANALYST"
TITLE_RACE = "RACE ANALYST"

# ── Launcher ─────────────────────────────────────────────────────────────────
LAUNCHER_TITLE = "TRACE"
LAUNCHER_SUBTITLE = "GT7 Telemetry Suite"
LAUNCHER_WINDOW_TOOL = "GT7 Telemetry Suite"
SIGNED_IN_AS_FMT = "signed in as {name}"
CARD_LIVE_DESC = "Real-time telemetry overlay while you drive."
CARD_LAP_DESC = "Compare laps, chart every input, submit to the leaderboard."
CARD_RACE_DESC = "Full race breakdown and pace review."
LAUNCH = "Launch  →"
FOOTER_NOTE = "community tool - not affiliated with Polyphony Digital"
LOG_OUT = "Log Out"
CREATE_ACCOUNT = "Create Free Account"
BACKUP_LAPS = "\U0001F5C4  Backup Laps"
RECORDS = "\U0001F3C1  Records"
THEME_LIGHT = "☀  Light"
THEME_DARK = "\U0001F319  Dark"

# update status (launcher)
VERSION_FMT = "v{version}"
UPDATE_CURRENT = "current"
UPDATE_AVAILABLE_FMT = "update available: v{version}"
IGNORE_UPDATE_CHECKS = "Ignore update checks"

# ── Live Dashboard ───────────────────────────────────────────────────────────
STATUS_ONLINE = "● LIVE"
STATUS_OFFLINE = "● OFFLINE"
RECORD_RACE = "Record Race"
STOP_RACE_RECORDING = "Stop Race Recording"
RECORD_LAP = "Record Lap"
STOP_RECORDING = "Stop Recording"
WAITING_FOR_START_LINE = "Waiting for start line..."
SESSION_SUMMARY = "Session Summary"
EXPORT_SESSION = "Export Session"

CONSOLE_IP = "CONSOLE IP"
AUTO_DETECT = "Auto-Detect"
TRACK = "TRACK"
CAR = "CAR"
REC_RATE = "REC RATE"
METRICS_FMT = "METRICS :{port}"
ALLOW_REMOTE_METRICS = "ALLOW REMOTE (METRICS)"
WEBUI_FMT = "WEB UI :{port}"
ALLOW_REMOTE_WEBUI = "ALLOW REMOTE (WEB UI)"

PREFERENCES = "PREFERENCES"
# in the order they appear on screen, left to right
PREF_KEEP_AWAKE = "KEEP AWAKE"
PREF_AUTO_LAP = "AUTO LAP"
PREF_VOICE_ALERTS = "VOICE ALERTS"
PREF_NOTIFICATIONS = "NOTIFICATIONS"
PREF_TOP_LAP_GHOST = "TOP-LAP GHOST"
PREF_CHECK_FOR_UPDATES = "CHECK FOR UPDATES"
PREF_SHARE_USAGE_DATA = "SHARE USAGE DATA"
PREF_DEBUG_LOG = "DEBUG LOG"

SPEED_UNIT = "km/h"
SUGGEST = "SUGGEST"
GEAR = "gear"
STAT_LAP = "LAP"
STAT_CURRENT = "CURRENT"
STAT_BEST = "BEST"
STAT_LAST = "LAST"
STAT_GRID = "GRID"
STAT_TOP_SPD = "TOP SPD"
STAT_DELTA = "DELTA"
STAT_VS_TOP = "VS TOP"

BAR_RPM = "RPM"
BAR_THROTTLE = "THROTTLE"
BAR_BRAKE = "BRAKE"
BAR_CLUTCH = "CLUTCH"

FRAME_TYRES = "TYRES"
FRAME_SUSPENSION = "SUSPENSION (mm)"
FRAME_DRIVER_AIDS = "DRIVER AIDS"
FRAME_ENGINE = "ENGINE AND FLUIDS"
FRAME_STRATEGY = "STRATEGY (est.)"
FRAME_DYNAMICS = "DYNAMICS"
FRAME_POSITION = "POSITION"
FRAME_DRIVETRAIN = "DRIVETRAIN EXTRAS"
FRAME_EXTENDED = "EXTENDED (B/~/C)"
FRAME_SURFACE = "SURFACE (per tyre)"
FRAME_STEERING = "WHEEL STEERING ANGLE"
FRAME_MISC = "SESSION / MISC"
GEAR_RATIOS = "GEAR RATIOS:"
NEW_TYRES = "[ NEW TYRES ]"
ALERT_TYRE_HOT = "TYRE HOT"
ALERT_TYRE_COLD = "TYRE COLD"
ALERT_FUEL_LOW = "FUEL LOW"
TRACK_MAP = "TRACK MAP"
WAITING_FOR_DATA = "WAITING FOR DATA"
LAP_HISTORY = "LAP HISTORY"
LOG = "LOG"
HIST_NUM = "#"
HIST_TIME = "Time"
HIST_TRACK = "Track"
HIST_CAR = "Car"

# ── Analysts ─────────────────────────────────────────────────────────────────
TAB_REPLAY = "▶  Replay"
TAB_CHARTS = "\U0001F4CA  Charts"
LAP_A = "LAP A"
LAP_B = "LAP B"
RACE_A = "RACE A"
RACE_B = "RACE B"
NOT_LOADED = "not loaded"
BROWSE = "\U0001F4C2  Browse"
OVERLAY_SECTION = "OVERLAY (3+ LAPS)"
ADD_OVERLAY_LAP = "➕  Add Overlay Lap"
EXPORT_SECTION = "EXPORT"
EXPORT_CSV = "\U0001F4BE  Export CSV"
EXPORT_CHART_HTML = "\U0001F310  Export Chart HTML"
EXPORT_LAP_CARD = "\U0001F5BC  Export Lap Card (PNG)"
EXPORT_RACE_CARD = "\U0001F5BC  Export Race Card (PNG)"
BACKUP_ALL_LAPS = "\U0001F5C4  Backup All Laps"
FIND_DUPLICATE_LAPS = "\U0001F9F9  Find Duplicate Laps"
LEADERBOARD_SECTION = "LEADERBOARD"
SUBMIT_TO_LEADERBOARD = "\U0001F3C6  Submit to Leaderboard"
LOAD_COMMUNITY_LINE = "\U0001F310  Load Community Line"
TOP10_PLACEHOLDER = "load a lap to see the top 10"
SECTORS_LAP_A = "SECTORS — LAP A"
STATS_LAP_A = "STATS — LAP A"
LAP_NOTES = "LAP NOTES"
SAVE_NOTES = "\U0001F4BE  Save Notes"
LAP_SPLITS_RACE_A = "LAP SPLITS — RACE A"
STATS_RACE_A = "STATS — RACE A"
RACE_NOTES = "RACE NOTES"
CHARTS = "CHARTS"
COMPARE_OFF = "⚡ Compare: OFF"
COMPARE_ON = "⚡ Compare: ON"
CORNER_BASELINE_BEST = "Corner baseline: best lap in race"
CORNER_BASELINE_PB = "Corner baseline: saved PB lap"
PLAY = "▶  Play"
PAUSE = "⏸  Pause"
RESTART = "⏮"
SPEED_LABEL = "Speed:"
SPEED_FMT = "{speed}×"
MODE_SYNCED = "🔀 Synced"
MODE_REALTIME = "⏱ Real-Time"
HEATMAP = "🌡 Heatmap"
HEATMAP_ON = "🌡 Heatmap ON"
SAVE_BOUNDARY = "💾 Save Boundary"
REPLAY_EXPORT_CSV = "📄 Export CSV"
READ_TIME = "Time"
READ_SPEED = "Speed"
READ_GEAR = "Gear"
READ_THROTTLE = "Throttle"
READ_BRAKE = "Brake"
READ_RPM = "RPM"
READ_LAP_PCT = "Lap %"
READ_LAP = "Lap"
READ_RACE_PCT = "Race %"

LAP_CHART_TABS = [
    "Inputs", "Engine", "Tyres", "Dynamics", "Maps", "G-Force", "Fuel", "Braking", "Sectors", "Traction",
    "Tele Diff", "Ratings", "Heat Maps", "Timeline", "Extended", "Consensus", "Style", "Corners",
    "Theoretical", "Dyno & Tune",
]
RACE_CHART_TABS = [
    "Race", "Inputs", "Engine", "Tyres", "Dynamics", "Maps", "G-Force", "Fuel", "Braking", "Laps",
    "Traction", "Tele Diff", "Ratings", "Heat Maps", "Timeline", "Corners", "Consistency",
]


def shared() -> dict[str, str]:
    """name -> text for every plain-string constant (what strings.xml must carry)."""
    return {k: v for k, v in globals().items() if k.isupper() and isinstance(v, str)}


def window_title(tool: str, version: str) -> str:
    return WINDOW_TITLE_FMT.format(version=version, tool=tool)
