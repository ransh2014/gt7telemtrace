"""records.py -- the Records table: your best lap for every car and track, built
from the lap files the Dashboard has saved. Opened from the Launcher menu.

Scanning stays cheap on big folders: the Dashboard writes each lap file's
summary fields *before* its (large) `samples` list, so only the first few KB of
a file are read and the samples are never parsed. Files in another order (hand
edited, exported) fall back to a full read. Parsed rows are cached by
(path, mtime), so reopening the table is instant. The scanner is pure and
tkinter-free; the window is built lazily in show_records().
"""

import json
import queue
import threading
from datetime import datetime
from pathlib import Path

from . import config, leaderboard

_HEAD_BYTES = 8192
_SKIP_DIRS = {"races", "_session"}
_CACHE: dict = {}   # (path, mtime_ns) -> header dict or None


def _fmt(t) -> str:
    """m:ss.mmm, rounded to the millisecond first so 59.9996 s reads 1:00.000."""
    total_ms = int(round(float(t) * 1000))
    m, ms = divmod(total_ms, 60000)
    return f"{m}:{ms / 1000:06.3f}"


def lap_header(path) -> dict | None:
    """The summary fields of a saved lap file (everything except `samples`), or
    None if the file isn't a TRACE lap. Reads only the start of the file when the
    summary comes before the samples, which is how the Dashboard writes it."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            head = f.read(_HEAD_BYTES)
            cut = head.find('"samples"')
            if cut > 0:
                body = head[:cut].rstrip()
                if body.endswith(","):
                    body = body[:-1]
                try:
                    data = json.loads(body + "}")
                except ValueError:
                    data = None
                if isinstance(data, dict) and "lap_time_s" in data:
                    return data
            data = json.loads(head + f.read())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("samples"), list) or "lap_time_s" not in data:
        return None
    return {k: v for k, v in data.items() if k != "samples"}


def _row(path: Path, mtime_ns: int) -> dict | None:
    key = (str(path), mtime_ns)
    if key in _CACHE:
        return _CACHE[key]
    h = lap_header(path)
    row = None
    if h is not None:
        try:
            t = float(h.get("lap_time_s") or 0)
        except (TypeError, ValueError):
            t = 0.0
        if t > 0:
            row = {
                "car": str(h.get("car_display") or h.get("car") or "Unknown car"),
                "track": str(h.get("track_display") or h.get("track") or "Unknown track"),
                "time_s": t,
                "recorded_at": str(h.get("recorded_at") or ""),
                "incomplete": bool(h.get("incomplete")),
                "current_era": leaderboard.is_current_era(h),
                "path": str(path),
            }
    _CACHE[key] = row
    return row


def best_by_car_track(laps, include_old: bool = False) -> list[dict]:
    """Group lap rows into one record per (car, track): the fastest complete lap,
    how many complete laps there are, and when the record was set. Incomplete
    laps never count. Laps from before the current GT7 physics era are left out
    unless `include_old` (their times aren't comparable). Sorted by track, then
    car. Pure, so it is tested without any files."""
    groups: dict = {}
    for lap in laps:
        if lap.get("incomplete") or lap.get("time_s", 0) <= 0:
            continue
        if not include_old and not lap.get("current_era", True):
            continue
        key = (lap["track"].casefold(), lap["car"].casefold())
        g = groups.setdefault(key, {"car": lap["car"], "track": lap["track"], "laps": 0, "best": None})
        g["laps"] += 1
        if g["best"] is None or lap["time_s"] < g["best"]["time_s"]:
            g["best"] = lap
    out = []
    for g in groups.values():
        best = g["best"]
        try:
            date = datetime.strptime(best["recorded_at"][:15], "%Y%m%d_%H%M%S").strftime("%Y-%m-%d")
        except ValueError:
            date = ""
        out.append({"car": g["car"], "track": g["track"], "time_s": best["time_s"], "time": _fmt(best["time_s"]),
                    "laps": g["laps"], "date": date, "path": best["path"]})
    return sorted(out, key=lambda r: (r["track"].casefold(), r["car"].casefold()))


def scan_laps(folder) -> list[dict]:
    """Every readable lap file under `folder` as a row (see best_by_car_track).
    reference_*.json and personal_bests.json are skipped: they are copies of laps
    that are already there, or index files."""
    rows = []
    folder = Path(folder)
    try:
        if not folder.is_dir():
            return rows
        files = list(folder.rglob("*.json"))
    except OSError:
        return rows
    for f in files:
        rel = f.relative_to(folder).parts
        if any(p in _SKIP_DIRS for p in rel[:-1]) or f.name == "personal_bests.json" \
                or f.name.startswith("reference_"):
            continue
        try:
            row = _row(f, f.stat().st_mtime_ns)
        except OSError:
            continue
        if row is not None:
            rows.append(row)
    return rows


def build_records(folder=None, include_old: bool = False) -> list[dict]:
    """The Records table for the laps folder (default: the configured one)."""
    return best_by_car_track(scan_laps(folder if folder is not None else config.load().get("LAPS_FOLDER")),
                             include_old)


def filter_records(records, text: str) -> list[dict]:
    """Rows whose car or track contains `text` (case-insensitive)."""
    q = (text or "").strip().casefold()
    return [r for r in records if not q or q in r["car"].casefold() or q in r["track"].casefold()]


# ── window ────────────────────────────────────────────────────────────────────
def show_records(parent, colors: dict):
    """Open the Records window over `parent`. `colors` holds the launcher's
    palette: bg, panel, fg, dim, accent. The scan runs on a worker thread so the
    window opens at once even on a big laps folder."""
    import tkinter as tk
    from tkinter import ttk

    win = tk.Toplevel(parent)
    win.title("TRACE - Records")
    win.configure(bg=colors["bg"])
    win.geometry("820x520")
    win.minsize(600, 360)
    mono = (config.MONO, 10)

    top = tk.Frame(win, bg=colors["bg"])
    top.pack(fill="x", padx=16, pady=(14, 6))
    tk.Label(top, text="Records", font=(config.MONO, 15, "bold"), fg=colors["accent"], bg=colors["bg"]).pack(side="left")
    status = tk.Label(top, text="Scanning your laps...", font=(config.MONO, 9), fg=colors["dim"], bg=colors["bg"])
    status.pack(side="right")

    bar = tk.Frame(win, bg=colors["bg"])
    bar.pack(fill="x", padx=16, pady=(0, 6))
    tk.Label(bar, text="Filter", font=(config.MONO, 9), fg=colors["dim"], bg=colors["bg"]).pack(side="left")
    q = tk.StringVar()
    entry = tk.Entry(bar, textvariable=q, bg=colors["panel"], fg=colors["fg"], insertbackground=colors["fg"],
                     relief="flat", font=mono, width=26)
    entry.pack(side="left", padx=8, ipady=3)
    old = tk.BooleanVar(value=False)
    chk = tk.Checkbutton(bar, text=f"include pre-{leaderboard.PHYSICS_ERA} laps", variable=old, bg=colors["bg"],
                         fg=colors["dim"], selectcolor=colors["panel"], activebackground=colors["bg"],
                         font=(config.MONO, 9))
    chk.pack(side="left", padx=8)

    style = ttk.Style(win)
    style.configure("Records.Treeview", background=colors["panel"], foreground=colors["fg"],
                    fieldbackground=colors["panel"], font=mono, rowheight=24, borderwidth=0)
    style.configure("Records.Treeview.Heading", background=colors["bg"], foreground=colors["accent"],
                    font=(config.MONO, 9, "bold"))
    cols = (("track", "Track", 230), ("car", "Car", 260), ("time", "Best lap", 100), ("laps", "Laps", 60),
            ("date", "Set on", 100))
    tree = ttk.Treeview(win, columns=[c[0] for c in cols], show="headings", style="Records.Treeview")
    for key, title, width in cols:
        tree.heading(key, text=title)
        tree.column(key, width=width, anchor="w" if key in ("track", "car") else "center")
    sb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y", padx=(0, 8), pady=(0, 12))
    tree.pack(fill="both", expand=True, padx=(16, 0), pady=(0, 12))

    state = {"records": [], "closed": False}
    inbox: queue.SimpleQueue = queue.SimpleQueue()

    def refill(*_):
        tree.delete(*tree.get_children())
        shown = filter_records(state["records"], q.get())
        for r in shown:
            tree.insert("", "end", values=(r["track"], r["car"], r["time"], r["laps"], r["date"]))
        total = len(state["records"])
        if not total:
            status.config(text="No saved laps yet -- record some in the Live Dashboard")
        else:
            status.config(text=f"{len(shown)} of {total} records")

    def scan():
        try:
            records = build_records(include_old=old.get())
        except Exception:
            records = []
        inbox.put(records)

    def start_scan(*_):
        status.config(text="Scanning your laps...")
        threading.Thread(target=scan, daemon=True).start()

    def poll():
        if state["closed"]:
            return
        try:
            while True:
                state["records"] = inbox.get_nowait()
                refill()
        except queue.Empty:
            pass
        win.after(120, poll)

    def on_close():
        state["closed"] = True
        win.destroy()

    q.trace_add("write", refill)
    chk.config(command=start_scan)
    win.protocol("WM_DELETE_WINDOW", on_close)
    start_scan()
    win.after(120, poll)
    return win
