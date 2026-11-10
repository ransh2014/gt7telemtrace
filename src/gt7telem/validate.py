"""Lap validation: flags laps with shortcuts, off-track cuts, teleports or physically impossible data, so the
leaderboard and your records can be trusted.

Pure numpy/pandas. lapclean.py already drops junk laps (too short, parked, duplicates); this goes further and looks at
the data itself. A lap is "clean", "suspect" (worth a look; a human decides) or "invalid" (the numbers can't be real).
Checks on the samples only; nothing here can tell you a lap was *legal* in the game's own rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

MAX_ACCEL_MS2 = 60.0         # ~6 g longitudinal: beyond any road or race car in GT7
MAX_JUMP_M = 60.0            # position jump between consecutive samples (at 60 Hz even 400 km/h is ~2 m)
MAX_LAT_G = 6.5
MAX_SPEED_KMH = 650.0
MIN_ROWS = 60
LENGTH_TOL = 0.04            # distance within 4% of the reference lap, else a shortcut / wrong layout
SURFACE_OFF = {2, 3, 4, 5}   # not asphalt; GT7 reports the surface under the car (best-effort, see below)


@dataclass
class Issue:
    code: str
    level: str     # "suspect" | "invalid"
    text: str


def check(df, ref_length_m: float | None = None, lap_time_s: float | None = None) -> dict:
    """{"verdict": "clean"|"suspect"|"invalid", "issues": [Issue,...]} for one lap frame."""
    issues: list[Issue] = []
    if df is None or len(df) < MIN_ROWS:
        return {"verdict": "invalid", "issues": [Issue("short", "invalid", "Too few samples to be a lap.")]}

    def col(name):
        return pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=float) if name in df.columns else None

    pos, spd, t = col("track_position"), col("speed_kmh"), col("t")
    if pos is None or spd is None:
        return {"verdict": "invalid", "issues": [Issue("columns", "invalid", "Missing position or speed.")]}
    if not np.isfinite(pos).all() or not np.isfinite(spd).all():
        issues.append(Issue("nan", "invalid", "The lap has NaN/infinite values."))
        pos, spd = np.nan_to_num(pos), np.nan_to_num(spd)

    if spd.max() > MAX_SPEED_KMH:
        issues.append(Issue("speed", "invalid", f"Speed reaches {spd.max():.0f} km/h."))

    if t is not None and np.isfinite(t).all():
        if (np.diff(t) < -1e-6).any():
            issues.append(Issue("time_back", "invalid", "Time runs backwards."))
        dt = np.diff(t)
        ok = dt > 1e-3
        if ok.sum() > 10:
            acc = np.abs(np.diff(spd / 3.6))[ok] / dt[ok]
            n_bad = int((acc > MAX_ACCEL_MS2).sum())
            if n_bad >= 3:
                issues.append(Issue("accel", "invalid", f"{n_bad} samples accelerate faster than any car can."))
        if lap_time_s and abs(float(t[-1] - t[0]) - lap_time_s) > max(1.5, 0.02 * lap_time_s):
            issues.append(Issue("time_mismatch", "suspect",
                                f"Samples span {t[-1] - t[0]:.1f}s but the lap time is {lap_time_s:.1f}s."))

    # teleport: the world position jumps (a reset, or two laps spliced together)
    if all(c in df.columns for c in ("world_x", "world_z")):
        x, z = col("world_x"), col("world_z")
        step = np.hypot(np.diff(x), np.diff(z))
        big = int((step > MAX_JUMP_M).sum())
        if big:
            issues.append(Issue("teleport", "invalid" if big > 2 else "suspect",
                                f"The car jumps >{MAX_JUMP_M:.0f} m between samples {big} time(s)."))

    # shortcut: the lap distance doesn't match the reference
    length = float(np.nanmax(pos) - np.nanmin(pos))
    if ref_length_m and ref_length_m > 100:
        off = abs(length - ref_length_m) / ref_length_m
        if off > LENGTH_TOL:
            issues.append(Issue("length", "suspect",
                                f"Lap is {length:.0f} m against {ref_length_m:.0f} m: possible shortcut or wrong layout."))

    # wall hits and cuts show up as sudden speed loss or a long off-asphalt stretch
    if "surface_type" in df.columns:
        surf = pd.to_numeric(df["surface_type"], errors="coerce").fillna(0).to_numpy()
        off_share = float(np.mean(np.isin(surf, list(SURFACE_OFF))))
        if off_share > 0.05:
            issues.append(Issue("off_track", "suspect", f"{off_share * 100:.0f}% of the lap is off the tarmac."))
    if "ang_y" in df.columns:
        lat = np.abs(col("ang_y") * spd / 3.6 / 9.81)
        if np.nanmax(lat) > MAX_LAT_G:
            issues.append(Issue("lat_g", "suspect", f"Peak lateral load {np.nanmax(lat):.1f} g is unrealistic."))
    if t is not None and len(t) > 20 and np.isfinite(t).all():
        dt = np.maximum(np.diff(t), 1e-3)
        decel = -np.diff(spd / 3.6) / dt
        hits = int((decel > 35.0).sum())     # >3.5 g of deceleration with the brake off is a wall
        if "brake" in df.columns:
            b = col("brake")[1:]
            hits = int(((decel > 35.0) & (b < 0.2)).sum())
        if hits >= 2:
            issues.append(Issue("wall", "suspect", f"{hits} sudden stops with no braking: likely wall contact."))

    if any(i.level == "invalid" for i in issues):
        verdict = "invalid"
    elif issues:
        verdict = "suspect"
    else:
        verdict = "clean"
    return {"verdict": verdict, "issues": issues}


def describe(res: dict) -> str:
    if res["verdict"] == "clean":
        return "Lap looks clean."
    return f"Lap is {res['verdict']}: " + " ".join(i.text for i in res["issues"])


def submission_ok(res: dict) -> bool:
    """Only clean laps go to the leaderboard."""
    return res["verdict"] == "clean"


def finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False
