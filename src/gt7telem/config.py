"""
config.py — runtime settings for TRACE.

No manual editing needed. On first run it uses sensible defaults; whenever
you change the PS4/PS5 IP in the app, it's saved to settings.json right
next to the .exe (or script) so it's remembered next time.
"""
import base64
import json
import os
import socket
import ssl
import sys
import tempfile
import time
from pathlib import Path

__all__ = ["load", "save", "remember_good_ip", "PS_IP", "LAPS_FOLDER", "SAMPLE_RATE", "KNOWN_IPS", "DEBUG_LOG",
           "ANALYTICS_ENABLED", "PSN_NAME",
           "SUPABASE_ACCESS_TOKEN", "SUPABASE_REFRESH_TOKEN", "SUPABASE_USER_ID", "ONBOARDING_DONE",
           "METRICS_ENABLED", "METRICS_PORT", "METRICS_BIND_ALL",
           "THEME", "NOTIFY_ENABLED", "UPDATE_CHECK_ENABLED", "backup_laps",
           "ensure_ca_bundle", "explain_error", "MONO", "install_mac_buttons"]

_SUPABASE_SECRET_KEYS = ("SUPABASE_ACCESS_TOKEN", "SUPABASE_REFRESH_TOKEN", "SUPABASE_USER_ID")
_ENC_PREFIX = "enc:v1:"


# Path fragments meaning the executable lives somewhere a package manager
# owns rather than a folder the user picked: Chocolatey's lib folder
# (admin-write-only on Windows) and WinGet's Packages folder are both
# replaced wholesale on upgrade.
_MANAGED_INSTALL_MARKERS = ("chocolatey", "winget")


def _is_writable_dir(d: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=d):
            pass
        return True
    except OSError:
        return False


def _portable_dir() -> Path | None:
    """The folder a frozen build keeps settings.json and laps/ in, or None
    to use the per-user folders instead.

    Frozen (.exe/.app/binary) builds are portable by design -- unzip
    anywhere and the data lives next to the executable. That only works
    where the user put the executable themselves, though. Installed by
    Chocolatey, settings silently never saved and lap saves raised (the
    folder is admin-only); installed by WinGet or run as a macOS .app, the
    data sat inside a folder that's replaced on upgrade (and, for the .app,
    hidden from the user). Those, and any folder we can't write to, now use
    the same per-user folders pip installs do. An existing portable install
    (settings.json already next to the exe) stays where it is."""
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    parts = [p.lower() for p in exe_dir.parts]
    if any(p.endswith(".app") for p in parts):
        return None
    if any(marker in p for p in parts for marker in _MANAGED_INSTALL_MARKERS):
        return None
    # Scoop installs under ~/scoop/apps/<app>/<version>/ -- writable, but the
    # versioned folder is replaced on upgrade, so settings/laps would be stranded.
    if "scoop" in parts and "apps" in parts:
        return None
    if (exe_dir / "settings.json").exists() or _is_writable_dir(exe_dir):
        return exe_dir
    return None


def _base_dir() -> Path:
    """Where settings.json lives: next to the executable for a portable
    frozen build (see _portable_dir), otherwise ~/.gt7telem.
    Non-frozen (pip/source) runs used to resolve this to the package's own
    install directory (Path(__file__).parent), which for a pip install
    means inside site-packages: not reliably writable, and wiped on every
    `pip install --upgrade` -- silently, since save() swallows write
    failures. A per-user config dir survives upgrades and reinstalls."""
    portable = _portable_dir()
    if portable is not None:
        return portable
    try:
        d = Path.home() / ".gt7telem"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except (OSError, RuntimeError):
        # No usable home folder: fall back to a temp folder rather than
        # crashing `import gt7telem`.
        d = Path(tempfile.gettempdir()) / "gt7telem"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return d


def _default_laps_dir() -> Path:
    """Portable frozen builds keep laps next to the exe, as before.
    Everything else gets a visible ~/TRACE/laps instead of nesting inside
    the hidden ~/.gt7telem config dir, which would be a surprising place to
    go looking for recorded laps."""
    portable = _portable_dir()
    if portable is not None:
        return portable / "laps"
    return Path.home() / "TRACE" / "laps"


_SETTINGS_FILE = _base_dir() / "settings.json"
_KEY_FILE = _base_dir() / ".settings.key"


def _get_or_create_key() -> bytes:
    """32-byte local key used to encrypt the Supabase session tokens at
    rest, kept in a separate file from settings.json so a copy/paste/upload
    of settings.json alone (bug report, backup, accidental share) doesn't
    hand over a usable session token in plain text."""
    try:
        for _ in range(3):
            if _KEY_FILE.exists():
                key = _KEY_FILE.read_bytes()
                if len(key) == 32:
                    return key
                if len(key) != 0:
                    # Damaged or foreign key file: don't silently replace it
                    # (that would orphan every encrypted token).
                    return b""
            # Create atomically with owner-only permissions from the start;
            # O_EXCL means two first-launch processes can't both write a key.
            key = os.urandom(32)
            try:
                fd = os.open(str(_KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if _KEY_FILE.exists() and _KEY_FILE.stat().st_size == 0:
                    try:
                        _KEY_FILE.unlink()  # crash left an empty file; safe to redo
                    except OSError:
                        pass
                continue  # another process won the race (or we cleared a stub); re-read
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(key)
            except Exception:
                try:
                    _KEY_FILE.unlink()
                except OSError:
                    pass
                raise
            return key
        return b""
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
    if not isinstance(value, str):
        return ""
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
    # dashboard.py's RECORD_RATE_OPTIONS). 10 is the safe default -- it's
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
    # UI color theme -- "dark" (default) or "light". Applied by each GUI
    # tool (launcher/dashboard/lap_analyst/race_analyst) at startup; switch
    # takes effect the next time a tool window is opened.
    "THEME": "dark",
    # Desktop + sound alerts for new personal bests and session/race end.
    "NOTIFY_ENABLED": True,
    # Check GitHub for a newer release on launcher startup. Read-only HEAD
    # request-equivalent to the public releases API -- no data sent.
    "UPDATE_CHECK_ENABLED": True,
}

def remember_good_ip(ip: str) -> list:
    """Push `ip` to the front of the known-good IP list (dedup, uncapped --
    every console you've ever successfully connected to stays in the
    dropdown), persist it, and return the updated list. Only call this once
    a connection has actually been confirmed -- not on every keystroke."""
    ip = (ip or "").strip()
    if not ip:
        return load().get("KNOWN_IPS", [])
    data  = load()
    known = [x for x in data.get("KNOWN_IPS", []) if x != ip]
    known.insert(0, ip)
    save(KNOWN_IPS=known)
    return known


def _coerce(key: str, value):
    """Return `value` if it has the right type for `key`, else the default.
    A hand-edited settings.json (string KNOWN_IPS, text SAMPLE_RATE...)
    must not crash the apps that read these."""
    default = _DEFAULTS.get(key)
    if default is None:
        return value
    if key in _SUPABASE_SECRET_KEYS or isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)) and value == value and abs(value) != float("inf"):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return default
        return default
    if isinstance(default, list):
        if isinstance(value, list):
            return [x for x in value if isinstance(x, str)]
        return default
    return value


def _read_raw():
    """settings.json exactly as stored (secrets still encrypted).

    Returns (data, ok). ok is False only when the file exists but can't be
    read right now (locked by antivirus, permissions): callers must NOT
    write defaults over it in that case. A file that's corrupt is moved
    aside to settings.json.bad (so it isn't lost) and treated as empty."""
    try:
        if not _SETTINGS_FILE.exists():
            return {}, True
        text = _SETTINGS_FILE.read_text(encoding="utf-8-sig")
    except OSError:
        return {}, False
    except ValueError:  # UnicodeDecodeError
        text = ""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, True
    except ValueError:
        pass
    try:
        os.replace(_SETTINGS_FILE, _SETTINGS_FILE.with_name(_SETTINGS_FILE.name + ".bad"))
    except OSError:
        return {}, False
    return {}, True


def load() -> dict:
    data, _ok = _read_raw()
    merged = dict(_DEFAULTS)
    merged.update(data)
    for k in list(merged):
        merged[k] = _coerce(k, merged[k])
    for k in _SUPABASE_SECRET_KEYS:
        merged[k] = _decrypt(merged.get(k, ""))
    return merged


def _atomic_write(path: Path, text: str) -> bool:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(4):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))  # antivirus/indexer briefly holding the file
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
    return False


def save(**kwargs) -> bool:
    """Persist the given settings. Returns True if they were written.

    Only keys already on disk plus the ones passed in are written -- derived
    defaults (like the absolute laps folder) are not frozen into the file.
    Secrets not passed in keep their stored ciphertext untouched, so a
    failed decrypt can never turn into an empty value written back."""
    data, ok = _read_raw()
    if not ok:
        return False
    out = dict(data)
    for k in _SUPABASE_SECRET_KEYS:
        if k in out and not isinstance(out[k], str):
            out[k] = ""
        elif k in out and out[k] and not out[k].startswith(_ENC_PREFIX):
            out[k] = _encrypt(out[k])  # migrate an old plaintext token
    for k, v in kwargs.items():
        out[k] = _encrypt(v) if k in _SUPABASE_SECRET_KEYS else v
    return _atomic_write(_SETTINGS_FILE, json.dumps(out, indent=2))


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
THEME = _cfg["THEME"]
NOTIFY_ENABLED = _cfg["NOTIFY_ENABLED"]
UPDATE_CHECK_ENABLED = _cfg["UPDATE_CHECK_ENABLED"]


def backup_laps() -> "Path | None":
    """Zip the whole laps folder to ~/TRACE/backups/laps_backup_<ts>.zip and
    return the path, or None if there's nothing to back up / it fails.
    Local-only, no network -- a simple safety net against a deleted laps
    folder, disk wipe, or moving to a new machine by hand."""
    import zipfile
    from datetime import datetime
    global last_backup_error
    last_backup_error = None
    try:
        # Read the setting now, not the value frozen at import, so a folder
        # changed since launch is the one that gets backed up.
        src = Path(load().get("LAPS_FOLDER") or LAPS_FOLDER)
        if not src.exists() or not any(src.iterdir()):
            return None
        backups_dir = Path.home() / "TRACE" / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backups_dir / f"laps_backup_{ts}.zip"
        src_r, backups_r = src.resolve(), backups_dir.resolve()
        tmp = dest.with_name(dest.name + ".part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in src.rglob("*"):
                if not f.is_file():
                    continue
                fr = f.resolve()
                # If the laps folder is at/above the backups folder, never
                # archive the backups (or the archive being written).
                if backups_r == fr or backups_r in fr.parents:
                    continue
                zf.write(f, fr.relative_to(src_r))
        os.replace(tmp, dest)
        return dest
    except Exception as e:
        last_backup_error = str(e) or e.__class__.__name__
        return None


last_backup_error = None  # why the last backup_laps() failed; None = ok / nothing to back up


# ---------------------------------------------------------------------------
# HTTPS CA-certificate fallback.
#
# A PyInstaller binary built on Ubuntu looks for CA certs under /usr/lib/ssl.
# Other distros keep them elsewhere -- Fedora/RHEL under /etc/pki, openSUSE under
# /var/lib/ca-certificates, Arch under /etc/ca-certificates, NixOS under /etc/ssl/certs --
# and some (newer Fedora) ship only a directory of hashed certs with no bundle file.
# Without a fix every HTTPS call fails certificate verification.
#
# ensure_ca_bundle() runs once, when this module is first imported (before anything
# touches the network). It does nothing on Windows (OS store), nothing if the user set
# SSL_CERT_FILE / SSL_CERT_DIR, and nothing if Python's default CA file or dir exists.
# Otherwise it tries a known system bundle that actually loads, then a known system cert
# directory, then certifi's bundled copy. Verification stays ON. It never raises.
#
# explain_error(exc) turns a network exception into a short category so the UI can say
# *why* a request failed instead of a generic "couldn't reach the server".
# ---------------------------------------------------------------------------

CA_CANDIDATES = (
    "/etc/pki/tls/certs/ca-bundle.crt",                    # Fedora / RHEL / CentOS
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",   # Fedora / RHEL (extracted)
    "/etc/ssl/certs/ca-certificates.crt",                  # Debian / Ubuntu / Arch / Gentoo
    "/etc/ca-certificates/extracted/tls-ca-bundle.pem",    # Arch (extracted)
    "/var/lib/ca-certificates/ca-bundle.pem",              # openSUSE (extracted)
    "/etc/ssl/ca-bundle.pem",                              # openSUSE
    "/etc/ssl/certs/ca-bundle.crt",                        # NixOS / some others
    "/etc/pki/tls/cacert.pem",                             # older RHEL
    "/usr/share/ssl/certs/ca-bundle.crt",                  # very old RHEL
    "/etc/ssl/cert.pem",                                   # Alpine / macOS / BSD
    "/usr/local/share/certs/ca-root-nss.crt",              # FreeBSD
    "/usr/local/etc/ssl/cert.pem",                         # FreeBSD / OpenBSD ports
    "/data/data/com.termux/files/usr/etc/tls/cert.pem",    # Termux
)

# Hashed cert directories, used only when no bundle file works (e.g. newer Fedora).
CA_DIR_CANDIDATES = (
    "/etc/pki/tls/certs",
    "/etc/ssl/certs",
    "/etc/openssl/certs",
    "/var/lib/ca-certificates/openssl",
    "/etc/ca-certificates/extracted/cadir",
)


def _usable_file(path: str) -> bool:
    """True if `path` is a CA bundle OpenSSL can actually load (not empty/garbage)."""
    try:
        if not os.path.isfile(path):
            return False
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=path)
        return True
    except Exception:
        return False


def _usable_dir(path: str) -> bool:
    """True if `path` looks like a hashed cert dir (has c_rehash-style *.0 entries)."""
    try:
        return os.path.isdir(path) and any(n.endswith(".0") for n in os.listdir(path))
    except Exception:
        return False


def ensure_ca_bundle() -> str | None:
    """Point OpenSSL at a CA store if Python's default has none. Returns what was set, else None."""
    try:
        if sys.platform.startswith("win"):
            return None
        if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
            return None
        paths = ssl.get_default_verify_paths()
        if (paths.cafile and os.path.isfile(paths.cafile)) or (
            paths.capath and _usable_dir(paths.capath)
        ):
            return None
        for cand in CA_CANDIDATES:
            if _usable_file(cand):
                os.environ["SSL_CERT_FILE"] = cand
                return cand
        for d in CA_DIR_CANDIDATES:
            if _usable_dir(d):
                os.environ["SSL_CERT_DIR"] = d
                return d
        try:
            import certifi

            cand = certifi.where()
            if _usable_file(cand):
                os.environ["SSL_CERT_FILE"] = cand
                return cand
        except Exception:
            pass
    except Exception:
        pass
    return None


def explain_error(exc: BaseException) -> str:
    """Classify a network exception: "certs", "dns", "timeout", "http:<code>" or "network"."""
    try:
        import urllib.error

        if isinstance(exc, urllib.error.HTTPError):
            return f"http:{exc.code}"
        reason = getattr(exc, "reason", exc)
        # Only a failed certificate check means "install CA certificates";
        # a handshake timeout / captive portal is just a network problem.
        if isinstance(reason, ssl.SSLCertVerificationError) or isinstance(exc, ssl.SSLCertVerificationError):
            return "certs"
        if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
            return "certs"
        if isinstance(reason, socket.gaierror):
            return "dns"
        if isinstance(reason, (TimeoutError, socket.timeout)) or isinstance(exc, (TimeoutError, socket.timeout)):
            return "timeout"
    except Exception:
        pass
    return "network"


ensure_ca_bundle()


def _mono_font(platform: str) -> str:
    """Monospace UI font family for this OS. Consolas only ships with Windows; elsewhere Tk
    would silently substitute some arbitrary (often proportional) font and misalign the
    dashboard's number columns, so ask for the platform's own monospace face instead."""
    if platform.startswith("win"):
        return "Consolas"
    if platform == "darwin":
        return "Menlo"
    return "monospace"  # fontconfig resolves this to the distro's default mono font


MONO = _mono_font(sys.platform)

def _make_mac_button_class(tk):
    """Build the label-based button class. A function so that importing
    config never imports tkinter (the headless modules and tests rely on that)."""

    class MacButton(tk.Label):
        """A flat button drawn as a Label. macOS's native (Aqua) Tk ignores a
        Button's bg/fg, so TRACE's dark theme -- and light text on a dark
        button -- came out as unreadable default-white buttons there. A Label
        honours colours on every OS; this gives it the parts of tk.Button the
        app uses: command=, state=, hover/press colours and invoke()."""

        def __init__(self, master=None, cnf=None, **kw):
            kw = dict(cnf or {}, **kw)
            self._command = kw.pop("command", None)
            self._state = kw.pop("state", "normal")
            self._active_bg = kw.pop("activebackground", None)
            self._active_fg = kw.pop("activeforeground", None)
            for ignored in ("default", "overrelief", "repeatdelay", "repeatinterval", "disabledforeground"):
                kw.pop(ignored, None)
            kw.setdefault("relief", "flat")
            tk.Label.__init__(self, master, **kw)
            self._bg = self.cget("bg")
            self._fg = self.cget("fg")
            self._down = False
            self.bind("<Enter>", self._enter)
            self.bind("<Leave>", self._leave)
            self.bind("<ButtonPress-1>", self._press)
            self.bind("<ButtonRelease-1>", self._release)
            self._apply_state()

        def _paint(self, bg, fg):
            tk.Label.configure(self, bg=bg, fg=fg)

        def _apply_state(self):
            if self._state == "disabled":
                tk.Label.configure(self, bg=self._bg, fg="#777777", cursor="arrow")
            else:
                tk.Label.configure(self, bg=self._bg, fg=self._fg, cursor="hand2")

        def _enter(self, _e):
            if self._state != "disabled" and self._active_bg:
                self._paint(self._active_bg, self._active_fg or self._fg)

        def _leave(self, _e):
            self._down = False
            self._apply_state()

        def _press(self, _e):
            if self._state == "disabled":
                return
            self._down = True
            if self._active_bg:
                self._paint(self._active_bg, self._active_fg or self._fg)

        def _release(self, e):
            was_down, self._down = self._down, False
            if not was_down or self._state == "disabled":
                return
            self._apply_state()
            inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
            if inside and self._command:
                self._command()

        def configure(self, cnf=None, **kw):
            if cnf is None and not kw:
                return tk.Label.configure(self)   # a plain query
            kw = dict(cnf or {}, **kw)
            if "command" in kw:
                self._command = kw.pop("command")
            if "state" in kw:
                self._state = kw.pop("state")
            if "activebackground" in kw:
                self._active_bg = kw.pop("activebackground")
            if "activeforeground" in kw:
                self._active_fg = kw.pop("activeforeground")
            for key in ("bg", "background"):
                if key in kw:
                    self._bg = kw.pop(key)
            for key in ("fg", "foreground"):
                if key in kw:
                    self._fg = kw.pop(key)
            if kw:
                tk.Label.configure(self, **kw)
            self._apply_state()

        config = configure

        def cget(self, key):
            if key == "state":
                return self._state
            if key == "command":
                return self._command
            return tk.Label.cget(self, key)

        __getitem__ = cget   # Misc binds its own cget to `w[key]`; point it at this one

        def invoke(self):
            if self._state != "disabled" and self._command:
                return self._command()

    return MacButton


def install_mac_buttons():
    """On macOS, make tk.Button a label-based button (see MacButton) so the
    app's colours show up there. A no-op everywhere else. Safe to call from
    every GUI module; returns True when the replacement is in place."""
    if sys.platform != "darwin":
        return False
    try:
        import tkinter as tk
    except ImportError:
        return False
    if not getattr(tk.Button, "_trace_label_button", False):
        cls = _make_mac_button_class(tk)
        cls._trace_label_button = True
        tk.Button = cls
    return True
