"""Corner-by-corner driving coach.

Pure pandas/numpy (no tkinter). Builds on corners.py: every corner of your lap is matched to the same corner on a
reference lap (your PB or the leaderboard's top lap) and the biggest *fixable* difference at each one becomes a single
plain-English tip, e.g. "Brake 14 m later at T4" or "Back on the throttle 0.3 s sooner at T7".

``LiveCoach`` turns the tips from your previous lap into short spoken cues just before each of those corners on the
next lap. It only decides *what* to say and *when*; the Dashboard hands the text to voice.speak().
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import corners

BRAKE_MIN_M = 8.0        # a brake-point difference smaller than this is noise
THROTTLE_MIN_M = 8.0
SPEED_MIN_KMH = 3.0
TIME_MIN_S = 0.02        # a corner must cost at least this to earn a tip
LEAD_S = 3.5             # speak this many seconds before the corner's brake point
MIN_LEAD_M = 120.0
MAX_LEAD_M = 400.0


@dataclass
class Tip:
    corner: int              # corner number on the lap being coached
    apex: float              # m
    brake_pos: float | None  # m, where the cue should be spoken from (None: use the apex)
    kind: str                # "brake_late" | "brake_early" | "speed" | "throttle"
    text: str                # full sentence for the log / report
    cue: str                 # short spoken form
    time_s: float            # time lost at this corner vs the reference (+ = slower)
    est_s: float             # rough seconds the tip itself is worth


def _speed_at(d, pos: float) -> float:
    return max(20.0, float(np.interp(pos, d["track_position"], d["speed_kmh"]))) / 3.6


def _candidates(c: corners.Corner, delta: corners.CornerDelta, d) -> list[tuple[float, str, str, str]]:
    """(estimated seconds, kind, long text, spoken cue) for each thing worth saying about this corner."""
    n = c.number
    out = []
    b = delta.brake_m
    if b is not None:
        if b < -BRAKE_MIN_M:
            m = abs(b)
            v = _speed_at(d, c.brake_pos) if c.brake_pos is not None else 50.0
            out.append((m / v * 0.5, "brake_early", f"Brake {m:.0f} m later at T{n}.", f"Turn {n}. Brake later"))
        elif b > BRAKE_MIN_M and delta.min_speed_kmh < -SPEED_MIN_KMH:
            out.append(
                (0.1 + b / 80.0, "brake_late", f"You brake {b:.0f} m later than the reference at T{n} but carry less "
                 f"speed ({abs(delta.min_speed_kmh):.0f} km/h slower at the apex). Brake a touch earlier.",
                 f"Turn {n}. Brake earlier")
            )
    if delta.min_speed_kmh < -SPEED_MIN_KMH and not (b is not None and b > BRAKE_MIN_M):
        s = abs(delta.min_speed_kmh)
        out.append(
            (s / 50.0, "speed", f"Carry {s:.0f} km/h more through the middle of T{n}.", f"Turn {n}. More minimum speed")
        )
    t = delta.throttle_m
    if t is not None and t > THROTTLE_MIN_M:
        v = _speed_at(d, c.throttle_pos) if c.throttle_pos is not None else c.min_speed / 3.6
        sec = t / max(v, 5.0)
        out.append(
            (sec, "throttle", f"You're lifting {sec:.1f} s early at T{n}: back on the throttle {t:.0f} m sooner.",
             f"Turn {n}. Throttle sooner")
        )
    return out


def analyse(df, ref, max_tips: int = 3) -> list[Tip]:
    """The most useful tips for `df` against `ref`, biggest time loss first. Empty when the laps can't be read or
    nothing costs time."""
    mine, _, deltas = corners.match_corners(df, ref)
    d = corners._prepare(df)
    if d is None or not deltas:
        return []
    by_num = {c.number: c for c in mine}
    tips: list[Tip] = []
    for delta in deltas:
        c = by_num.get(delta.number)
        if c is None or delta.time_s is None or delta.time_s < TIME_MIN_S:
            continue
        cands = _candidates(c, delta, d)
        if not cands:
            continue
        est, kind, text, cue = max(cands, key=lambda x: x[0])
        tips.append(Tip(c.number, c.apex, c.brake_pos, kind, text, cue, float(delta.time_s), float(est)))
    tips.sort(key=lambda t: t.time_s, reverse=True)
    return tips[:max_tips]


def describe(tips: list[Tip]) -> str:
    """Log text for a lap: the tips one per line, or a line saying there is nothing to fix."""
    if not tips:
        return "Coach: no corner is costing you time against the reference."
    return "Coach:\n" + "\n".join(f"  T{t.corner} (+{t.time_s:.2f}s): {t.text}" for t in tips)


class LiveCoach:
    """Speaks a short cue shortly before each coached corner on the next lap. Pure: feed it the position each frame."""

    def __init__(self, tips: list[Tip] | None = None):
        self.tips = list(tips or [])
        self._said: set[int] = set()
        self._last_pos: float | None = None

    def set_tips(self, tips: list[Tip]) -> None:
        self.tips = list(tips)
        self._said.clear()
        self._last_pos = None

    def update(self, track_position: float, speed_kmh: float) -> str | None:
        """The cue to speak now, or None. Each corner is announced once per lap; a drop in position (new lap)
        re-arms them."""
        if self._last_pos is not None and track_position < self._last_pos - 200.0:
            self._said.clear()
        self._last_pos = track_position
        lead = min(MAX_LEAD_M, max(MIN_LEAD_M, speed_kmh / 3.6 * LEAD_S))
        for t in self.tips:
            if t.corner in self._said:
                continue
            at = t.brake_pos if t.brake_pos is not None else t.apex
            if at - lead <= track_position < at:
                self._said.add(t.corner)
                return t.cue
        return None
