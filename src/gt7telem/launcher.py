"""
launcher.py — TRACE unified entry point.
Shows a small menu; picks one of the three tools, then launches it as its
own window. Closing that tool closes the whole app (re-open the exe to
pick a different tool).
"""
import tkinter as tk

# Static imports so PyInstaller's analysis bundles these (and their deps:
# numpy, pandas, matplotlib, pycryptodome) -- do NOT switch these back to
# dynamic __import__() calls, PyInstaller can't see those.
from . import dashboard as gt7telem
from . import lap_analyst, race_analyst

BG = "#0a0e1a"


def launch_dashboard():
    root.destroy()
    app = gt7telem.App()
    app.mainloop()


def launch_lap_analyst():
    root.destroy()
    app = lap_analyst.AnalystApp()
    app.mainloop()


def launch_race_analyst():
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
