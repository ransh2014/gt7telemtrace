"""Race engineer: pit window, undercut/overcut maths, live "pit now" calls and a simulated finishing position.

Pure Python. It reads the numbers StintTracker.snapshot() already produces (fuel per lap, laps of fuel, pace loss and
trend) and the race state (lap, total laps, position). Everything here is an estimate built from your own laps, and the
text says so; GT7 sends no tyre wear and no pit-lane flag.
"""

from __future__ import annotations

from dataclasses import dataclass

FUEL_RESERVE_LAPS = 0.5
PIT_LOSS_DEFAULT_S = 24.0      # drive-through + stop; set the real one with pit_loss_s
OUT_LAP_PENALTY_S = 1.5        # cold tyres on the out lap


@dataclass
class PitWindow:
    earliest: int | None       # first lap where stopping is still worthwhile on pace
    latest: int | None         # last lap you can pit on fuel
    reason: str


def fuel_latest_lap(lap: int, laps_of_fuel: float | None) -> int | None:
    """The last lap you can start a stop on and still make it round on fuel."""
    if laps_of_fuel is None:
        return None
    return lap + max(0, int(laps_of_fuel - FUEL_RESERVE_LAPS) - 1)


def pit_window(lap: int, total_laps: int, snap: dict, pit_loss_s: float = PIT_LOSS_DEFAULT_S,
               fresh_gain_s: float | None = None) -> PitWindow:
    """When to stop. `snap` is StintTracker.snapshot(). The latest lap is set by fuel; the earliest is the lap where
    the time lost to worn tyres over the rest of the race beats the stop. `fresh_gain_s` is how much quicker a fresh
    set is per lap than the current one (defaults to the stint's pace_loss_s)."""
    latest = fuel_latest_lap(lap, snap.get("laps_of_fuel"))
    gain = fresh_gain_s if fresh_gain_s is not None else snap.get("pace_loss_s")
    remaining = max(0, total_laps - lap + 1) if total_laps else 0
    earliest = None
    reason = ""
    if snap.get("stop_needed") is False:
        return PitWindow(None, None, "You can reach the flag on the fuel you have; no stop needed for fuel.")
    if gain and gain > 0 and remaining:
        # pitting now repays the stop if gain * (laps left) > pit loss + out-lap penalty
        pay_laps = (pit_loss_s + OUT_LAP_PENALTY_S) / gain
        earliest = lap if remaining > pay_laps else None
        if earliest is None:
            reason = f"A stop costs about {pit_loss_s:.0f}s and fresh tyres only repay it over {pay_laps:.0f} laps."
        else:
            reason = f"Tyres are about {gain:.2f}s/lap off the pace; a stop repays itself in {pay_laps:.0f} laps."
    elif latest is not None:
        reason = "Pit by the fuel limit."
    return PitWindow(earliest, latest, reason)


def undercut(gap_ahead_s: float | None, tyre_gain_s: float, laps_to_hold: int = 2,
             pit_loss_s: float = PIT_LOSS_DEFAULT_S) -> dict:
    """Would stopping now (undercut) get you ahead of the car in front, if it stays out `laps_to_hold` laps?

    You gain `tyre_gain_s` per lap on fresh tyres while they stay out on old ones, minus the out-lap penalty. Returns
    {"works", "margin_s", "text"}; margin is how far ahead you'd come out (negative: still behind)."""
    if gap_ahead_s is None:
        return {"works": None, "margin_s": None, "text": "No car ahead to undercut."}
    swing = tyre_gain_s * laps_to_hold - OUT_LAP_PENALTY_S
    margin = swing - gap_ahead_s     # the other car pays its own stop later, so only the swing must beat the gap
    works = margin > 0
    text = (f"Undercut {'works' if works else 'does not work'}: you'd gain about {swing:.1f}s over {laps_to_hold} laps "
            f"against a {gap_ahead_s:.1f}s gap ({margin:+.1f}s).")
    return {"works": works, "margin_s": margin, "text": text}


def overcut(gap_behind_s: float | None, tyre_loss_per_lap_s: float, laps_to_hold: int = 2) -> dict:
    """Staying out while the car behind pits: do you keep position? You lose `tyre_loss_per_lap_s` each extra lap but
    they spend an out lap on cold tyres."""
    if gap_behind_s is None:
        return {"works": None, "margin_s": None, "text": "No car behind to cover."}
    margin = gap_behind_s - (tyre_loss_per_lap_s * laps_to_hold) + OUT_LAP_PENALTY_S
    works = margin > 0
    return {"works": works, "margin_s": margin,
            "text": f"Overcut {'holds' if works else 'fails'}: {margin:+.1f}s margin after {laps_to_hold} laps."}


def simulate_finish(position: int, total_positions: int, lap: int, total_laps: int, lap_s: float,
                    pits_needed: int, pit_loss_s: float = PIT_LOSS_DEFAULT_S,
                    rival_gaps_s: list[float] | None = None) -> dict:
    """Where you'd finish if you keep this pace and make `pits_needed` more stops.

    `rival_gaps_s` are the time gaps (s) to every other car, negative for cars ahead of you. Without gaps only the time cost is returned. Returns
    {"cost_s", "finish", "text"}."""
    left = max(0, total_laps - lap + 1)
    cost = pits_needed * pit_loss_s
    out = {"cost_s": cost, "finish": None, "text": ""}
    if rival_gaps_s:
        # cars ahead stay ahead; a car behind us by less than the stop costs gets past (equal pace assumed)
        ahead = sum(1 for g in rival_gaps_s if g < 0)
        passed_by = sum(1 for g in rival_gaps_s if 0 < g < cost)
        out["finish"] = ahead + passed_by + 1
        out["text"] = (f"Pace {lap_s:.1f}s/lap over {left} laps and {pits_needed} stop(s) (~{cost:.0f}s): "
                       f"P{out['finish']} projected.")
    else:
        out["text"] = f"{pits_needed} stop(s) will cost about {cost:.0f}s over the remaining {left} laps."
    return out


def pit_call(lap: int, total_laps: int, snap: dict, window: PitWindow | None = None, last_called: int | None = None):
    """The live radio call for this lap, or None. Call once per lap (it returns None if `last_called == lap`).

    Returns (message, kind) with kind "box" or "warn"."""
    if last_called == lap:
        return None
    w = window or pit_window(lap, total_laps, snap)
    if w.latest is not None and lap >= w.latest:
        return "Box this lap. Box, box.", "box"
    if w.latest is not None and lap == w.latest - 1:
        return "Pit next lap.", "warn"
    if w.earliest is not None and w.latest is not None and lap == w.earliest and w.earliest < w.latest:
        return "Pit window is open.", "warn"
    return None
