"""webui_auth.py -- the passcode that guards the WEB UI when ALLOW REMOTE (WEB UI) is on.

Model (the Android app's WebAuth.kt is a port of this file and follows the same rules):
  * Each time the WEB UI is turned on: a fresh 6-digit code (``secrets``) and a separate login token for the QR
    link (32 random bytes, urlsafe). Both live in memory only -- never in settings.json, never in a log.
  * Code login: constant-time compare; per client IP, 5 wrong tries lock that IP out for 60 s, doubling on each
    further lockout up to 10 minutes. A wrong code and a lockout give the caller the same answer (False).
  * QR token: ``?t=<token>`` logs a device in once; it is spent on first use or after 5 minutes, whichever comes
    first, and a new one is issued for the Dashboard's QR (``qr_token()``).
  * Sessions: random cookie values kept as SHA-256 hashes in memory. Turning the WEB UI off, or LOG OUT ALL
    DEVICES, forgets them all.
  * Remember this device: a 30-day random device token; only its SHA-256 hash and a created date are stored, in
    devices.json next to settings.json, so a remembered phone skips the code even after the code rotates.
    LOG OUT ALL DEVICES empties that file.
Never imports tkinter (headless-safe).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from pathlib import Path

__all__ = ["Auth", "SESSION_COOKIE", "DEVICE_COOKIE", "DEVICE_DAYS", "QR_TTL_S", "MAX_TRIES",
           "LOCK_BASE_S", "LOCK_MAX_S", "is_loopback", "parse_cookies"]

SESSION_COOKIE = "trace_s"
DEVICE_COOKIE = "trace_d"
DEVICE_DAYS = 30
QR_TTL_S = 300
MAX_TRIES = 5
LOCK_BASE_S = 60
LOCK_MAX_S = 600


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def is_loopback(host) -> bool:
    """127.0.0.0/8, ::1 and ::ffff:127.x (an IPv4 client on a dual-stack socket)."""
    try:
        ip = ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(ip.is_loopback or (mapped is not None and mapped.is_loopback))


def parse_cookies(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "").split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k and k not in out:
            out[k] = v.strip().strip('"')
    return out


def _default_devices_path() -> Path:
    from . import config
    return Path(config._SETTINGS_FILE).with_name("devices.json")


class Auth:
    """All WEB UI auth state. Thread-safe; `clock` is injectable so tests can fast-forward lockouts and expiry."""

    def __init__(self, devices_path=None, clock=time.monotonic, today=None):
        self._lock = threading.RLock()
        self._devices_path = devices_path
        self._clock = clock
        self._today = today or _dt.date.today
        self.code = ""
        self._qr = ""
        self._qr_at = 0.0
        self._qr_used = True
        self._sessions: set[str] = set()
        self._fails: dict[str, list] = {}     # ip -> [wrong tries, lockouts so far, locked until]

    # ── lifecycle ────────────────────────────────────────────────────────────
    def rotate(self) -> None:
        """WEB UI turned on: new code, new QR token, no sessions (remembered devices stay)."""
        with self._lock:
            self.code = f"{secrets.randbelow(1_000_000):06d}"
            self._sessions.clear()
            self._new_qr()

    def end(self) -> None:
        """WEB UI turned off: every session and the QR token stop working; the code is forgotten."""
        with self._lock:
            self.code = ""
            self._sessions.clear()
            self._qr, self._qr_used = "", True

    def logout_all(self) -> None:
        """LOG OUT ALL DEVICES: forget every session and every remembered device."""
        with self._lock:
            self._sessions.clear()
            self._write_devices([])

    # ── code login ───────────────────────────────────────────────────────────
    def locked(self, ip: str) -> bool:
        with self._lock:
            f = self._fails.get(ip)
            return bool(f and f[2] > self._clock())

    def check_code(self, ip: str, code) -> bool:
        """True only for the right code from an IP that isn't locked out. Wrong code and lockout look the same."""
        with self._lock:
            now = self._clock()
            f = self._fails.setdefault(ip, [0, 0, 0.0])
            if f[2] > now:
                return False
            given = str(code or "").strip().encode("utf-8")
            ok = bool(self.code) and hmac.compare_digest(given, self.code.encode("utf-8"))
            if ok:
                self._fails.pop(ip, None)
                return True
            f[0] += 1
            if f[0] >= MAX_TRIES:
                f[2] = now + min(LOCK_MAX_S, LOCK_BASE_S * (2 ** f[1]))
                f[1] += 1
                f[0] = 0
            return False

    # ── QR token ─────────────────────────────────────────────────────────────
    def _new_qr(self) -> None:
        self._qr = secrets.token_urlsafe(32)
        self._qr_at = self._clock()
        self._qr_used = False

    def qr_token(self) -> str:
        """The token for the Dashboard's QR; a fresh one once the last was used or expired ("" while off)."""
        with self._lock:
            if not self.code:
                return ""
            if self._qr_used or self._clock() - self._qr_at >= QR_TTL_S:
                self._new_qr()
            return self._qr

    def consume_qr(self, token) -> bool:
        with self._lock:
            if not token or not self._qr or self._qr_used or self._clock() - self._qr_at >= QR_TTL_S:
                return False
            if not hmac.compare_digest(str(token).encode("utf-8"), self._qr.encode("utf-8")):
                return False
            self._qr_used = True
            return True

    # ── sessions and remembered devices ──────────────────────────────────────
    def new_session(self) -> str:
        tok = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions.add(_sha(tok))
        return tok

    def remember_device(self) -> str:
        tok = secrets.token_urlsafe(32)
        with self._lock:
            devs = self._read_devices()
            devs.append({"hash": _sha(tok), "created": self._today().isoformat()})
            self._write_devices(devs)
        return tok

    def is_authed(self, cookies: dict) -> bool:
        s, d = cookies.get(SESSION_COOKIE), cookies.get(DEVICE_COOKIE)
        with self._lock:
            if not self.code:
                return False
            if s and _sha(s) in self._sessions:
                return True
            if d:
                h = _sha(d)
                return any(hmac.compare_digest(h, e["hash"]) for e in self._read_devices())
        return False

    # ── devices.json ─────────────────────────────────────────────────────────
    def _path(self) -> Path:
        return Path(self._devices_path) if self._devices_path else _default_devices_path()

    def _read_devices(self) -> list[dict]:
        """Valid, unexpired entries only (a hand-edited or damaged file just counts as empty)."""
        try:
            raw = json.loads(self._path().read_text(encoding="utf-8"))
            rows = raw.get("devices", []) if isinstance(raw, dict) else []
        except (OSError, ValueError):
            return []
        cutoff = self._today() - _dt.timedelta(days=DEVICE_DAYS)
        out = []
        for e in rows:
            try:
                h, c = str(e["hash"]), _dt.date.fromisoformat(str(e["created"]))
            except (KeyError, TypeError, ValueError):
                continue
            if len(h) == 64 and all(ch in "0123456789abcdef" for ch in h) and cutoff < c <= self._today():
                out.append({"hash": h, "created": c.isoformat()})
        return out

    def _write_devices(self, devs: list[dict]) -> None:
        p = self._path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps({"devices": devs}, indent=1), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass
