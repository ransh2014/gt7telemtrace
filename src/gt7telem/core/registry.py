"""The single registry both front ends build from. Add a chart group, tool or screen here (or in the chart modules'
GROUPS lists, which this reads) and it appears in the Tk windows and the terminal UI; the parity tests fail if a
front end forgets it."""

from __future__ import annotations

from .. import labels as L
from .. import lap_charts, race_charts

# (kind, label, colour key used by the Tk sidebar). Wording lives here once; Tk and the terminal both read it.
LAP_TOOLS = [
    ("coach", L.TOOL_COACH_AB, "GRN"),
    ("setup", L.TOOL_SETUP_ADVISOR, "CYN"),
    ("dna", L.TOOL_DRIVER_DNA, "PRP"),
    ("plan", L.TOOL_PRACTICE_PLAN_ALL, "YLW"),
    ("validate", L.TOOL_VALIDATE_LAP, "ACC"),
    ("dyno", L.TOOL_COMPARE_DYNO, "CYN"),
]
RACE_TOOLS = [
    ("coach", L.RACE_TOOL_COACH, "GRN"),
    ("plan", L.RACE_TOOL_PLAN, "GRN"),
    ("dna", L.RACE_TOOL_DNA, "GRN"),
    ("validate", L.RACE_TOOL_VALIDATE, "GRN"),
]
REPLAY_EXPORT_LABEL = L.TOOL_EXPORT_REPLAY3D

MOTEC_EXPORT_LABEL = "Export to MoTeC (.ld)"   # desktop and terminal UI only; the Android app has no MoTeC export

VIDEO_EXPORT_LABEL = "Export video overlay"    # desktop and terminal UI only; the Android app has no video export

# (kind, label) for the actions on the analysts' sidebars. Wording comes from labels.py where the Tk windows already
# had it, so a label can't read differently in the two front ends.
LAP_ACTIONS = [
    ("csv", L.EXPORT_CSV), ("html", L.EXPORT_CHART_HTML), ("card", L.EXPORT_LAP_CARD),
    ("motec", MOTEC_EXPORT_LABEL), ("video", VIDEO_EXPORT_LABEL), ("replay3d", REPLAY_EXPORT_LABEL), ("replay", "Replay (scrubber)"),
    ("backup", L.BACKUP_ALL_LAPS), ("dups", L.FIND_DUPLICATE_LAPS),
    ("community", L.LOAD_COMMUNITY_LINE), ("submit", L.SUBMIT_TO_LEADERBOARD),
    ("share", L.SHARE_LAP_LINK), ("unshare", L.SHARE_DELETE),
]
RACE_ACTIONS = [("csv", L.EXPORT_CSV), ("html", L.EXPORT_CHART_HTML), ("card", L.EXPORT_RACE_CARD),
                ("backup", L.BACKUP_ALL_LAPS)]
ADD_OVERLAY_LABEL = L.ADD_OVERLAY_LAP


def overlay_colors():
    return [lap_charts.YLW, lap_charts.GRN, lap_charts.PRP, lap_charts.ORG, "#96949c", "#f2f0ec"]


SCREENS = ["live", "laps", "race", "records", "settings"]

# (config key, label) for the on/off preferences both front ends show. Wording comes from labels.py.
SETTINGS = [
    ("VOICE_ENABLED", L.PREF_VOICE_ALERTS),
    ("NOTIFY_ENABLED", L.PREF_NOTIFICATIONS),
    ("GHOST_ENABLED", L.PREF_TOP_LAP_GHOST),
    ("UPDATE_CHECK_ENABLED", L.PREF_CHECK_FOR_UPDATES),
    ("ANALYTICS_ENABLED", L.PREF_SHARE_USAGE_DATA),
    ("DEBUG_LOG", L.PREF_DEBUG_LOG),
    ("AUTO_LAP_ENABLED", L.PREF_AUTO_LAP),
    ("KEEP_AWAKE_ENABLED", L.PREF_KEEP_AWAKE),
    ("LLM_ENABLED", L.PREF_LOCAL_AI),
]


def lap_groups() -> list[str]:
    return [g[0] for g in lap_charts.GROUPS]


def race_groups() -> list[str]:
    return [g[0] for g in race_charts.GROUPS]


def lap_group(name):
    """(draw function, figsize) for a Lap Analyst chart group."""
    for n, fn, _shape, size in lap_charts.GROUPS:
        if n == name:
            return fn, size
    raise KeyError(name)


def race_group(name):
    for n, fn, _shape, size in race_charts.GROUPS:
        if n == name:
            return fn, size
    raise KeyError(name)
