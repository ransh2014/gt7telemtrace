"""Lap-time consistency maths for the Race Analyst (race card + Consistency tab).

Pure numpy, no tkinter, so it is easy to test. Only *clean* laps count: a lap
more than 7 % slower than the best one (the usual "107 % rule") is almost
always a spin, a pit in/out lap or traffic, and would swamp the spread.

    spread   max - min of the clean laps (s)
    std      standard deviation of the clean laps (s)
    trend    least-squares slope of lap time against lap order (s per lap);
             positive = getting slower (tyres, fuel, tiredness)
    score    0-100. 100 is every clean lap identical; 0 is a standard
             deviation of 2 % of the average lap or worse.
"""

from __future__ import annotations

import math

import numpy as np

CLEAN_LAP_FACTOR = 1.07  # laps slower than best * this are not "clean"
SCORE_ZERO_CV = 0.02  # std / mean at which the score reaches 0
MIN_LAPS_FOR_SCORE = 3
MIN_LAPS_FOR_TREND = 3


def clean_laps(times, factor: float = CLEAN_LAP_FACTOR) -> list[tuple[int, float]]:
    """(index in `times`, time) for each usable lap within `factor` of the best one."""
    valid = [(i, float(t)) for i, t in enumerate(times) if _usable(t)]
    if not valid:
        return []
    best = min(t for _, t in valid)
    return [(i, t) for i, t in valid if t <= best * factor]


def _usable(t) -> bool:
    try:
        f = float(t)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 1.0


def lap_time_stats(times, factor: float = CLEAN_LAP_FACTOR) -> dict | None:
    """Summary of a list of lap times (seconds, in lap order), or None if there
    is no usable lap. `trend` and `score` are None when there are too few clean
    laps for them to mean anything."""
    valid = [float(t) for t in times if _usable(t)]
    if not valid:
        return None
    clean = clean_laps(times, factor)
    ys = np.array([t for _, t in clean])
    xs = np.array([i for i, _ in clean], dtype=float)
    avg = float(ys.mean())
    std = float(ys.std(ddof=1)) if len(ys) > 1 else 0.0
    trend = None
    if len(ys) >= MIN_LAPS_FOR_TREND and float(xs.max() - xs.min()) > 0:
        trend = float(np.polyfit(xs, ys, 1)[0])
    score = None
    if len(ys) >= MIN_LAPS_FOR_SCORE and avg > 0:
        score = int(round(100 * min(1.0, max(0.0, 1.0 - (std / avg) / SCORE_ZERO_CV))))
    return {
        "n": len(valid),
        "n_clean": len(ys),
        "best": float(ys.min()),
        "avg": avg,
        "std": std,
        "spread": float(ys.max() - ys.min()),
        "trend": trend,
        "score": score,
        "clean_idx": [int(i) for i, _ in clean],
    }


def trend_label(trend: float | None) -> str:
    """Plain-words reading of the trend slope."""
    if trend is None:
        return "not enough laps"
    if abs(trend) < 0.03:
        return "flat"
    return f"{'slowing' if trend > 0 else 'speeding up'} {abs(trend):.2f} s/lap"
