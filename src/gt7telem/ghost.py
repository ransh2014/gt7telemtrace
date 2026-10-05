"""ghost.py -- the live gap to the leaderboard's fastest lap in the Dashboard.

A leaderboard lap is stored compactly (track position, speed, pedals; no
timestamps), so its clock is rebuilt from distance and speed and scaled to the
lap's real time, the same way the Lap Analyst reads a downloaded ghost. The gap
shown live is then your elapsed lap time minus the ghost's time at the same
spot on the track. Pure numpy; no tkinter, no network (leaderboard.py fetches).
"""

from __future__ import annotations

import math

import numpy as np

MIN_SAMPLES = 20
MIN_SPAN_M = 100.0
MIN_SPEED_MS = 1.0  # floor so a stationary sample can't make a gap of hours


class Ghost:
    """A lap's position -> time curve."""

    def __init__(self, positions: np.ndarray, times: np.ndarray, lap_time_s: float):
        self.positions = positions
        self.times = times
        self.lap_time_s = lap_time_s

    def time_at(self, track_position: float) -> float | None:
        """The ghost's lap time at `track_position` metres, or None outside the
        range it covers (before the line, or past the end of the lap)."""
        if not (self.positions[0] <= track_position <= self.positions[-1]):
            return None
        return float(np.interp(track_position, self.positions, self.times))

    def gap(self, track_position: float, elapsed_s: float) -> float | None:
        """Seconds you are ahead (-) or behind (+) the ghost at this spot."""
        t = self.time_at(track_position)
        return None if t is None else float(elapsed_s - t)


def build_ghost(samples, lap_time_ms) -> Ghost | None:
    """A Ghost from a leaderboard lap's compact samples and its time in
    milliseconds, or None if the data can't make a usable lap."""
    try:
        lap_s = float(lap_time_ms) / 1000.0
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lap_s) and lap_s > 1.0) or not isinstance(samples, list):
        return None
    pts = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        try:
            p, v = float(s.get("track_position")), float(s.get("speed_kmh"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(p) and math.isfinite(v):
            pts.append((p, v))
    if len(pts) < MIN_SAMPLES:
        return None
    pts.sort(key=lambda x: x[0])
    pos = np.array([p for p, _ in pts])
    spd = np.array([v for _, v in pts]) / 3.6
    keep = np.concatenate([[True], np.diff(pos) > 0])          # strictly increasing positions only
    pos, spd = pos[keep], spd[keep]
    if len(pos) < MIN_SAMPLES or pos[-1] - pos[0] < MIN_SPAN_M:
        return None
    mid = np.maximum((spd[1:] + spd[:-1]) / 2, MIN_SPEED_MS)
    t = np.concatenate([[0.0], np.cumsum(np.diff(pos) / mid)])
    if t[-1] <= 0:
        return None
    return Ghost(pos, t * (lap_s / t[-1]), lap_s)


def format_gap(gap: float | None) -> tuple[str, str]:
    """(text, colour) for the Dashboard label: red when behind, green when ahead."""
    if gap is None:
        return "--", "#c0c0e0"
    return f"{gap:+.2f}s", "#e74c3c" if gap > 0 else "#2ecc71"
