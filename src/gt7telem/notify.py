"""notify.py -- lightweight desktop + sound alerts (new PB, session end).

Deliberately dependency-free (no plyer/win10toast): a transient Tk toast
window plus a system beep, so it works the same on Windows/macOS/Linux
without adding to the frozen-build size. Respects config.NOTIFY_ENABLED
and never raises -- a failed notification should never crash the tool
that triggered it.
"""
import sys

from . import config


def _beep():
    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        else:
            print("\a", end="", flush=True)  # terminal bell fallback
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
        _beep()
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
                 font=("Consolas", 10, "bold"), anchor="w",
                 justify="left").pack(fill="x", padx=10, pady=(10, 2))
        tk.Label(frame, text=message, fg="#c8d3f5", bg="#13141f",
                 font=("Consolas", 9), anchor="w", justify="left",
                 wraplength=280).pack(fill="x", padx=10)
        toast.after(4200, toast.destroy)
    except Exception:
        pass  # headless / no display -- the beep already fired
