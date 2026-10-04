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

import queue
import re
import threading
import tkinter as tk
import webbrowser

from . import __version__, analytics, auth, config, lap_analyst, race_analyst

# Static imports so PyInstaller's analysis bundles these (and their deps:
# numpy, pandas, matplotlib, pycryptodome) -- do NOT switch these back to
# dynamic __import__() calls, PyInstaller can't see those.
from . import dashboard as gt7telem

config.install_mac_buttons()   # macOS ignores Button colours; no-op elsewhere

# ── Theme (matches the analysis tools) ──────────────────────────────────
if config.THEME == "light":
    BG     = "#f4f5fa"
    PANEL  = "#ffffff"
    PANEL2 = "#eceef5"
    CYN    = "#0088aa"
    PRP    = "#6a3fd6"
    PINK   = "#c72d5c"
    FG     = "#14162a"
    DIM    = "#7a7f9a"
    DIM2   = "#d7dae6"
else:
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

_LATEST_RELEASE_URL = None  # set by _check_for_update() if a newer tag exists
_UPDATE_TAG = None          # ... together with the tag name; the Tk thread shows the banner
_banner = None
_uiq = queue.SimpleQueue()  # worker threads -> Tk thread (root.after() isn't thread-safe)


def _post(fn, *args):
    _uiq.put((fn, args))


def _poll_ui():
    try:
        while True:
            try:
                fn, args = _uiq.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:
                pass
        # (re)show the update banner: swapping screens destroys it
        if _UPDATE_TAG and _content is not None and not (
                _banner is not None and _banner.winfo_exists() and _banner.master is _content):
            _show_update_banner(_UPDATE_TAG)
    finally:
        if root is not None:
            try:
                root.after(150, _poll_ui)
            except tk.TclError:
                pass


def _ver_tuple(s):
    """'v0.7.10' -> (0, 7, 10); ignores a '-rc1' / '+build' suffix. Compared
    as ints (not strings), and padded so 0.8 == 0.8.0."""
    core = re.split(r"[-+]", str(s).strip().lstrip("vV"), maxsplit=1)[0]
    nums = [int(n) for n in re.findall(r"\d+", core)]
    return tuple(nums + [0] * (4 - len(nums)))


def _check_for_update():
    """Background: compare the installed version against GitHub's latest
    release tag. Silent on any failure (offline, rate-limited, API shape
    change) -- an update nag is a nice-to-have, never worth an error dialog.
    Runs once per launch, only if the user hasn't turned it off."""
    if not config.UPDATE_CHECK_ENABLED:
        return

    def worker():
        global _LATEST_RELEASE_URL, _UPDATE_TAG
        try:
            import json as _json
            import urllib.request
            req = urllib.request.Request(
                "https://api.github.com/repos/ransh2014/gt7telemtrace/releases/latest",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "TRACE-updater"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            tag = str(data.get("tag_name", "")).lstrip("vV")
            if not tag:
                return
            if _ver_tuple(tag) > _ver_tuple(__version__):
                _LATEST_RELEASE_URL = data.get("html_url") or \
                    "https://github.com/ransh2014/gt7telemtrace/releases/latest"
                _UPDATE_TAG = tag      # picked up by _poll_ui on the Tk thread
        except Exception:
            pass  # offline / rate-limited / API changed -- fail silent

    threading.Thread(target=worker, daemon=True).start()


def _show_update_banner(new_version: str):
    global _banner
    if _content is None:
        return
    bar = tk.Label(
        _content, text=f"  🔔  TRACE {new_version} is available — click to open the release  ",
        font=("Segoe UI", 9, "bold"), bg=PINK, fg="#ffffff", cursor="hand2")
    bar.place(relx=0.5, rely=0.0, anchor="n")
    bar.lift()
    bar.bind("<Button-1>", lambda e: webbrowser.open(_LATEST_RELEASE_URL))
    _banner = bar

root = None
_content = None   # the frame currently swapped into root (onboarding or menu)


def _center(win, w, h):
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


def _launch(tool, app_cls):
    analytics.track_launch(tool)
    root.destroy()
    try:
        app = app_cls()
    except Exception as e:
        # The menu window is already gone -- say why, then come back to the menu
        # instead of exiting silently.
        from tkinter import messagebox
        tmp = tk.Tk()
        tmp.withdraw()
        try:
            messagebox.showerror("TRACE", f"Couldn't start {tool.replace('_', ' ')}:\n{type(e).__name__}: {e}",
                                 parent=tmp)
        finally:
            tmp.destroy()
        main()
        return
    app.mainloop()


def launch_dashboard():
    _launch("dashboard", gt7telem.App)


def launch_lap_analyst():
    _launch("lap_analyst", lap_analyst.AnalystApp)


def launch_race_analyst():
    _launch("race_analyst", race_analyst.AnalystApp)


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
    reopened later via the "Create Free Account" link on the tool menu
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
        # <Return> on the entry calls this directly, bypassing the disabled
        # button -- without this guard, pressing Enter again while a sign-up
        # was in flight created a second (third, ...) anonymous account.
        if str(create_btn.cget("state")) == "disabled":
            return
        name = name_var.get().strip()
        if not name:
            status.config(text="Enter a display name first.")
            return
        set_busy(True)
        status.config(text="Creating account…", fg=DIM)

        def worker():
            session = None
            name_saved = False
            try:
                session = auth.sign_up_anonymous()
                if session:
                    name_saved = auth.set_display_name(session["access_token"], session["user_id"], name)
            except Exception:
                session = None
            _post(after_create, session, name, name_saved)

        threading.Thread(target=worker, daemon=True).start()

    def after_create(session, name, name_saved=True):
        set_busy(False)
        if not session:
            why = auth.last_signup_error or ""
            if why == "certs":
                msg = ("Couldn't verify the server's security certificate — your system may be "
                       "missing CA certificates (install the 'ca-certificates' package). Or skip for now.")
            elif why.startswith("http:"):
                msg = f"The server had a problem ({why[5:]}) — try again later, or skip for now."
            else:
                msg = "Couldn't reach the server — check your connection and try again, or skip for now."
            status.config(text=msg, fg=PINK)
            return
        config.SUPABASE_ACCESS_TOKEN = session["access_token"]
        config.SUPABASE_REFRESH_TOKEN = session["refresh_token"]
        config.SUPABASE_USER_ID = session["user_id"]
        config.PSN_NAME = name
        config.ONBOARDING_DONE = True
        persisted = config.save(
            SUPABASE_ACCESS_TOKEN=config.SUPABASE_ACCESS_TOKEN,
            SUPABASE_REFRESH_TOKEN=config.SUPABASE_REFRESH_TOKEN,
            SUPABASE_USER_ID=config.SUPABASE_USER_ID,
            PSN_NAME=config.PSN_NAME,
            ONBOARDING_DONE=True,
        )
        if not persisted:
            # Don't claim success: a locked / unwritable settings.json means the
            # account is lost on the next launch (and can't be recovered).
            status.config(
                text="Account created, but TRACE couldn't write its settings file, so "
                     "you'll be signed out next launch. Check that the settings folder "
                     "isn't read-only or locked.", fg=PINK)
            root.after(4500, _show_menu)
            return
        if name_saved:
            _show_menu()
            return
        # Account/session creation itself worked -- only the display-name
        # write failed (network). Say so instead of silently looking done;
        # the account still works, submissions use the name typed at
        # submit time either way.
        status.config(
            text="Account created — the display name didn't save (network "
                 "hiccup). Doesn't matter: leaderboard rows use the name you "
                 "type when submitting.", fg=PINK)
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

    theme_lbl = tk.Label(
        foot, text=("☀  Light" if config.THEME == "dark" else "🌙  Dark"),
        font=("Segoe UI", 9, "underline"), fg=DIM, bg=BG, cursor="hand2")
    theme_lbl.pack(side="right", padx=(0, 24))
    theme_lbl.bind("<Button-1>", lambda e: _toggle_theme())

    backup_lbl = tk.Label(foot, text="🗄  Backup Laps",
                           font=("Segoe UI", 9, "underline"), fg=DIM, bg=BG,
                           cursor="hand2")
    backup_lbl.pack(side="right", padx=(0, 24))
    backup_lbl.bind("<Button-1>", lambda e: _do_backup_laps())

    if not config.SUPABASE_ACCESS_TOKEN:
        link = tk.Label(foot, text="Create Free Account",
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


def _toggle_theme():
    """Flips the saved theme and asks the user to relaunch -- the color
    constants above are module-level and used throughout every widget in
    this file (and lap_analyst/race_analyst), so re-skinning live without
    rebuilding every open window isn't worth the risk. Dashboard keeps its
    own fixed dark palette regardless (its colors are inline, not themed)."""
    new_theme = "light" if config.THEME == "dark" else "dark"
    config.THEME = new_theme
    config.save(THEME=new_theme)
    from tkinter import messagebox
    messagebox.showinfo(
        "Theme",
        f"Switched to {new_theme} mode. Close and reopen TRACE (or just "
        f"the tool you launch next) to see it.", parent=root)


def _do_backup_laps():
    from tkinter import messagebox
    def work():
        try:
            path = config.backup_laps()
        except Exception as e:
            path = None
            config.last_backup_error = f"{type(e).__name__}: {e}"
        _post(done, path, getattr(config, "last_backup_error", None))

    def done(path, err):
        if path is None and err:
            messagebox.showerror("Backup Laps", f"Backup failed:\n{err}", parent=root)
        elif path is None:
            messagebox.showinfo("Backup Laps", "No laps found to back up yet.", parent=root)
        else:
            messagebox.showinfo("Backup Laps", f"Saved to:\n{path}", parent=root)

    threading.Thread(target=work, daemon=True).start()


def _do_logout():
    """Clear the local Supabase session so the account menu shows signed-out
    state again. PSN_NAME is left alone so it still pre-fills next time.

    Accounts are anonymous (no email/password), so there is no way to sign
    back into one after logging out -- the laps already submitted stay on
    the leaderboard but can't be edited or deleted from here any more.
    Warn before doing it."""
    from tkinter import messagebox
    if not messagebox.askyesno(
            "Log out?",
            "TRACE accounts have no email or password, so once you log out "
            "you can't sign back into this one.\n\n"
            "Laps you've already submitted stay on the leaderboard, but you "
            "won't be able to manage them. You can create a new free account "
            "any time.\n\nLog out anyway?", parent=root):
        return
    config.SUPABASE_ACCESS_TOKEN = ""
    config.SUPABASE_REFRESH_TOKEN = ""
    config.SUPABASE_USER_ID = ""
    if not config.save(
        SUPABASE_ACCESS_TOKEN="",
        SUPABASE_REFRESH_TOKEN="",
        SUPABASE_USER_ID="",
    ):
        messagebox.showwarning(
            "Log out", "Logged out for this session, but TRACE couldn't update its "
            "settings file -- you may be signed back in next launch.", parent=root)
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
    root.title(f"TRACE {__version__} - GT7 Telemetry Suite")
    root.configure(bg=BG)
    root.resizable(True, True)
    root.minsize(760, 560)
    _center(root, WIN_W, WIN_H)

    if config.ONBOARDING_DONE:
        _show_menu()
    else:
        _show_onboarding()

    root.after(150, _poll_ui)
    if config.UPDATE_CHECK_ENABLED:
        _check_for_update()

    root.mainloop()


if __name__ == "__main__":
    main()
