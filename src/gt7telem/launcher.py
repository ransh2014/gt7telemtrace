"""
launcher.py — TRACE unified entry point.
Shows a small menu; picks one of the three tools, then launches it as its
own window. Closing that tool closes the whole app (re-open the exe to
pick a different tool).
"""
# Permanent fix for "attempted relative import with no known parent
# package" -- this file uses relative imports (below) because it's part
# of the gt7telem package, but people naturally try to run it directly
# (F5 in IDLE, double-click, `python launcher.py`), which normally breaks
# relative imports entirely. This block detects that case and patches
# sys.path + __package__ so it works no matter how it's launched --
# `python -m gt7telem.launcher`, F5 in an editor, or a plain double-click.
if __name__ == "__main__" and (not __package__):
    import sys
    from pathlib import Path
    _pkg_dir = Path(__file__).resolve().parent      # .../gt7telem
    _src_dir = _pkg_dir.parent                       # .../src  (or wherever gt7telem/ lives)
    if str(_src_dir) not in sys.path:
        sys.path.insert(0, str(_src_dir))
    __package__ = _pkg_dir.name                      # "gt7telem"

import tkinter as tk

from . import analytics, lap_analyst, race_analyst

# Static imports so PyInstaller's analysis bundles these (and their deps:
# numpy, pandas, matplotlib, pycryptodome) -- do NOT switch these back to
# dynamic __import__() calls, PyInstaller can't see those.
from . import dashboard as gt7telem

BG = "#0a0e1a"


def launch_dashboard():
    analytics.track_launch("dashboard")
    root.destroy()
    app = gt7telem.App()
    app.mainloop()


def launch_lap_analyst():
    analytics.track_launch("lap_analyst")
    root.destroy()
    app = lap_analyst.AnalystApp()
    app.mainloop()


def launch_race_analyst():
    analytics.track_launch("race_analyst")
    root.destroy()
    app = race_analyst.AnalystApp()
    app.mainloop()


def main():
    global root
    root = tk.Tk()
    root.title("TRACE - GT7 Telemetry Suite")
    root.geometry("420x340")
    root.configure(bg=BG)
    root.resizable(False, False)

    tk.Label(root, text="TRACE", font=("Segoe UI", 28, "bold"),
             fg="#00e5ff", bg=BG).pack(pady=(30, 0))
    tk.Label(root, text="GT7 Telemetry Suite", font=("Segoe UI", 11),
             fg="#8a8fa3", bg=BG).pack(pady=(0, 25))

    btn_style = {"font": ("Segoe UI", 12), "width": 26, "height": 2, "bd": 0,
                 "fg": "#0a0e1a", "activeforeground": "#0a0e1a", "cursor": "hand2"}

    tk.Button(root, text="Live Dashboard", bg="#00e5ff", activebackground="#33ecff",
              command=launch_dashboard, **btn_style).pack(pady=6)
    tk.Button(root, text="Lap Analyst", bg="#7c5cff", activebackground="#9c82ff",
              command=launch_lap_analyst, **btn_style).pack(pady=6)
    tk.Button(root, text="Race Analyst", bg="#ff5c8a", activebackground="#ff82a6",
              command=launch_race_analyst, **btn_style).pack(pady=6)

    tk.Label(root, text="community tool - not affiliated with Polyphony Digital",
             font=("Segoe UI", 8), fg="#4a4f63", bg=BG).pack(side="bottom", pady=12)

    root.mainloop()


if __name__ == "__main__":
    main()
