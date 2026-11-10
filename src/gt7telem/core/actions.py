"""The things you can *do* with a loaded lap or race, without a window: save notes, export CSV / a chart page / a share
card, load the community line, submit to the leaderboard, set a car's weight, back up and de-duplicate laps.

The Tk analysts and the terminal UI both call these, so the behaviour (and the files written) is identical.
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

from .. import auth, dyno, lapclean, leaderboard
from .. import config as runtime_config

# ── notes ────────────────────────────────────────────────────────────────────────────────────────────────────────


def save_notes(path, notes: str) -> bool:
    """Write notes into a lap file under "notes" through a temp file, so a failed write can't corrupt the lap."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        data["notes"] = (notes or "").strip()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# ── exports ──────────────────────────────────────────────────────────────────────────────────────────────────────
def chart_html(png_bytes: bytes, title: str) -> str:
    """The standalone page the 'Export Chart as HTML' button writes around a chart image."""
    b64 = base64.b64encode(png_bytes).decode()
    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        f"<title>TRACE Lap Analyst — {title}</title>"
        "<style>body{background:#07080f;display:flex;justify-content:center;"
        "align-items:flex-start;min-height:100vh;margin:0;padding:20px;box-sizing:border-box}"
        "img{max-width:100%;border-radius:8px;box-shadow:0 0 30px #00f0d444}"
        "h3{color:#00f0d4;font-family:monospace;text-align:center}</style></head>"
        f"<body><div><h3>TRACE Lap Analyst — {title}</h3>"
        f"<img src='data:image/png;base64,{b64}'></div></body></html>"
    )


def chart_png(kind: str, name: str, df, dfb=None, **ctx) -> bytes:
    """Render a chart group to PNG bytes (same figure the window shows)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from .. import lap_charts, race_charts
    from . import registry
    mod = lap_charts if kind == "lap" else race_charts
    fn, size = registry.lap_group(name) if kind == "lap" else registry.race_group(name)
    fig = Figure(figsize=size, facecolor=mod.BG)
    FigureCanvasAgg(fig)
    if kind == "lap" and name == "Consensus":
        fn(fig, df, dfb, ctx.get("consensus"))
    elif kind == "lap" and name == "Theoretical":
        fn(fig, df, dfb, ctx.get("pool"))
    elif kind == "lap" and name == "Inputs" and ctx.get("extra"):
        fn(fig, df, dfb, ctx["extra"])
    elif kind == "race" and name == "Corners":
        fn(fig, df, dfb, ctx.get("baseline"))
    else:
        fn(fig, df, dfb)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=mod.BG)
    fig.clear()
    return buf.getvalue()


def export_chart_html(path, kind, name, df, dfb=None, **ctx):
    Path(path).write_text(chart_html(chart_png(kind, name, df, dfb, **ctx), name), encoding="utf-8")
    return Path(path)


def export_csv(df, path):
    from .. import lap_charts
    lap_charts.export_csv(df, path)
    return Path(path)


def export_lap_card(data, df, path):
    from .. import lap_charts
    Path(path).write_bytes(lap_charts.lap_card_png(data, df))
    return Path(path)


def export_race_card(data, df, path):
    from .. import race_charts
    Path(path).write_bytes(race_charts.race_card_png(data, df))
    return Path(path)


# ── leaderboard ──────────────────────────────────────────────────────────────────────────────────────────────────
def consensus_rows(car: str, track: str, n: int = 10) -> list:
    try:
        return leaderboard.get_consensus_line(car, track, n=n) or []
    except Exception:
        return []


def signed_in() -> bool:
    return bool(runtime_config.SUPABASE_ACCESS_TOKEN)


def create_account(display_name: str) -> tuple[bool, str]:
    """The same anonymous sign-up the launcher's onboarding does. Returns (ok, message)."""
    name = " ".join((display_name or "").split())[:32]
    if not name:
        return False, "Enter a display name first."
    session = auth.sign_up_anonymous()
    if not session:
        return False, "Couldn't create the account (no connection?)."
    auth.set_display_name(session["access_token"], session["user_id"], name)
    runtime_config.SUPABASE_ACCESS_TOKEN = session["access_token"]
    runtime_config.SUPABASE_REFRESH_TOKEN = session["refresh_token"]
    runtime_config.SUPABASE_USER_ID = session["user_id"]
    runtime_config.PSN_NAME = name
    runtime_config.ONBOARDING_DONE = True
    runtime_config.save(SUPABASE_ACCESS_TOKEN=session["access_token"], SUPABASE_REFRESH_TOKEN=session["refresh_token"],
                        SUPABASE_USER_ID=session["user_id"], PSN_NAME=name, ONBOARDING_DONE=True)
    return True, "Account created."


def submission_problem(data: dict) -> str | None:
    return leaderboard.lap_submission_error(data)


def submit_lap(data: dict, psn: str) -> tuple[bool, str]:
    """Submit Lap A to the public leaderboard, refreshing an expired session once. Returns (ok, reason) where reason
    is "" on success, else "session", "data", "server" or "network" (the Tk window words them the same way)."""
    if leaderboard.lap_submission_error(data):        # never send a lap the leaderboard itself would call unusable
        return False, "data"
    car = data.get("car_display") or data.get("car", "?")
    track = data.get("track_display") or data.get("track", "?")
    try:
        lap_time_ms = int(round(float(data.get("lap_time_s") or 0) * 1000))
    except (TypeError, ValueError):
        return False, "data"
    psn = " ".join((psn or "").split())[:32]
    if not psn:
        return False, "data"
    runtime_config.PSN_NAME = psn
    runtime_config.save(PSN_NAME=psn)
    try:
        ok, reason = leaderboard.submit_lap(car, track, lap_time_ms, psn, data["samples"],
                                            access_token=runtime_config.SUPABASE_ACCESS_TOKEN,
                                            user_id=runtime_config.SUPABASE_USER_ID)
        if not ok and reason == "auth":
            session = auth.refresh_session(runtime_config.SUPABASE_REFRESH_TOKEN)
            if session:
                runtime_config.SUPABASE_ACCESS_TOKEN = session["access_token"]
                runtime_config.SUPABASE_REFRESH_TOKEN = session["refresh_token"]
                runtime_config.save(SUPABASE_ACCESS_TOKEN=session["access_token"],
                                    SUPABASE_REFRESH_TOKEN=session["refresh_token"])
                ok, reason = leaderboard.submit_lap(car, track, lap_time_ms, psn, data["samples"],
                                                    access_token=runtime_config.SUPABASE_ACCESS_TOKEN,
                                                    user_id=runtime_config.SUPABASE_USER_ID)
            elif auth.last_refresh_error == "invalid":
                reason = "session"
            else:
                reason = "network"
            if reason == "auth":
                reason = "server"
    except Exception:
        return False, "network"
    if not ok and reason == "session":
        for k in ("SUPABASE_ACCESS_TOKEN", "SUPABASE_REFRESH_TOKEN", "SUPABASE_USER_ID"):
            setattr(runtime_config, k, "")
        runtime_config.save(SUPABASE_ACCESS_TOKEN="", SUPABASE_REFRESH_TOKEN="", SUPABASE_USER_ID="")
    return bool(ok), "" if ok else reason


SUBMIT_MESSAGES = {
    "session": "Your leaderboard account on this PC is no longer valid. Create a new free account (just a display "
               "name) and submit again.",
    "data": "This lap's data couldn't be prepared for upload (is the file damaged?).",
    "server": "The server rejected the submission -- this is a server-side issue, not your connection. Try again later.",
    "network": "Couldn't reach the leaderboard. Check your connection and try again.",
}


# ── car weight, backups and duplicates ───────────────────────────────────────────────────────────────────────────
def car_weight(car: str):
    return dyno.load_weight(car) if car else None


def set_car_weight(df, kg) -> bool:
    """Remember a car's weight and put it on the frame (the Dyno & Tune tab reads df.attrs['mass_kg'])."""
    car = df.attrs.get("car") or ""
    ok = bool(car) and dyno.save_weight(car, kg)
    df.attrs["mass_kg"] = float(kg) if ok else dyno.load_weight(car)
    return ok


def backup_laps():
    path = runtime_config.backup_laps()
    return path, getattr(runtime_config, "last_backup_error", None)


def find_duplicates(folder):
    return lapclean.find_duplicates(Path(folder))


def move_duplicates(groups, folder):
    return lapclean.move_duplicates(groups, Path(folder))


# ── theoretical best: the other saved laps for this car and track ────────────────────────────────────────────────
THEORY_MAX_LAPS = 10


def saved_theory_laps(data_a: dict, path_a, folder=None, limit: int = THEORY_MAX_LAPS) -> list:
    """[(label, frame)] for the fastest saved laps on Lap A's car and track (each file is a full parse: call from a
    worker thread)."""
    from .. import lap_charts, records
    folder = folder if folder is not None else runtime_config.load().get("LAPS_FOLDER")
    found = []
    for row in lap_charts.theory_candidates(records.scan_laps(folder), dict(data_a), path_a, limit):
        try:
            _d, frame = lap_charts.load_lap(row["path"])
        except Exception:
            continue
        found.append((f"{row['recorded_at'][:8]} {lap_charts._fmt_laptime(row['time_s'])}", frame))
    return found


def theory_pool(df_a, df_b, extra, disk) -> list:
    """Lap A, Lap B, the overlay laps (label, frame) and the saved laps found on disk."""
    pool = [("Lap A", df_a)]
    if df_b is not None:
        pool.append(("Lap B", df_b))
    return pool + list(extra or []) + list(disk or [])
