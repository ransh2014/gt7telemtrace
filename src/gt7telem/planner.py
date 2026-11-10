"""Practice session planner: where the next tenth is, which corners to drill, and a target for each.

Pure pandas/numpy. Takes the laps of a session (frames as corners.frame_from_samples builds them) and their times, and
reuses corners.py: the corners that cost the most time and vary the most are the ones worth practising. Progress across
sessions is a small JSON history so the planner can say whether the last session moved the needle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import corners

MIN_LAPS = 3
MAX_HISTORY = 100


@dataclass
class Drill:
    corner: int            # corner number on the best lap
    apex: float            # m
    mean_loss_s: float     # average time lost vs your best lap
    best_gain_s: float     # what you'd gain by repeating your best corner every lap
    spread_s: float        # lap-to-lap variation at this corner
    target: str            # what to aim for, in plain words


def plan(laps: list, times_s: list, n: int = 3) -> dict:
    """{"ok","msg","best_lap","best_time_s","drills":[Drill],"total_gain_s","text"}

    `laps[i]` is the frame of the lap that took `times_s[i]` seconds. The best lap is the reference; every other
    lap is scored against it."""
    res = {"ok": False, "msg": "", "best_lap": None, "best_time_s": None, "drills": [], "total_gain_s": 0.0, "text": ""}
    pairs = [(f, float(t)) for f, t in zip(laps, times_s) if f is not None and t and t > 0]
    if len(pairs) < MIN_LAPS:
        res["msg"] = f"Needs at least {MIN_LAPS} clean laps."
        res["text"] = res["msg"]
        return res
    best_i = int(np.argmin([t for _, t in pairs]))
    ref, best_t = pairs[best_i]
    scored = []
    for i, (f, _) in enumerate(pairs):
        if i == best_i:
            continue
        _, _, deltas = corners.match_corners(f, ref)
        scored.append((i, {d.ref_number: d for d in deltas}))
    spreads = {s.ref_number: s for s in corners.corner_spreads(scored, min_laps=2)}
    ref_corners = {c.number: c for c in corners.detect_corners(ref)}
    drills = []
    for num, s in spreads.items():
        c = ref_corners.get(num)
        if c is None or s.mean_time_s <= 0.01:
            continue
        gain = float(s.mean_time_s)
        target = f"Brake at {c.brake_pos:.0f} m, " if c.brake_pos is not None else ""
        target += f"hit {c.min_speed:.0f} km/h at the apex"
        if c.throttle_pos is not None:
            target += f", back on the throttle by {c.throttle_pos:.0f} m"
        drills.append(Drill(num, c.apex, s.mean_time_s, gain, s.std_time_s, target + "."))
    drills.sort(key=lambda d: d.best_gain_s + 0.5 * d.spread_s, reverse=True)
    drills = drills[:n]
    res.update(ok=True, best_lap=best_i, best_time_s=best_t, drills=drills,
               total_gain_s=float(sum(d.best_gain_s for d in drills)))
    if drills:
        lines = [f"Next tenth is in {len(drills)} corner(s), worth about {res['total_gain_s']:.2f}s per lap:"]
        lines += [f"  T{d.corner}: costs {d.mean_loss_s:.2f}s on average (±{d.spread_s:.2f}). {d.target}" for d in drills]
        res["text"] = "\n".join(lines)
    else:
        res["text"] = "Your laps are consistent against your best; no single corner stands out. Push your best lap."
    return res


# ---- progress across sessions --------------------------------------------------------------------------------
def _path() -> Path:
    return Path.home() / ".gt7telem" / "practice_history.json"


def load_history(path=None) -> list[dict]:
    try:
        with open(path or _path(), encoding="utf-8") as f:
            d = json.load(f)
        return [e for e in d if isinstance(e, dict) and "key" in e and "best_s" in e] if isinstance(d, list) else []
    except Exception:
        return []


def record_session(key: str, date: str, best_s: float, median_s: float, path=None) -> bool:
    """`key` is the car + track the session was on. One entry per (key, date)."""
    try:
        p = Path(path or _path())
        hist = [e for e in load_history(p) if not (e["key"] == key and e["date"] == date)]
        hist.append({"key": key, "date": date, "best_s": float(best_s), "median_s": float(median_s)})
        hist = sorted(hist, key=lambda e: (e["key"], e["date"]))[-MAX_HISTORY:]
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=1)
        tmp.replace(p)
        return True
    except Exception:
        return False


def progress(history: list[dict], key: str) -> str:
    h = [e for e in history if e["key"] == key]
    if len(h) < 2:
        return "First session on this car and track: nothing to compare with yet."
    a, b = h[-2], h[-1]
    d = b["best_s"] - a["best_s"]
    m = b["median_s"] - a["median_s"]
    word = "faster" if d < 0 else "slower"
    return (f"Best lap {abs(d):.2f}s {word} than last session ({a['date']}); typical lap {m:+.2f}s. "
            f"{len(h)} sessions logged.")
