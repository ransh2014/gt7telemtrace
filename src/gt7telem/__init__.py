"""
gt7telem (TRACE) -- GT7 telemetry capture and lap/race analysis toolkit.

Public data API (re-exported here for convenience):
    get_snapshot, get, get_int, get_float
    set_ip, set_car, set_track, is_connected, wait_for_connection
    get_diagnostics, get_last_error, get_incidents, register_event, reset_lap
    get_car_name, get_track_name, all_track_names
    load, save, remember_good_ip (settings)

GUI apps (Live Dashboard, Lap Analyst, Race Analyst) are not re-exported
here -- run `gt7telem` from the command line, or import the submodules
directly: gt7telem.dashboard, gt7telem.lap_analyst, gt7telem.race_analyst.
"""
__version__ = "0.1.5"

from .cars import get_car_name
from .config import load, remember_good_ip, save
from .tracks import all_track_names, get_track_name
from .udp import (
    get,
    get_diagnostics,
    get_float,
    get_incidents,
    get_int,
    get_last_error,
    get_snapshot,
    is_connected,
    register_event,
    reset_lap,
    set_car,
    set_ip,
    set_track,
    wait_for_connection,
)

__all__ = [
    "get_snapshot", "get", "get_int", "get_float",
    "set_ip", "set_car", "set_track", "is_connected", "wait_for_connection",
    "get_diagnostics", "get_last_error", "get_incidents", "register_event", "reset_lap",
    "get_car_name", "get_track_name", "all_track_names",
    "load", "save", "remember_good_ip",
]
