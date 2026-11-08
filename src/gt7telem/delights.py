"""delights.py -- small, optional touches for the desktop windows: hover tooltips, the lights-out sequence (five clicks
on the Launcher title) and a checkered flag.

Nothing here changes what TRACE records or sends: it is all local drawing. Imports tkinter, so only the GUI modules
import it (the headless modules and tests never do).
"""
from __future__ import annotations

import tkinter as tk

from . import theme

_OFF = "#1a1a1d"   # an unlit lamp (the website's gantry colour)

# ── Tooltips ───────────────────────────────────────────────────────────────────────────────────────────────
def tooltip(widget, text: str, delay_ms: int = 450) -> None:
    """A small hover help bubble in the TRACE look. Appears after `delay_ms`, goes away on leave or click."""
    state = {"win": None, "job": None}

    def hide(_e=None):
        try:
            if state["job"] is not None:
                widget.after_cancel(state["job"])
        except tk.TclError:
            pass
        state["job"] = None
        if state["win"] is not None:
            try:
                state["win"].destroy()
            except tk.TclError:
                pass
            state["win"] = None

    def show():
        state["job"] = None
        try:
            if state["win"] is not None or not widget.winfo_exists():
                return
            win = tk.Toplevel(widget)
            win.overrideredirect(True)
            try:
                win.attributes("-topmost", True)
            except tk.TclError:
                pass
            frame = tk.Frame(win, bg=theme.BG2, highlightthickness=1,
                             highlightbackground=theme.LINE2, highlightcolor=theme.LINE2)
            frame.pack()
            tk.Label(frame, text=text, bg=theme.BG2, fg=theme.TXT, font=(theme.MONO, 8), justify="left",
                     wraplength=theme.px(300), padx=8, pady=5).pack()
            win.update_idletasks()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x = max(0, min(widget.winfo_rootx() + 8, sw - win.winfo_reqwidth() - 4))
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            if y + win.winfo_reqheight() > sh - theme.px(40):
                y = max(0, widget.winfo_rooty() - win.winfo_reqheight() - 6)
            win.geometry(f"+{x}+{y}")
            state["win"] = win
        except tk.TclError:
            state["win"] = None

    def schedule(_e=None):
        hide()
        try:
            state["job"] = widget.after(delay_ms, show)
        except tk.TclError:
            pass

    widget.bind("<Enter>", schedule, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<ButtonPress>", hide, add="+")
    widget.bind("<Destroy>", hide, add="+")


# ── Hidden bits ────────────────────────────────────────────────────────────────────────────────────────────
def click_counter(widget, count: int, within_ms: int, callback) -> None:
    """Call `callback` after `count` clicks on `widget`, each within `within_ms` of the previous one."""
    state = {"n": 0, "job": None}

    def reset():
        state["n"] = 0
        state["job"] = None

    def click(_e=None):
        try:
            if state["job"] is not None:
                widget.after_cancel(state["job"])
            state["n"] += 1
            if state["n"] >= count:
                state["n"] = 0
                state["job"] = None
                callback()
            else:
                state["job"] = widget.after(within_ms, reset)
        except tk.TclError:
            pass

    widget.bind("<Button-1>", click, add="+")


def _centred_popup(root, w: int, h: int):
    """A borderless popup centred on `root` (hidden until the caller shows it)."""
    top = tk.Toplevel(root)
    top.withdraw()
    top.overrideredirect(True)
    top.configure(bg=theme.LINE2)
    try:
        top.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        root.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + (root.winfo_height() - h) // 2
    except tk.TclError:
        x = y = 100
    top.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
    return top


def lights_out(root, on_done=None) -> None:
    """Five red lights come on one by one, then go out: LIGHTS OUT. Click or press a key to skip."""
    w, h = theme.px(440), theme.px(210)
    top = _centred_popup(root, w, h)
    inner = tk.Frame(top, bg=theme.BG1)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    cv = tk.Canvas(inner, width=w - 2, height=h - 2, bg=theme.BG1, highlightthickness=0)
    cv.pack()
    r, gap = theme.px(26), theme.px(18)
    total = 5 * 2 * r + 4 * gap
    x0 = ((w - 2) - total) // 2 + r
    y = theme.px(82)
    lamps = [cv.create_oval(x0 + i * (2 * r + gap) - r, y - r, x0 + i * (2 * r + gap) + r, y + r,
                            fill=_OFF, outline=theme.LINE2, width=1) for i in range(5)]
    label = cv.create_text((w - 2) // 2, theme.px(158), text="", fill=theme.GREEN, font=(theme.IMPACT, 26))
    cv.create_text((w - 2) // 2, h - theme.px(18), text="click or press a key to skip", fill=theme.DIM,
                   font=(theme.MONO, 7))
    jobs: list = []
    done = {"v": False}

    def end(_e=None):
        if done["v"]:
            return
        done["v"] = True
        for j in jobs:
            try:
                top.after_cancel(j)
            except tk.TclError:
                pass
        try:
            top.destroy()
        except tk.TclError:
            pass
        if on_done:
            on_done()

    def light(i):
        if not done["v"]:
            cv.itemconfig(lamps[i], fill=theme.RED, outline=theme.RED_2)

    def lights_off():
        if done["v"]:
            return
        for c in lamps:
            cv.itemconfig(c, fill=_OFF, outline=theme.LINE2)
        cv.itemconfig(label, text="LIGHTS OUT")

    for i in range(5):
        jobs.append(top.after(400 + 420 * i, light, i))
    t_off = 400 + 420 * 4 + 900
    jobs.append(top.after(t_off, lights_off))
    jobs.append(top.after(t_off + 1000, end))
    for wdg in (top, cv):
        wdg.bind("<Button-1>", end)
        wdg.bind("<Key>", end)
    top.deiconify()
    top.lift()
    try:
        top.focus_force()
    except tk.TclError:
        pass


# ── Checkered flag ─────────────────────────────────────────────────────────────────────────────────────────
def checker_flag(parent, bg: str, cols: int = 5, rows: int = 3, cell: int = 7):
    """A tiny checkered flag on a Canvas (drawn, so it needs no emoji font)."""
    cell = theme.px(cell)
    cv = tk.Canvas(parent, width=cols * cell, height=rows * cell, bg=bg, highlightthickness=0)
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                cv.create_rectangle(c * cell, r * cell, (c + 1) * cell, (r + 1) * cell, fill=theme.TXT, outline="")
    return cv
