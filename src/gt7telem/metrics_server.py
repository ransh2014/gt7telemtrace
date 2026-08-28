"""metrics_server.py -- optional Prometheus metrics endpoint for the GT7
Live Dashboard.

Off by default (see config.METRICS_ENABLED / METRICS_PORT). When enabled,
exposes gauges for speed/rpm/throttle/brake/fuel/lap-time on a local HTTP
server, updated from the same per-frame snapshot dict dashboard.py's
_poll() loop already reads from telem.get_snapshot().

Testable without a PS5: start(), then call update() with frames replayed
from any saved lap file's "samples" list (see lap_analyst.load_lap()),
and curl localhost:<port>/metrics to see the gauges move.
"""
import threading

from prometheus_client import Gauge, start_http_server

__all__ = ["start", "stop", "update", "is_running", "DEFAULT_PORT"]

DEFAULT_PORT = 9109

_lock = threading.Lock()
_httpd = None
_thread = None
_started = False

_GAUGES = {
    "speed_kmh":      Gauge("gt7_speed_kmh", "Vehicle speed, km/h"),
    "rpm":            Gauge("gt7_rpm", "Engine RPM"),
    "throttle":       Gauge("gt7_throttle", "Throttle input, 0-1"),
    "brake":          Gauge("gt7_brake", "Brake input, 0-1"),
    "fuel_remaining": Gauge("gt7_fuel_remaining", "Fuel remaining (percent or litres, depending on car)"),
    "current_lap_ms": Gauge("gt7_current_lap_ms", "Current in-progress lap time, milliseconds"),
}


def is_running() -> bool:
    return _started


def start(port: int = DEFAULT_PORT) -> bool:
    """Start the metrics HTTP server on `port`, if not already running.
    Safe to call more than once -- a no-op after the first successful
    call. Returns True if the server is running afterward, False if it
    failed to bind (e.g. port already in use)."""
    global _httpd, _thread, _started
    with _lock:
        if _started:
            return True
        try:
            _httpd, _thread = start_http_server(port)
        except OSError:
            return False
        _started = True
        return True


def stop() -> None:
    """Stop the metrics HTTP server and reset the gauges to 0, so
    toggling the setting off mid-session actually frees the port
    instead of leaving stale values being served."""
    global _httpd, _thread, _started
    with _lock:
        if _httpd is not None:
            _httpd.shutdown()
            _httpd.server_close()
        _httpd = None
        _thread = None
        _started = False
        for gauge in _GAUGES.values():
            gauge.set(0)


def update(d: dict) -> None:
    """Push one frame's telemetry snapshot (the same dict shape
    dashboard.py's _poll() gets from telem.get_snapshot(), or a sample
    dict from a saved lap file) into the gauges. No-op if start() hasn't
    been called (or has since been stopped) -- so call sites don't need
    to check is_running() themselves before every call."""
    if not _started:
        return
    for key, gauge in _GAUGES.items():
        try:
            gauge.set(float(d.get(key) or 0))
        except (TypeError, ValueError):
            pass
