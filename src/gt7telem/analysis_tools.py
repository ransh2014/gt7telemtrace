"""Text reports for the Lap Analyst's ANALYSIS TOOLS buttons.

Pure functions (no tkinter): each takes the frames the Lap Analyst already holds and returns the text to show, so they
are unit-tested without a window. The window and file dialogs live in lap_analyst.py.
"""

from __future__ import annotations

import datetime as _dt

from . import coach, driver_dna, dyno, dyno_compare, planner, replay3d, setup_advisor, validate


def _need(df, what):
    return None if df is not None else f"Load Lap A first ({what})."


def coach_report(dfa, dfb) -> str:
    if dfa is None or dfb is None:
        return "The coach compares Lap A with a reference.\nLoad Lap A, then load your best lap (or a top lap) as Lap B."
    return coach.describe(coach.analyse(dfa, dfb, max_tips=5))


def setup_report(dfa) -> str:
    return _need(dfa, "setup advisor") or setup_advisor.describe(setup_advisor.diagnose(dfa))


def validate_report(dfa, data: dict | None = None, ref_length_m: float | None = None) -> str:
    if dfa is None:
        return "Load Lap A first (lap validation)."
    lap_s = None
    try:
        lap_s = float((data or {}).get("lap_time_s") or 0) or None
    except (TypeError, ValueError):
        pass
    return validate.describe(validate.check(dfa, ref_length_m, lap_s))


def dna_report(dfa, others: list | None = None, save: bool = False) -> str:
    if dfa is None:
        return "Load Lap A first (driver DNA)."
    p = driver_dna.profile(dfa, others)
    if p is None:
        return "This lap can't be read for driver DNA."
    lines = [driver_dna.archetype(p), ""]
    for k in driver_dna.TRAITS:
        v = p[k]
        lines.append(f"{driver_dna.LABELS[k]:<22}{'needs 3+ laps loaded' if v is None else f'{v:.0f} / 100'}")
    hist = driver_dna.load_history()
    if save:
        driver_dna.record(p, _dt.date.today().isoformat())
        hist = driver_dna.load_history()
    for k in driver_dna.TRAITS:
        t = driver_dna.trend(hist, k)
        if t is not None and abs(t) >= 0.5:
            lines.append(f"  {driver_dna.LABELS[k]} is {'rising' if t > 0 else 'falling'} ({t:+.1f} per day logged)")
    lines += ["", "Share this code to compare with a friend:", driver_dna.export_code(p)]
    return "\n".join(lines)


def plan_report(laps: list) -> str:
    """`laps` is [(frame, lap_time_s), ...] for every lap currently loaded."""
    laps = [(f, t) for f, t in laps if f is not None and t]
    r = planner.plan([f for f, _ in laps], [t for _, t in laps])
    return r["text"]


def dyno_compare_report(dfa, dfb) -> str:
    if dfa is None or dfb is None:
        return "Load Lap A and Lap B (two cars, or two tunes of one car) to compare power curves."
    ra, rb = dyno.analyse(dfa), dyno.analyse(dfb)
    out = [dyno_compare.overlay(ra, rb, "Lap A", "Lap B")["text"]]
    for name, df, res in (("Lap A", dfa, ra), ("Lap B", dfb, rb)):
        out.append(f"{name}: {dyno_compare.final_drive_advice(res, df['speed_kmh'])['text']}")
    return "\n".join(out)


def replay_ok(dfa) -> bool:
    return dfa is not None and replay3d.usable(dfa.to_dict("records"))


def write_replay(path, dfa, dfb=None, title: str = "TRACE lap replay"):
    ghost = dfb.to_dict("records") if dfb is not None else None
    return replay3d.write_html(path, dfa.to_dict("records"), title, ghost)


# ---- Race Analyst: the same tools across the laps of one race ------------------------------------------------
def race_report(kind: str, laps: list) -> str:
    """`laps` is race_analyst.race_lap_frames(df): [(lap number, time_s, frame), ...] for the complete laps."""
    if len(laps) < 2:
        return "Needs at least two complete laps in this race."
    best_n, best_t, best_f = min(laps, key=lambda x: x[1])
    if kind == "coach":
        worst_n, worst_t, worst_f = max(laps, key=lambda x: x[1])
        head = f"Slowest lap (lap {worst_n}, +{worst_t - best_t:.2f}s) against your best (lap {best_n}):\n"
        return head + coach.describe(coach.analyse(worst_f, best_f, max_tips=5))
    if kind == "plan":
        return planner.plan([f for _, _, f in laps], [t for _, t, _ in laps])["text"]
    if kind == "dna":
        p = driver_dna.profile(best_f, [f for n, _, f in laps if n != best_n])
        if p is None:
            return "These laps can't be read for driver DNA."
        lines = [f"Across {len(laps)} laps: {driver_dna.archetype(p)}", ""]
        for k in driver_dna.TRAITS:
            lines.append(f"{driver_dna.LABELS[k]:<22}{'--' if p[k] is None else f'{p[k]:.0f} / 100'}")
        return "\n".join(lines + ["", "Share: " + driver_dna.export_code(p)])
    if kind == "validate":
        lines = []
        for n, t, f in laps:
            r = validate.check(f, lap_time_s=t)
            lines.append(f"Lap {n}: {r['verdict']}" + ("" if r["verdict"] == "clean" else " - " +
                         " ".join(i.text for i in r["issues"])))
        return "\n".join(lines)
    return "Unknown tool."
