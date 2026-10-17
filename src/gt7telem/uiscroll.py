"""Scrolling helpers for the Lap and Race Analyst windows: the left side panel, the
chart pane (both ways) and the chart tab row, so nothing is cut off on a small screen."""
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


def _wire_wheel(outer, cv, scrolls_y, scrolls_x):
    """Mouse wheel scrolls `cv` while the pointer is over `outer` (Shift+wheel
    goes sideways). The global binding is dropped again when the pointer leaves,
    so two scrollable panes never fight over the wheel."""
    def move(step, sideways):
        if sideways and scrolls_x():
            cv.xview_scroll(step * 2, "units")
        elif not sideways and scrolls_y():
            cv.yview_scroll(step * 2, "units")

    def wheel(e):
        move(-1 if e.delta > 0 else 1, bool(e.state & 0x1))

    def enter(_e):
        cv.bind_all("<MouseWheel>", wheel)
        cv.bind_all("<Button-4>", lambda e: move(-1, bool(e.state & 0x1)))     # Linux
        cv.bind_all("<Button-5>", lambda e: move(1, bool(e.state & 0x1)))

    def leave(e):
        try:
            w = outer.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            w = None
        if w is None or not str(w).startswith(str(outer)):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                cv.unbind_all(seq)

    outer.bind("<Enter>", enter)
    outer.bind("<Leave>", leave)


def scroll_view(parent, bg, min_w=0, min_h=0):
    """Fill `parent` with a pane that scrolls both ways when its content needs
    more room than the window gives it. Returns the inner frame to put the
    content in: it is at least min_w x min_h, and grows to fill the window when
    that is bigger -- so a chart keeps a readable size on a small screen
    instead of being cut off, and still stretches on a big one."""
    outer = tk.Frame(parent, bg=bg)
    outer.pack(fill="both", expand=True)
    hs = ttk.Scrollbar(outer, orient="horizontal")
    vs = ttk.Scrollbar(outer, orient="vertical")
    hs.pack(side="bottom", fill="x")
    vs.pack(side="right", fill="y")
    cv = tk.Canvas(outer, bg=bg, highlightthickness=0, xscrollincrement=30, yscrollincrement=30,
                   xscrollcommand=hs.set, yscrollcommand=vs.set)
    cv.pack(side="left", fill="both", expand=True)
    hs.configure(command=cv.xview)
    vs.configure(command=cv.yview)
    inner = tk.Frame(cv, bg=bg)
    win = cv.create_window((0, 0), window=inner, anchor="nw")
    size = {"w": min_w, "h": min_h}

    def fit(_e=None):
        w = max(min_w, cv.winfo_width())
        h = max(min_h, cv.winfo_height())
        size["w"], size["h"] = w, h
        cv.itemconfigure(win, width=w, height=h)
        cv.configure(scrollregion=(0, 0, w, h))

    cv.bind("<Configure>", fit)
    _wire_wheel(outer, cv, lambda: size["h"] > cv.winfo_height(), lambda: size["w"] > cv.winfo_width())
    return inner


def tab_strip(parent, nb, names, bg, tab_bg, fg, sel_bg, sel_fg, font):
    """A tab row that scrolls sideways, standing in for a ttk.Notebook's own tabs
    (which just get squeezed and cut off when there are many). `nb` must use the
    "Plain.TNotebook" style, which hides its own tab row, and already hold its
    pages in the same order as `names`. Returns the strip's outer frame."""
    ttk.Style().layout("Plain.TNotebook.Tab", [])
    wrap = tk.Frame(parent, bg=bg)
    wrap.pack(fill="x", before=nb)
    cv = tk.Canvas(wrap, bg=bg, highlightthickness=0, xscrollincrement=30)
    hs = ttk.Scrollbar(wrap, orient="horizontal", command=cv.xview)
    cv.configure(xscrollcommand=hs.set)
    inner = tk.Frame(cv, bg=bg)
    cv.create_window((0, 0), window=inner, anchor="nw")
    btns = []
    for i, name in enumerate(names):
        b = tk.Label(inner, text=f" {name} ", bg=tab_bg, fg=fg, font=font, padx=8, pady=5, cursor="hand2")
        b.pack(side="left", padx=(0, 1))
        b.bind("<Button-1>", lambda _e, i=i: nb.select(i))
        btns.append(b)
    inner.update_idletasks()
    cv.configure(height=inner.winfo_reqheight())
    cv.pack(side="top", fill="x")
    hs.pack(side="top", fill="x")
    inner.bind("<Configure>", lambda _e: cv.configure(scrollregion=(0, 0, inner.winfo_reqwidth(), inner.winfo_reqheight())))

    def sideways(e):
        if inner.winfo_reqwidth() > cv.winfo_width():
            cv.xview_scroll(-2 if e.delta > 0 else 2, "units")
    for w in [cv, inner, *btns]:
        w.bind("<MouseWheel>", sideways)

    def sync(_e=None):
        try:
            cur = nb.index("current")
        except tk.TclError:
            return
        for i, b in enumerate(btns):
            b.configure(bg=sel_bg if i == cur else tab_bg, fg=sel_fg if i == cur else fg)
        inner.update_idletasks()
        total = max(inner.winfo_reqwidth(), 1)
        left, right = btns[cur].winfo_x(), btns[cur].winfo_x() + btns[cur].winfo_width()
        v0, v1 = cv.xview()
        if left < v0 * total:
            cv.xview_moveto(left / total)
        elif right > v1 * total:
            cv.xview_moveto(max(0.0, (right - cv.winfo_width()) / total))

    nb.bind("<<NotebookTabChanged>>", sync, add="+")
    sync()
    return wrap
