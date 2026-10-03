"""notify.py -- lightweight desktop + sound alerts (new PB, session end).

Deliberately dependency-free (no plyer/win10toast): a transient Tk toast
window plus a system beep, so it works the same on Windows/macOS/Linux
without adding to the frozen-build size. Respects config.NOTIFY_ENABLED
and never raises -- a failed notification should never crash the tool
that triggered it.
"""
import os
import shutil
import subprocess
import sys

from . import config

# Linux sound players to try, in order: (command, sound file or None).
_LINUX_PLAYERS = (
    (["paplay"], "/usr/share/sounds/freedesktop/stereo/complete.oga"),
    (["canberra-gtk-play", "-i", "complete"], None),
    (["aplay", "-q"], "/usr/share/sounds/alsa/Front_Center.wav"),
)


def _sound_command(platform, which=shutil.which, exists=os.path.exists):
    """The external command that plays the alert sound on this OS, or None
    when there isn't one (Windows uses winsound; anything else falls back to
    the Tk bell). Pure, so it can be tested without playing anything."""
    if platform.startswith("win"):
        return None
    if platform == "darwin":
        return ["afplay", "/System/Library/Sounds/Glass.aiff"] if which("afplay") else None
    for cmd, sound_file in _LINUX_PLAYERS:
        if which(cmd[0]) and (sound_file is None or exists(sound_file)):
            return cmd + ([sound_file] if sound_file else [])
    return None


def _spawn(cmd):
    """Start `cmd` without waiting for it. A PyInstaller build points
    LD_LIBRARY_PATH at its own bundled libraries, which can stop the system's
    sound tools from starting, so hand them the original environment."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)


def _beep(root=None):
    """Play the alert sound: the Windows system sound, afplay on macOS, a
    freedesktop/ALSA player on Linux, and the Tk bell if none of those work.
    (A terminal bell does nothing in a packaged app, which has no terminal.)"""
    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
            return
        cmd = _sound_command(sys.platform)
        if cmd:
            try:
                _spawn(cmd)
                return
            except OSError:
                pass
        if root is not None:
            root.bell()
        else:
            print("\a", end="", flush=True)  # last resort: terminal bell
    except Exception:
        pass


def notify(title: str, message: str, sound: bool = True, root=None):
    """Show a small self-dismissing toast in the bottom-right corner of the
    screen (or of `root` if given) and optionally beep. No-op if the user
    turned notifications off in Settings. Safe to call from a background
    thread only via a Tk-owning caller -- schedule with `root.after(0, ...)`
    if this isn't running on the Tk main thread."""
    if not config.NOTIFY_ENABLED:
        return
    if sound:
        _beep(root)
    try:
        import tkinter as tk
        owner = root
        toast = tk.Toplevel(owner) if owner is not None else tk.Tk()
        if owner is None:
            toast.withdraw()  # the Tk() we just made is only a host for Toplevel-less use
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        try:
            toast.attributes("-alpha", 0.96)
        except Exception:
            pass
        w, h = 300, 74
        sw = toast.winfo_screenwidth(); sh = toast.winfo_screenheight()
        toast.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 60}")
        frame = tk.Frame(toast, bg="#13141f", highlightthickness=1,
                          highlightbackground="#00f0d4")
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=title, fg="#00f0d4", bg="#13141f",
                 font=(config.MONO, 10, "bold"), anchor="w",
                 justify="left").pack(fill="x", padx=10, pady=(10, 2))
        tk.Label(frame, text=message, fg="#c8d3f5", bg="#13141f",
                 font=(config.MONO, 9), anchor="w", justify="left",
                 wraplength=280).pack(fill="x", padx=10)
        toast.after(4200, toast.destroy)
    except Exception:
        pass  # headless / no display -- the beep already fired
