"""
runtime_config.py — replaces config.py for the standalone .exe builds.

No manual editing needed. On first run it uses sensible defaults; whenever
you change the PS4/PS5 IP in the app, it's saved to settings.json right
next to the .exe (or script) so it's remembered next time.
"""
import base64
import json
import os
import sys
from pathlib import Path

__all__ = ["load", "save", "remember_good_ip", "PS_IP", "LAPS_FOLDER", "KNOWN_IPS", "ANALYTICS_ENABLED", "PSN_NAME",
           "SUPABASE_ACCESS_TOKEN", "SUPABASE_REFRESH_TOKEN", "SUPABASE_USER_ID", "ONBOARDING_DONE",
           "METRICS_ENABLED", "METRICS_PORT", "METRICS_BIND_ALL"]

_SUPABASE_SECRET_KEYS = ("SUPABASE_ACCESS_TOKEN", "SUPABASE_REFRESH_TOKEN", "SUPABASE_USER_ID")
_ENC_PREFIX = "enc:v1:"


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
_KEY_FILE = _base_dir() / ".settings.key"


def _get_or_create_key() -> bytes:
    """32-byte local key used to encrypt the Supabase session tokens at
    rest, kept in a separate file from settings.json so a copy/paste/upload
    of settings.json alone (bug report, backup, accidental share) doesn't
    hand over a usable session token in plain text."""
    try:
        if _KEY_FILE.exists():
            key = _KEY_FILE.read_bytes()
            if len(key) == 32:
                return key
        key = os.urandom(32)
        _KEY_FILE.write_bytes(key)
        try:
            os.chmod(_KEY_FILE, 0o600)
        except OSError:
            pass  # best-effort on platforms/filesystems without POSIX perms (e.g. Windows)
        return key
    except Exception:
        return b""  # caller falls back to storing the value unencrypted


def _encrypt(value: str) -> str:
    if not value or value.startswith(_ENC_PREFIX):
        return value
    try:
        from Crypto.Cipher import AES
        key = _get_or_create_key()
        if not key:
            return value
        nonce = os.urandom(12)
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(value.encode("utf-8"))
        blob = base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")
        return _ENC_PREFIX + blob
    except Exception:
        return value  # never lose the value over an encryption failure


def _decrypt(value: str) -> str:
    if not value or not value.startswith(_ENC_PREFIX):
        return value  # empty, or a pre-existing plaintext token from before this fix
    try:
        from Crypto.Cipher import AES
        key = _get_or_create_key()
        raw = base64.urlsafe_b64decode(value[len(_ENC_PREFIX):].encode("ascii"))
        nonce, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
    except Exception:
        return ""  # undecryptable (key lost/rotated) -- treat as logged out, not a crash

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
    # Off by default: the metrics server binds localhost-only unless this
    # is on, in which case it binds 0.0.0.0 -- reachable from anyone else
    # on the same Wi-Fi/LAN, not just this machine. Turn on only if you're
    # scraping from a remote Grafana/Prometheus on a network you trust.
    "METRICS_BIND_ALL": False,
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
        except Exception:
            merged = dict(_DEFAULTS)
    else:
        merged = dict(_DEFAULTS)
    for k in _SUPABASE_SECRET_KEYS:
        merged[k] = _decrypt(merged.get(k, ""))
    return merged


def save(**kwargs) -> None:
    data = load()
    data.update(kwargs)
    out = dict(data)
    for k in _SUPABASE_SECRET_KEYS:
        out[k] = _encrypt(out.get(k, ""))
    try:
        _SETTINGS_FILE.write_text(json.dumps(out, indent=2), encoding="utf-8")
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
METRICS_BIND_ALL = _cfg["METRICS_BIND_ALL"]
