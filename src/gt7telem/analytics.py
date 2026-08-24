"""
analytics.py -- anonymous usage ping. On by default; see config.ANALYTICS_ENABLED
to opt out (Settings, or edit settings.json directly).

Sends exactly five fields on tool launch, nothing else:
    event, tool, version, os, created_at

No PII, no telemetry content (speed/inputs/lap times/car/track), no IP stored
on our end. Full disclosure: https://gt7trace.netlify.app/privacy.html

This module never raises and never blocks the caller -- the actual HTTP call
runs in a background daemon thread with a short timeout, and any failure
(offline, DNS, Supabase down, whatever) is swallowed silently. Analytics must
never be able to crash or slow down the app.
"""
import json
import platform
import threading
import urllib.request
from datetime import datetime, timezone

from . import config

__all__ = ["track_launch"]

# Public anon key -- safe to ship in source. Row Level Security on the
# app_events table only permits INSERT from this key, never SELECT/UPDATE/
# DELETE, so this key can't be used to read or tamper with anyone's data.
_SUPABASE_URL = "https://hignsvyojdqsjoidgkud.supabase.co"
_SUPABASE_ANON_KEY = "sb_publishable_OdzGvcypa0GI7TVxxUkusQ_KJ_Apa8i"
_ENDPOINT = f"{_SUPABASE_URL}/rest/v1/app_events"

_OS_NAMES = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}


def _get_version() -> str:
    try:
        from . import __version__
        return __version__
    except Exception:
        return "unknown"


def _os_name() -> str:
    return _OS_NAMES.get(platform.system(), platform.system().lower())


def _send(payload: dict) -> None:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _ENDPOINT,
            data=data,
            method="POST",
            headers={
                "apikey": _SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {_SUPABASE_ANON_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )
        urllib.request.urlopen(req, timeout=4)
    except Exception:
        pass  # analytics must never surface an error to the user


def track_launch(tool: str) -> None:
    """Fire-and-forget usage ping. Call once, right after a tool (dashboard /
    lap_analyst / race_analyst) finishes starting up. Instantly a no-op if
    the user has ANALYTICS_ENABLED off -- no thread even gets spawned."""
    if not config.ANALYTICS_ENABLED:
        return
    payload = {
        "event": "app_launch",
        "tool": tool,
        "version": _get_version(),
        "os": _os_name(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    threading.Thread(target=_send, args=(payload,), daemon=True).start()
