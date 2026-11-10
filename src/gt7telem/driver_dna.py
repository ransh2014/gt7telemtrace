"""Driver DNA: a style profile from your laps, tracked over weeks and comparable with a friend's.

Pure pandas/numpy. Five traits, each 0-100 (50 is a typical hot lap; the scales are fixed so profiles from different
people and different weeks are comparable):

* brake_aggression    how hard and how fast you hit the brake
* trail_braking       how much brake you carry into the corner after turning in
* smoothness          how little the steering is jerked about
* throttle_modulation how much of the exit is partial throttle rather than on/off
* precision           how repeatable your brake points are, lap to lap (needs 3+ laps; otherwise None)

The profile describes how a lap was driven, not whether it was fast.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import corners

TRAITS = ("brake_aggression", "trail_braking", "smoothness", "throttle_modulation", "precision")
LABELS = {
    "brake_aggression": "Brake aggression",
    "trail_braking": "Trail braking",
    "smoothness": "Smoothness",
    "throttle_modulation": "Throttle modulation",
    "precision": "Precision",
}
MAX_HISTORY = 200


def _clip(x):
    return float(max(0.0, min(100.0, x)))


def profile(df, other_laps: list | None = None) -> dict | None:
    """Trait scores for one lap (None if it can't be read). `other_laps` are more frames from the same session; with
    two or more the precision trait is filled in from the spread of brake points."""
    d = corners._prepare(df)
    if d is None:
        return None
    t = d["t"].to_numpy(dtype=float)
    brk = d["brake"].to_numpy(dtype=float)
    thr = d["throttle"].to_numpy(dtype=float)
    st = d["steering"].to_numpy(dtype=float)
    dt = np.maximum(np.diff(t, prepend=t[0] - 0.05), 1e-3)

    on = brk > 0.1
    if on.any():
        peak = float(np.mean([brk[i:j].max() for i, j in _runs(on)]))
        rise = np.gradient(brk, t + np.arange(len(t)) * 1e-9)
        rate = float(np.percentile(rise[rise > 0], 90)) if (rise > 0).any() else 0.0
        brake_aggr = _clip(100 * (0.6 * peak + 0.4 * min(1.0, rate / 6.0)))
    else:
        brake_aggr = 0.0

    corn = corners.detect_corners(df)
    trail = [c for c in corn if c.brake_pos is not None]
    trail_score = _clip(100 * (np.mean([1.0 if c.trail else 0.0 for c in trail]) if trail else 0.0))
    # also credit how long the brake is held after steering starts
    steer_on = np.abs(st) > 0.15
    both = on & steer_on
    if on.any():
        trail_score = _clip(0.5 * trail_score + 50 * min(1.0, float(both.sum()) / max(on.sum(), 1) * 2.5))

    jerk = np.abs(np.diff(st, prepend=st[0])) / dt
    smooth = _clip(100 * (1.0 - min(1.0, float(np.percentile(jerk, 95)) / 6.0)))

    moving = d["speed_kmh"].to_numpy(dtype=float) > 30
    part = moving & (thr > 0.1) & (thr < 0.9) & (brk < 0.05)
    full_or_off = moving & ((thr >= 0.9) | (thr <= 0.1)) & (brk < 0.05)
    denom = max(int(part.sum() + full_or_off.sum()), 1)
    modulation = _clip(100 * min(1.0, (part.sum() / denom) * 3.0))

    precision = None
    laps = [x for x in [df] + list(other_laps or []) if x is not None]
    if len(laps) >= 3:
        pts: dict[int, list[float]] = {}
        for lap in laps:
            for c in corners.detect_corners(lap):
                if c.brake_pos is not None:
                    key = int(round(c.apex / 60.0))
                    pts.setdefault(key, []).append(c.brake_pos)
        sds = [float(np.std(v)) for v in pts.values() if len(v) >= 3]
        if sds:
            precision = _clip(100 * (1.0 - min(1.0, float(np.mean(sds)) / 25.0)))

    return {
        "brake_aggression": brake_aggr,
        "trail_braking": trail_score,
        "smoothness": smooth,
        "throttle_modulation": modulation,
        "precision": precision,
    }


def _runs(mask):
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def archetype(p: dict) -> str:
    """A one-line read of the style."""
    a, tr, sm, mod = (p.get(k) or 0 for k in ("brake_aggression", "trail_braking", "smoothness", "throttle_modulation"))
    if tr >= 60 and a >= 55:
        base = "Late-braking trail braker"
    elif a >= 65:
        base = "Hard, late braker"
    elif sm >= 70 and mod >= 55:
        base = "Smooth momentum driver"
    elif sm < 40:
        base = "Aggressive, busy-handed driver"
    else:
        base = "Balanced driver"
    return base


def compare(a: dict, b: dict, name_a: str = "You", name_b: str = "Friend") -> list[str]:
    """One line per trait where the two differ by 10 points or more."""
    out = []
    for k in TRAITS:
        x, y = a.get(k), b.get(k)
        if x is None or y is None or abs(x - y) < 10:
            continue
        hi, lo = (name_a, name_b) if x > y else (name_b, name_a)
        out.append(f"{LABELS[k]}: {hi} {max(x, y):.0f} vs {lo} {min(x, y):.0f}")
    return out or ["Your styles are very close."]


# ---- history over weeks --------------------------------------------------------------------------------------
def _path() -> Path:
    return Path.home() / ".gt7telem" / "driver_dna.json"


def load_history(path=None) -> list[dict]:
    try:
        with open(path or _path(), encoding="utf-8") as f:
            d = json.load(f)
        return [e for e in d if isinstance(e, dict) and "date" in e and "traits" in e] if isinstance(d, list) else []
    except Exception:
        return []


def record(traits: dict, date: str, path=None) -> bool:
    """Add one entry (one per date: a second call for the same day replaces it). Keeps the last MAX_HISTORY."""
    try:
        p = Path(path or _path())
        hist = [e for e in load_history(p) if e["date"] != date]
        hist.append({"date": date, "traits": {k: traits.get(k) for k in TRAITS}})
        hist = sorted(hist, key=lambda e: e["date"])[-MAX_HISTORY:]
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=1)
        tmp.replace(p)
        return True
    except Exception:
        return False


def trend(history: list[dict], trait: str) -> float | None:
    """Points per entry (least-squares slope) for one trait, or None with fewer than 3 entries."""
    ys = [e["traits"].get(trait) for e in history if e["traits"].get(trait) is not None]
    if len(ys) < 3:
        return None
    return float(np.polyfit(np.arange(len(ys)), ys, 1)[0])


def export_code(traits: dict, name: str = "") -> str:
    """A short string a friend can paste into TRACE to compare with you."""
    vals = ",".join("-" if traits.get(k) is None else str(int(round(traits[k]))) for k in TRAITS)
    safe = "".join(ch for ch in name if ch.isalnum() or ch in " _-")[:24]
    return f"DNA1:{safe}:{vals}"


def import_code(code: str) -> tuple[str, dict] | None:
    try:
        tag, name, vals = code.strip().split(":", 2)
        if tag != "DNA1":
            return None
        parts = vals.split(",")
        if len(parts) != len(TRAITS):
            return None
        traits = {}
        for k, v in zip(TRAITS, parts):
            if v == "-":
                traits[k] = None
            else:
                n = int(v)
                if not 0 <= n <= 100:
                    return None
                traits[k] = float(n)
        return name or "Friend", traits
    except (ValueError, AttributeError):
        return None
