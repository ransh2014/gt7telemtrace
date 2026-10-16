"""A left side panel that scrolls when the window is too short to show it all."""
import tkinter as tk
from tkinter import ttk


def make_scrollable(parent, bg, width):
    """Pack a fixed-width, vertically scrollable panel on the left of `parent`
    and return the inner frame to build the panel's widgets in. A scrollbar and
    the mouse wheel (while the pointer is over the panel) scroll it."""
    outer = tk.Frame(parent, bg=bg, width=width)
    outer.pack(side="left", fill="y")
    outer.pack_propagate(False)
    cv = tk.Canvas(outer, bg=bg, highlightthickness=0, width=width - 16)
    sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
    cv.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    cv.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(cv, bg=bg)
    win = cv.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
    cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))

    def wheel(e):
        if inner.winfo_reqheight() > cv.winfo_height():
            cv.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def leave(e):
        try:
            w = outer.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            w = None
        if w is None or not str(w).startswith(str(outer)):
            cv.unbind_all("<MouseWheel>")

    outer.bind("<Enter>", lambda e: cv.bind_all("<MouseWheel>", wheel))
    outer.bind("<Leave>", leave)
    return inner
