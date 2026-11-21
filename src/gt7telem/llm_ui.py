"""The Tk side of the local AI wording switch: the warning dialog shown BEFORE anything is downloaded, the progress while
it downloads, and "Remove AI model". All the logic (spec check, what to download, hash checks) lives in llm.py and is
tested there; this file only draws it."""

import threading
import tkinter as tk
from tkinter import messagebox

from . import config as runtime_config
from . import llm, theme

REMOVE_LABEL = llm.REMOVE_LABEL


def ask_enable(parent, log, on_done):
    """Show the warning for turning on local AI wording. Nothing is downloaded unless the user presses the first button.
    `on_done(True)` once the model is installed and the setting saved; `on_done(False)` on "switch back to Rules mode",
    on close, or on any failure (the setting stays off)."""
    flow = llm.EnableFlow()
    try:
        w = flow.request()
    except Exception as e:      # never let a failed spec check stop the window opening
        messagebox.showerror("Local AI wording", f"Couldn't check this PC: {e}")
        on_done(False)
        return
    win = tk.Toplevel(parent)
    win.title("TRACE - Local AI wording")
    win.configure(bg=theme.BG)
    theme.fit_window(win, 640, 560, 420, 360)
    win.transient(parent)
    colour = {"green": "#3c9a5f", "amber": "#c9a24b", "red": "#e2455a"}[w.rating.level]
    tk.Label(win, text=w.title, bg=theme.BG, fg=theme.TXT, font=(theme.MONO, 12, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
    tk.Label(win, text=f"PC check: {w.rating.level.upper()}", bg=theme.BG, fg=colour, font=(theme.MONO, 10, "bold")).pack(anchor="w", padx=14)
    box = tk.Text(win, bg=theme.BG2, fg=theme.TXT, font=(theme.MONO, 9), wrap="word", relief="flat", padx=10, pady=8, height=18)
    box.pack(fill="both", expand=True, padx=14, pady=8)
    box.insert("1.0", w.body)
    box.config(state="disabled")
    status = tk.Label(win, text="", bg=theme.BG, fg=theme.DIM, font=(theme.MONO, 9), anchor="w", justify="left")
    status.pack(fill="x", padx=14)
    row = tk.Frame(win, bg=theme.BG)
    row.pack(fill="x", padx=14, pady=10)
    state = {"busy": False, "cancel": False}

    def finish(ok, msg=""):
        if not win.winfo_exists():
            return
        win.destroy()
        if msg:
            log(msg)
        on_done(ok)

    def rules_mode():
        state["cancel"] = True
        finish(False, "Local AI wording stays off (Rules mode)")

    def go():
        if state["busy"]:
            return
        state["busy"] = True
        yes.config(state="disabled")
        no.config(text="Cancel download")

        def progress(what, done, total):
            pct = f" {done * 100 // total}%" if total else ""
            win.after(0, lambda: status.winfo_exists() and status.config(text=f"Downloading the {what}...{pct}"))

        def fail(msg):
            messagebox.showerror("Local AI wording", f"Couldn't set it up:\n{msg}")
            finish(False, f"Local AI wording not enabled: {msg}")

        def work():
            try:
                flow.confirm(progress, cancel=lambda: state["cancel"])
            except Exception as e:      # a cancelled download is not an error to show
                msg = str(e)
                if not state["cancel"]:
                    win.after(0, lambda: fail(msg))
                return

            def ok():
                runtime_config.LLM_ENABLED = True
                runtime_config.save(LLM_ENABLED=True)
                finish(True, "Local AI wording on -- the coach's tips are reworded after each lap (rules text is always kept if the check fails)")
            win.after(0, ok)

        threading.Thread(target=work, daemon=True, name="trace-ai-download").start()

    yes = tk.Button(row, text="Download and turn on" if w.download_mb else "Turn on", command=go, bg=theme.RED, fg=theme.BG,
                    relief="flat", font=(theme.MONO, 9, "bold"), padx=12, pady=4, state="normal" if w.can_enable else "disabled")
    yes.pack(side="left")
    no = tk.Button(row, text="Switch back to Rules mode", command=rules_mode, bg=theme.BG3, fg=theme.TXT, relief="flat",
                   font=(theme.MONO, 9), padx=12, pady=4)
    no.pack(side="left", padx=8)
    win.protocol("WM_DELETE_WINDOW", rules_mode)
    win.grab_set()


def remove_model(parent, log):
    """Settings > Remove AI model: confirm, then delete the model and runtime and switch the feature off."""
    if not llm.llm_dir().exists():
        messagebox.showinfo("Local AI wording", "No AI model is installed.")
        return False
    if not messagebox.askyesno("Remove AI model", "Delete the downloaded AI model and runtime and go back to Rules mode?\n"
                               "You can download them again later."):
        return False
    llm.default_worder().close()
    ok = llm.remove_model()
    runtime_config.LLM_ENABLED = False
    runtime_config.save(LLM_ENABLED=False)
    log("AI model removed -- back to Rules mode" if ok else "AI model removed, but some files could not be deleted")
    return True
