"""The single registry both front ends build from. Add a chart group, tool or screen here (or in the chart modules'
GROUPS lists, which this reads) and it appears in the Tk windows and the terminal UI; the parity tests fail if a
front end forgets it."""

from __future__ import annotations

from .. import labels as L
from .. import lap_charts, race_charts

# (kind, label, colour key used by the Tk sidebar). Wording lives here once; Tk and the terminal both read it.
LAP_TOOLS = [
    ("coach", "Coach (A vs B)", "GRN"),
    ("setup", "Setup advisor", "CYN"),
    ("dna", "Driver DNA", "PRP"),
    ("plan", "Practice plan (all loaded laps)", "YLW"),
    ("validate", "Validate lap", "ACC"),
    ("dyno", "Compare dyno (A vs B)", "CYN"),
]
RACE_TOOLS = [
    ("coach", "Coach (slowest vs best lap)", "GRN"),
    ("plan", "Practice plan", "GRN"),
    ("dna", "Driver DNA", "GRN"),
    ("validate", "Validate laps", "GRN"),
]
REPLAY_EXPORT_LABEL = "Export 3D replay (HTML)"

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
