"""voice.py -- spoken Dashboard alerts (fuel low, tyres too hot / cold).

Uses the operating system's own voice, so there is nothing to install and no
network involved:

    Windows   SAPI (System.Speech) through PowerShell
    macOS     `say`
    Linux     `spd-say` or `espeak-ng` / `espeak`, if one is installed

With no voice available (a Linux box with none of those, say) it plays the
same alert sound notify.py uses, so an alert is never silently lost. Speaking
never blocks the caller: the voice runs as a separate process started from a
background thread. Never raises -- a failed alert must not take the Dashboard
down. Muting is config.VOICE_ENABLED, checked by the caller.
"""

import os
import shutil
import subprocess
import sys
import threading

from . import notify

# Windows: read the text from an environment variable so nothing the
# Dashboard says is ever parsed as PowerShell code.
_WIN_SCRIPT = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.Rate = 1; "
    "$s.Speak($env:TRACE_SPEAK_TEXT)"
)
_ENV_TEXT = "TRACE_SPEAK_TEXT"


def voice_command(platform, text, which=shutil.which):
    """The command (argv list) that speaks `text` on this OS, or None when no
    voice is installed. Pure, so it can be tested without speaking."""
    if platform.startswith("win"):
        for shell in ("powershell", "pwsh"):
            if which(shell):
                return [shell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", _WIN_SCRIPT]
        return None
    if platform == "darwin":
        return ["say", text] if which("say") else None
    for exe in ("spd-say", "espeak-ng", "espeak"):
        if which(exe):
            return [exe, text]
    return None


def available(platform=None, which=shutil.which):
    """True if an OS voice can be used here."""
    return voice_command(platform or sys.platform, "", which) is not None


def _run(cmd, text):
    env = dict(os.environ)
    if sys.platform.startswith("win"):
        env[_ENV_TEXT] = text
    elif getattr(sys, "frozen", False):
        # a PyInstaller build points LD_LIBRARY_PATH at its own libraries, which can
        # stop the system's speech tools from starting (same fix as notify._spawn)
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, env=env, **kwargs)
    proc.wait()  # we are on our own thread; this just reaps the process


def _speak_now(text, platform, which, run, fallback):
    cmd = voice_command(platform, text, which)
    if cmd is not None:
        try:
            run(cmd, text)
            return
        except Exception:
            pass
    fallback()


def speak(text, platform=None, which=shutil.which, run=_run, fallback=None, threaded=True):
    """Say `text` out loud without blocking, or play the alert sound if there is
    no voice. The keyword arguments exist so the tests can swap the pieces out."""
    platform = platform or sys.platform
    fallback = fallback or (lambda: notify._beep(None))

    def work():
        try:
            _speak_now(text, platform, which, run, fallback)
        except Exception:
            pass

    if threaded:
        threading.Thread(target=work, daemon=True).start()
    else:
        work()


# ── when to speak ─────────────────────────────────────────────────────────────
class AlertAnnouncer:
    """Turns the Dashboard's per-frame alert flags into a few spoken lines.

    An alert is spoken the moment it turns on. Fuel and hot-tyre alerts repeat
    every `repeat_s` while they stay on; the cold-tyre alert is said once (tyres
    start cold, nagging about it would be noise). An alert only counts as
    cleared after it has been off for `clear_s`, so a temperature hovering round
    its limit doesn't make it talk every few seconds. Pure -- feed it the time."""

    MESSAGES = {"fuel": "Fuel low", "hot": "Tyres overheating", "cold": "Tyres cold"}
    REPEATS = {"fuel": True, "hot": True, "cold": False}

    def __init__(self, repeat_s: float = 60.0, clear_s: float = 5.0):
        self.repeat_s = repeat_s
        self.clear_s = clear_s
        self._spoken_at: dict[str, float] = {}   # kind -> when it was last said
        self._off_since: dict[str, float] = {}   # kind -> when it went quiet

    def reset(self):
        self._spoken_at.clear()
        self._off_since.clear()

    def update(self, active, now: float, fuel_laps=None) -> list[str]:
        """`active` is the set of alert kinds ("fuel", "hot", "cold") on this
        frame. Returns what to say now, in a fixed priority order."""
        out = []
        for kind in ("fuel", "hot", "cold"):
            if kind in active:
                self._off_since.pop(kind, None)
                last = self._spoken_at.get(kind)
                if last is None or (self.REPEATS[kind] and now - last >= self.repeat_s):
                    self._spoken_at[kind] = now
                    out.append(self._message(kind, fuel_laps))
            elif kind in self._spoken_at:
                since = self._off_since.setdefault(kind, now)
                if now - since >= self.clear_s:
                    del self._spoken_at[kind]
                    del self._off_since[kind]
        return out

    def _message(self, kind, fuel_laps):
        if kind == "fuel" and fuel_laps is not None and 0 < fuel_laps < 99:
            return f"Fuel low. About {fuel_laps:.1f} laps left"
        return self.MESSAGES[kind]
