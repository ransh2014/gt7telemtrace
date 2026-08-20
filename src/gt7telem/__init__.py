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
__version__ = "0.1.0"

from .udp import (
    get_snapshot, get, get_int, get_float,
    set_ip, set_car, set_track, is_connected, wait_for_connection,
    get_diagnostics, get_last_error, get_incidents, register_event, reset_lap,
)
from .cars import get_car_name
from .tracks import get_track_name, all_track_names
from .config import load, save, remember_good_ip

__all__ = [
    "get_snapshot", "get", "get_int", "get_float",
    "set_ip", "set_car", "set_track", "is_connected", "wait_for_connection",
    "get_diagnostics", "get_last_error", "get_incidents", "register_event", "reset_lap",
    "get_car_name", "get_track_name", "all_track_names",
    "load", "save", "remember_good_ip",
]