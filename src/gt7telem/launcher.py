"""
launcher.py — TRACE unified entry point.
First launch shows a one-time onboarding screen (create a free anonymous
account, or skip); every launch after that goes straight to the tool menu.
Picking a tool launches it as its own window -- closing that tool closes
the whole app (re-open the exe to pick a different tool).
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

import threading
import tkinter as tk

from . import analytics, auth, config, lap_analyst, race_analyst

# Static imports so PyInstaller's analysis bundles these (and their deps:
# numpy, pandas, matplotlib, pycryptodome) -- do NOT switch these back to
# dynamic __import__() calls, PyInstaller can't see those.
from . import dashboard as gt7telem

# ── Theme (matches the analysis tools) ──────────────────────────────────
BG     = "#0a0e1a"
PANEL  = "#10152a"
PANEL2 = "#161c36"
CYN    = "#00e5ff"
PRP    = "#7c5cff"
PINK   = "#ff5c8a"
FG     = "#e8eaf6"
DIM    = "#6b7290"
DIM2   = "#333a5c"

WIN_W, WIN_H = 900, 650

root = None
_content = None   # the frame currently swapped into root (onboarding or menu)


def _center(win, w, h):
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


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


def _swap_content() -> tk.Frame:
    """Destroy whatever's currently shown and return a fresh empty frame to
    build the next screen into -- used to move between onboarding and the
    tool menu without opening a second window."""
    global _content
    if _content is not None:
        _content.destroy()
    _content = tk.Frame(root, bg=BG)
    _content.place(x=0, y=0, relwidth=1, relheight=1)
    return _content


# ── onboarding ───────────────────────────────────────────────────────────

def _show_onboarding(from_menu: bool = False):
    """The one-time first-launch screen. `from_menu=True` means it was
    reopened later via the "Sign In / Create Account" link on the tool menu
    (ONBOARDING_DONE is already True in that case) -- Skip/Back should just
    return to the menu rather than touching that flag again."""
    f = _swap_content()

    card = tk.Frame(f, bg=BG)
    card.place(relx=0.5, rely=0.46, anchor="center")

    tk.Label(card, text="TRACE", font=("Segoe UI", 34, "bold"),
              fg=CYN, bg=BG).pack()
    tk.Label(card, text="GT7 Telemetry Suite", font=("Segoe UI", 11),
              fg=DIM, bg=BG).pack(pady=(0, 24))

    pitch = ("Want to submit laps to the global leaderboard?\n"
             "Create a free account — just a display name,\n"
             "no email or password needed.")
    tk.Label(card, text=pitch, font=("Segoe UI", 10), fg=FG, bg=BG,
              justify="center").pack(pady=(0, 18))

    name_var = tk.StringVar(value=config.PSN_NAME)
    entry = tk.Entry(card, textvariable=name_var, font=("Segoe UI", 12),
                      width=26, bg=PANEL2, fg=FG, insertbackground=CYN,
                      relief="flat", justify="center")
    entry.pack(ipady=7, pady=(0, 12))
    entry.focus_set()
    entry.bind("<Return>", lambda e: do_create())

    status = tk.Label(card, text="", font=("Segoe UI", 9), bg=BG, fg=PINK,
                       wraplength=340, justify="center")
    status.pack(pady=(0, 6))

    btn_row = tk.Frame(card, bg=BG)
    btn_row.pack(pady=(8, 0))

    create_btn = tk.Button(
        btn_row, text="Create Account", font=("Segoe UI", 11, "bold"),
        bg=CYN, activebackground="#33ecff", fg=BG, activeforeground=BG,
        bd=0, width=18, height=2, cursor="hand2")
    create_btn.pack(side="left", padx=6)

    skip_btn = tk.Button(
        btn_row, text=("Back" if from_menu else "Skip for now"),
        font=("Segoe UI", 11), bg=PANEL2, fg=DIM, activebackground=PANEL2,
        activeforeground=FG, bd=0, width=14, height=2, cursor="hand2")
    skip_btn.pack(side="left", padx=6)

    tk.Label(
        card,
        text=("Skipping still lets you view the leaderboard and download\n"
              "ghost laps — only submitting a lap needs an account, and\n"
              "you can always sign up later from the tool menu."),
        font=("Segoe UI", 8), fg=DIM, bg=BG, justify="center",
    ).pack(pady=(20, 0))

    def set_busy(busy: bool):
        state = "disabled" if busy else "normal"
        create_btn.config(state=state)
        skip_btn.config(state=state)

    def do_skip():
        if not from_menu:
            config.ONBOARDING_DONE = True
            config.save(ONBOARDING_DONE=True)
        _show_menu()

    def do_create():
        name = name_var.get().strip()
        if not name:
            status.config(text="Enter a display name first.")
            return
        set_busy(True)
        status.config(text="Creating account…", fg=DIM)

        def worker():
            session = auth.sign_up_anonymous()
            name_saved = False
            if session:
                name_saved = auth.set_display_name(session["access_token"], session["user_id"], name)
            root.after(0, lambda: after_create(session, name, name_saved))

        threading.Thread(target=worker, daemon=True).start()

    def after_create(session, name, name_saved=True):
        set_busy(False)
        if not session:
            status.config(
                text="Couldn't reach the server — check your connection "
                     "and try again, or skip for now.", fg=PINK)
            return
        config.SUPABASE_ACCESS_TOKEN = session["access_token"]
        config.SUPABASE_REFRESH_TOKEN = session["refresh_token"]
        config.SUPABASE_USER_ID = session["user_id"]
        config.PSN_NAME = name
        config.ONBOARDING_DONE = True
        config.save(
            SUPABASE_ACCESS_TOKEN=config.SUPABASE_ACCESS_TOKEN,
            SUPABASE_REFRESH_TOKEN=config.SUPABASE_REFRESH_TOKEN,
            SUPABASE_USER_ID=config.SUPABASE_USER_ID,
            PSN_NAME=config.PSN_NAME,
            ONBOARDING_DONE=True,
        )
        if name_saved:
            _show_menu()
            return
        # Account/session creation itself worked -- only the display-name
        # sync failed (a known upstream Supabase auth issue). Say so
        # honestly instead of silently swallowing it and looking done;
        # the account still works fine, submissions just use whatever
        # name you type at submit time either way.
        status.config(
            text="Account created — display name didn't sync to the "
                 "server (known Supabase issue on their end). Doesn't "
                 "affect using TRACE.", fg=PINK)
        root.after(2200, _show_menu)

    create_btn.config(command=do_create)
    skip_btn.config(command=do_skip)


# ── tool menu ────────────────────────────────────────────────────────────

_TOOLS = [
    ("Live Dashboard", "Real-time telemetry overlay while you drive.", CYN, launch_dashboard),
    ("Lap Analyst", "Compare laps, chart every input, submit to the leaderboard.", PRP, launch_lap_analyst),
    ("Race Analyst", "Full race breakdown, incidents, and pace review.", PINK, launch_race_analyst),
]


def _show_menu():
    f = _swap_content()

    hdr = tk.Frame(f, bg=BG)
    hdr.pack(fill="x", pady=(50, 8))
    tk.Label(hdr, text="TRACE", font=("Segoe UI", 30, "bold"),
              fg=CYN, bg=BG).pack()
    tk.Label(hdr, text="GT7 Telemetry Suite", font=("Segoe UI", 11),
              fg=DIM, bg=BG).pack(pady=(0, 4))
    if config.SUPABASE_ACCESS_TOKEN and config.PSN_NAME:
        tk.Label(hdr, text=f"signed in as {config.PSN_NAME}",
                  font=("Segoe UI", 9), fg=DIM, bg=BG).pack()

    cards = tk.Frame(f, bg=BG)
    cards.pack(expand=True)

    for title, desc, color, cmd in _TOOLS:
        _build_card(cards, title, desc, color, cmd)

    foot = tk.Frame(f, bg=BG)
    foot.pack(side="bottom", fill="x", pady=16)

    tk.Label(foot, text="community tool - not affiliated with Polyphony Digital",
              font=("Segoe UI", 8), fg=DIM2, bg=BG).pack(side="left", padx=24)

    if not config.SUPABASE_ACCESS_TOKEN:
        link = tk.Label(foot, text="Sign In / Create Account",
                         font=("Segoe UI", 9, "underline"), fg=CYN, bg=BG,
                         cursor="hand2")
        link.pack(side="right", padx=24)
        link.bind("<Button-1>", lambda e: _show_onboarding(from_menu=True))
    else:
        logout_link = tk.Label(foot, text="Log Out",
                         font=("Segoe UI", 9, "underline"), fg=DIM, bg=BG,
                         cursor="hand2")
        logout_link.pack(side="right", padx=24)
        logout_link.bind("<Button-1>", lambda e: _do_logout())


def _do_logout():
    """Clear the local Supabase session so the account menu shows signed-out
    state again. This only forgets the session on this machine -- it does
    not delete the account itself. PSN_NAME is left alone so it still
    pre-fills next time this or another account signs in."""
    config.SUPABASE_ACCESS_TOKEN = ""
    config.SUPABASE_REFRESH_TOKEN = ""
    config.SUPABASE_USER_ID = ""
    config.save(
        SUPABASE_ACCESS_TOKEN="",
        SUPABASE_REFRESH_TOKEN="",
        SUPABASE_USER_ID="",
    )
    _show_menu()


def _build_card(parent, title, desc, color, cmd):
    card = tk.Frame(parent, bg=PANEL, width=250, height=300, cursor="hand2",
                     highlightthickness=1, highlightbackground=DIM2)
    card.pack(side="left", padx=16, pady=10)
    card.pack_propagate(False)

    tk.Frame(card, bg=color, height=4).pack(fill="x", side="top")

    body = tk.Frame(card, bg=PANEL)
    body.pack(fill="both", expand=True, padx=20, pady=20)

    tk.Label(body, text=title, font=("Segoe UI", 15, "bold"), fg=color,
              bg=PANEL, wraplength=200, justify="left").pack(anchor="w", pady=(4, 10))
    tk.Label(body, text=desc, font=("Segoe UI", 9), fg=DIM, bg=PANEL,
              wraplength=190, justify="left").pack(anchor="w")

    launch_lbl = tk.Label(body, text="Launch  →", font=("Segoe UI", 10, "bold"),
                           fg=color, bg=PANEL)
    launch_lbl.pack(anchor="w", side="bottom")

    clickable = [card, body, launch_lbl] + body.winfo_children()

    def on_enter(_e=None):
        card.config(highlightbackground=color, highlightthickness=2)

    def on_leave(_e=None):
        card.config(highlightbackground=DIM2, highlightthickness=1)

    for w in clickable:
        w.bind("<Button-1>", lambda e: cmd())
        w.bind("<Enter>", on_enter)
        w.bind("<Leave>", on_leave)


def main():
    global root
    root = tk.Tk()
    root.title("TRACE - GT7 Telemetry Suite")
    root.configure(bg=BG)
    root.resizable(True, True)
    root.minsize(760, 560)
    _center(root, WIN_W, WIN_H)

    if config.ONBOARDING_DONE:
        _show_menu()
    else:
        _show_onboarding()

    root.mainloop()


if __name__ == "__main__":
    main()
