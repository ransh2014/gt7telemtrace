"""Friends groups: private leaderboards, per-sector times and weekly challenges, with no server.

TRACE has no account system for friends, so groups live in a local file and travel as short pasteable codes: you
export your result for a challenge, a friend pastes it into their copy, and the board is rebuilt on both sides.
Codes carry a checksum against typos and accidental edits; it is not anti-cheat (see validate.py for lap checks).

Pure Python, no network.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import zlib
from pathlib import Path

PREFIX = "TRC1"
MAX_NAME = 24
MAX_SECTORS = 24
MAX_ENTRIES = 500


def week_id(date: _dt.date | None = None) -> str:
    """ISO week, e.g. '2026-W46'. Challenges reset every Monday."""
    d = date or _dt.date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _safe(s, n=MAX_NAME) -> str:
    return "".join(ch for ch in str(s) if ch.isprintable() and ch not in "<>&\"'")[:n].strip()


def make_challenge(track: str, car: str, week: str | None = None, name: str = "") -> dict:
    return {"track": _safe(track, 60), "car": _safe(car, 60), "week": week or week_id(),
            "name": _safe(name or f"{track} weekly"), "results": []}


def _key(ch: dict) -> str:
    return f"{ch['week']}|{ch['track'].lower()}|{ch['car'].lower()}"


def add_result(ch: dict, player: str, lap_time_ms: int, sectors_ms: list[int] | None = None) -> bool:
    """Record a lap. Keeps each player's best; returns False for a nonsense time or a full board."""
    try:
        t = int(lap_time_ms)
    except (TypeError, ValueError):
        return False
    if not 20_000 <= t <= 1_800_000:
        return False
    secs = [int(x) for x in (sectors_ms or []) if isinstance(x, (int, float)) and x > 0][:MAX_SECTORS]
    p = _safe(player)
    if not p:
        return False
    for r in ch["results"]:
        if r["player"].lower() == p.lower():
            if t < r["time_ms"]:
                r["time_ms"], r["sectors_ms"] = t, secs
            return True
    if len(ch["results"]) >= MAX_ENTRIES:
        return False
    ch["results"].append({"player": p, "time_ms": t, "sectors_ms": secs})
    return True


def standings(ch: dict) -> list[dict]:
    """Rows sorted by lap time with the gap to the leader."""
    rows = sorted(ch["results"], key=lambda r: r["time_ms"])
    lead = rows[0]["time_ms"] if rows else 0
    return [{"pos": i + 1, "player": r["player"], "time_ms": r["time_ms"], "gap_ms": r["time_ms"] - lead}
            for i, r in enumerate(rows)]


def sector_board(ch: dict) -> list[dict]:
    """For each sector, who is fastest: [{"sector": 1, "player": ..., "time_ms": ...}, ...]."""
    n = max((len(r["sectors_ms"]) for r in ch["results"]), default=0)
    out = []
    for i in range(n):
        have = [(r["sectors_ms"][i], r["player"]) for r in ch["results"] if len(r["sectors_ms"]) > i]
        if have:
            t, p = min(have)
            out.append({"sector": i + 1, "player": p, "time_ms": t})
    return out


def ideal_lap(ch: dict) -> int | None:
    """Sum of the fastest sectors across the whole group (the lap the group could drive together)."""
    board = sector_board(ch)
    return sum(b["time_ms"] for b in board) if board else None


def fmt(ms: int) -> str:
    m, rest = divmod(int(ms), 60000)
    return f"{m}:{rest / 1000:06.3f}"


# ---- codes ---------------------------------------------------------------------------------------------------
def _pack(payload: dict) -> str:
    raw = zlib.compress(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"), 9)
    b = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    crc = hashlib.sha256(b.encode("ascii")).hexdigest()[:6]
    return f"{PREFIX}-{crc}-{b}"


def _unpack(code: str) -> dict | None:
    try:
        pre, crc, b = code.strip().split("-", 2)
        if pre != PREFIX or hashlib.sha256(b.encode("ascii")).hexdigest()[:6] != crc:
            return None
        raw = zlib.decompress(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)), 15, 200_000)
        d = json.loads(raw.decode("utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def export_challenge(ch: dict) -> str:
    """The whole board as one code, to share a challenge or its current standings."""
    return _pack({"v": 1, "ch": ch})


def export_result(ch: dict, player: str) -> str | None:
    """Just one player's result for a challenge: the small code you send a friend."""
    for r in ch["results"]:
        if r["player"].lower() == player.lower():
            return _pack({"v": 1, "key": _key(ch), "r": r, "track": ch["track"], "car": ch["car"],
                          "week": ch["week"], "name": ch["name"]})
    return None


def import_code(code: str, ch: dict | None = None) -> tuple[dict | None, str]:
    """Merge a pasted code. With no `ch` a challenge code creates one. Returns (challenge, message)."""
    d = _unpack(code)
    if d is None or d.get("v") != 1:
        return ch, "That code isn't valid (a character is missing or it was edited)."
    if "ch" in d:
        src = d["ch"]
        if not isinstance(src, dict) or not all(k in src for k in ("track", "car", "week", "results")):
            return ch, "That challenge code is damaged."
        base = ch or make_challenge(src["track"], src["car"], src["week"], src.get("name", ""))
        if _key(base) != _key(src):
            return ch, "That code is for a different car, track or week."
        n = 0
        for r in src["results"]:
            if isinstance(r, dict) and add_result(base, r.get("player", ""), r.get("time_ms", 0), r.get("sectors_ms")):
                n += 1
        return base, f"Merged {n} result(s)."
    r = d.get("r")
    if not isinstance(r, dict):
        return ch, "That code has no result in it."
    base = ch or make_challenge(d.get("track", ""), d.get("car", ""), d.get("week"), d.get("name", ""))
    if d.get("key") != _key(base):
        return ch, "That result is for a different car, track or week."
    ok = add_result(base, r.get("player", ""), r.get("time_ms", 0), r.get("sectors_ms"))
    return base, ("Added the result." if ok else "That result was rejected (bad time or name).")


# ---- storage -------------------------------------------------------------------------------------------------
def _path() -> Path:
    return Path.home() / ".gt7telem" / "groups.json"


def load(path=None) -> list[dict]:
    try:
        with open(path or _path(), encoding="utf-8") as f:
            d = json.load(f)
        return [c for c in d if isinstance(c, dict) and "results" in c and "week" in c] if isinstance(d, list) else []
    except Exception:
        return []


def save(challenges: list[dict], path=None) -> bool:
    try:
        p = Path(path or _path())
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(challenges[-100:], f, indent=1)
        tmp.replace(p)
        return True
    except Exception:
        return False
