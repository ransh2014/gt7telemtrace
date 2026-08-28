"""
runtime_config.py — replaces config.py for the standalone .exe builds.

No manual editing needed. On first run it uses sensible defaults; whenever
you change the PS4/PS5 IP in the app, it's saved to settings.json right
next to the .exe (or script) so it's remembered next time.
"""
import json
import sys
from pathlib import Path

__all__ = ["load", "save", "remember_good_ip", "PS_IP", "LAPS_FOLDER", "KNOWN_IPS", "ANALYTICS_ENABLED", "PSN_NAME",
           "SUPABASE_ACCESS_TOKEN", "SUPABASE_REFRESH_TOKEN", "SUPABASE_USER_ID", "ONBOARDING_DONE",
           "METRICS_ENABLED", "METRICS_PORT"]


def _base_dir() -> Path:
    """Where settings.json lives. Frozen (.exe/.app) builds keep it next to
    the executable -- a deliberate portable-app design, untouched here.
    Non-frozen (pip/source) runs used to resolve this to the package's own
    install directory (Path(__file__).parent), which for a pip install
    means inside site-packages: not reliably writable, and wiped on every
    `pip install --upgrade` -- silently, since save() swallows write
    failures. A per-user config dir survives upgrades and reinstalls."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    d = Path.home() / ".gt7telem"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_laps_dir() -> Path:
    """Frozen builds keep laps next to the exe, as before. Non-frozen runs
    get a visible ~/TRACE/laps instead of nesting inside the hidden
    ~/.gt7telem config dir, which would be a surprising place to go looking
    for recorded laps."""
    if getattr(sys, "frozen", False):
        return _base_dir() / "laps"
    return Path.home() / "TRACE" / "laps"


_SETTINGS_FILE = _base_dir() / "settings.json"

_DEFAULTS = {
    "PS_IP": "192.168.1.1",
    "LAPS_FOLDER": str(_default_laps_dir()),
    # Recording sample rate in Hz for Record Lap / Record Race (see
    # gt7telem.py's RECORD_RATE_OPTIONS). 10 is the safe default -- it's
    # the one rate we know for certain the app can sustain end-to-end.
    "SAMPLE_RATE": 10,
    "KNOWN_IPS": [],   # IPs that have successfully connected before, most-recent-first
    "DEBUG_LOG": False,  # show raw [DEBUG] state-change dumps in the log panel
    # Anonymous usage analytics -- on by default. Sends only: which tool was
    # launched, TRACE version, OS, and a timestamp. No telemetry content, no
    # PSN name, no IP stored on our end. See gt7trace.netlify.app/privacy.html
    # for the full disclosure. Turn off here or in Settings.
    "ANALYTICS_ENABLED": True,
    # Remembered PSN name for leaderboard submissions -- pre-fills the
    # submit dialog each time, editable inline there if you want to change it.
    "PSN_NAME": "",
    # Supabase anonymous-auth session (see auth.py) -- required by the
    # `laps` table's INSERT RLS policy (auth.uid() = user_id) to submit a
    # lap. Empty until the user completes onboarding or signs up later.
    "SUPABASE_ACCESS_TOKEN": "",
    "SUPABASE_REFRESH_TOKEN": "",
    "SUPABASE_USER_ID": "",
    # Set True the first time the onboarding screen is shown (whether the
    # user creates an account or skips) so launcher.py never shows it again.
    "ONBOARDING_DONE": False,
    # Prometheus/Grafana metrics export (see metrics_server.py) -- off by
    # default. When on, the Live Dashboard exposes speed/rpm/throttle/
    # brake/fuel/lap-time gauges on METRICS_PORT for Grafana or any other
    # Prometheus-compatible scraper to pull from.
    "METRICS_ENABLED": False,
    "METRICS_PORT": 9109,
}

MAX_KNOWN_IPS = 3


def remember_good_ip(ip: str) -> list:
    """Push `ip` to the front of the known-good IP list (dedup, capped at
    MAX_KNOWN_IPS), persist it, and return the updated list. Only call this
    once a connection has actually been confirmed -- not on every keystroke."""
    ip = (ip or "").strip()
    if not ip:
        return load().get("KNOWN_IPS", [])
    data  = load()
    known = [x for x in data.get("KNOWN_IPS", []) if x != ip]
    known.insert(0, ip)
    known = known[:MAX_KNOWN_IPS]
    save(KNOWN_IPS=known)
    return known


def load() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            merged = dict(_DEFAULTS)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(_DEFAULTS)


def save(**kwargs) -> None:
    data = load()
    data.update(kwargs)
    try:
        _SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


_cfg = load()
PS_IP = _cfg["PS_IP"]
LAPS_FOLDER = _cfg["LAPS_FOLDER"]
SAMPLE_RATE = _cfg["SAMPLE_RATE"]
KNOWN_IPS = _cfg["KNOWN_IPS"]
DEBUG_LOG = _cfg["DEBUG_LOG"]
ANALYTICS_ENABLED = _cfg["ANALYTICS_ENABLED"]
PSN_NAME = _cfg["PSN_NAME"]
SUPABASE_ACCESS_TOKEN = _cfg["SUPABASE_ACCESS_TOKEN"]
SUPABASE_REFRESH_TOKEN = _cfg["SUPABASE_REFRESH_TOKEN"]
SUPABASE_USER_ID = _cfg["SUPABASE_USER_ID"]
ONBOARDING_DONE = _cfg["ONBOARDING_DONE"]
METRICS_ENABLED = _cfg["METRICS_ENABLED"]
METRICS_PORT = _cfg["METRICS_PORT"]
