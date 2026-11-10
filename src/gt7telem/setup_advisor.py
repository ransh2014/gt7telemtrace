"""Setup advisor: finds understeer, oversteer, wheelspin, lock-ups and bottoming-out in one lap and suggests setup
changes, plus an A/B comparison so you can tell whether a change helped.

Pure pandas/numpy. Balance uses the front wheels' steering angle and the car's wheelbase (both in newer GT7 packets):
at speed v a car that does what you ask turns at yaw = v * tan(angle) / wheelbase. Less than that is understeer; more
(or the wheel pointing against the turn) is oversteer. Without those two fields balance is reported as unknown rather
than guessed.

Every suggestion is a *direction and size to try*, in GT7's own units, not a guaranteed fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MIN_SPEED_KMH = 40.0
MIN_DEMAND = 0.01          # rad/s of expected yaw before a sample counts as "turning"
UNDER_RATIO = 0.75         # actual / expected yaw below this = understeer
OVER_RATIO = 1.25          # above this = oversteer
SEVERE = 0.18              # share of cornering samples that makes an issue "strong"
MILD = 0.07                # below this we say nothing
SPIN = 1.12                # driven-wheel speed / car speed
LOCK = 0.80
BOTTOM_MM = 5.0            # ride height below this = floor touching
MIN_ROWS = 120


@dataclass
class Finding:
    key: str                # "under_entry", "over_exit", "wheelspin", ...
    phase: str              # "entry" | "mid" | "exit" | "straight" | "all"
    share: float            # 0..1 share of the relevant samples
    severity: str           # "mild" | "strong"
    text: str
    fixes: list[str] = field(default_factory=list)


def _col(df, name, default=0.0):
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").fillna(default).to_numpy(dtype=float)
    return np.full(len(df), default)


def _wheel_angle(df):
    """Mean front road-wheel angle (rad) per row, or None if the lap has no such data."""
    if "wheel_steering_angle" not in df.columns:
        return None
    vals = []
    for v in df["wheel_steering_angle"]:
        try:
            vals.append((float(v[0]) + float(v[1])) / 2.0)
        except (TypeError, ValueError, IndexError, KeyError):
            vals.append(np.nan)
    a = np.array(vals, dtype=float)
    return None if np.isnan(a).mean() > 0.5 else np.nan_to_num(a)


def _wheelbase(df):
    if "wheel_base" not in df.columns:
        return None
    w = pd.to_numeric(df["wheel_base"], errors="coerce").dropna()
    w = w[w > 1.0]
    return float(w.median()) if len(w) else None


def _sev(share):
    return "strong" if share >= SEVERE else "mild"


def diagnose(df) -> dict:
    """{"ok", "msg", "balance": {phase: {"under","over","n"}}, "findings": [Finding, ...]}"""
    res = {"ok": False, "msg": "", "balance": {}, "findings": []}
    if df is None or len(df) < MIN_ROWS or "speed_kmh" not in df.columns:
        res["msg"] = "Needs a full recorded lap."
        return res
    v = _col(df, "speed_kmh") / 3.6
    thr, brk = _col(df, "throttle"), _col(df, "brake")
    yaw = _col(df, "ang_y")
    res["ok"] = True
    findings: list[Finding] = []

    # ---- balance ----
    ang, wb = _wheel_angle(df), _wheelbase(df)
    if ang is None or wb is None:
        res["msg"] = "No steering-angle / wheelbase data in this lap, so understeer and oversteer can't be measured."
    else:
        expect = v * np.tan(ang) / wb
        cornering = (v * 3.6 > MIN_SPEED_KMH) & (np.abs(expect) > MIN_DEMAND)
        ratio = np.where(cornering, yaw * np.sign(expect) / np.maximum(np.abs(expect), 1e-6), 1.0)
        # GT7's yaw sign can differ from the wheel sign; pick the orientation that agrees on average
        if np.nanmedian(ratio[cornering]) < 0 if cornering.any() else False:
            ratio = -ratio
        phases = {
            "entry": cornering & (brk > 0.1),
            "mid": cornering & (brk <= 0.1) & (thr <= 0.5),
            "exit": cornering & (brk <= 0.1) & (thr > 0.5),
        }
        for ph, m in phases.items():
            n = int(m.sum())
            if n < 15:
                res["balance"][ph] = {"under": 0.0, "over": 0.0, "n": n}
                continue
            under = float(np.mean(ratio[m] < UNDER_RATIO))
            over = float(np.mean(ratio[m] > OVER_RATIO))
            res["balance"][ph] = {"under": under, "over": over, "n": n}
            if under >= MILD and under >= over:
                findings.append(_under(ph, under))
            elif over >= MILD:
                findings.append(_over(ph, over))

    # ---- wheelspin and lock-ups ----
    slip = {k: _col(df, f"tyre_slip_{k}", 1.0) for k in ("fl", "fr", "rl", "rr")}
    moving = v * 3.6 > 30
    acc = moving & (thr > 0.6) & (brk < 0.05)
    if acc.sum() >= 20:
        rear = np.maximum(slip["rl"], slip["rr"])
        share = float(np.mean(rear[acc] > SPIN))
        if share >= MILD:
            findings.append(
                Finding("wheelspin", "exit", share, _sev(share),
                        f"The rear wheels spin on {share * 100:.0f}% of your full-throttle driving.",
                        ["LSD accel −5", "Traction control +1", "Rear ARB −2", "Rear spring rate −0.3"])
            )
    braking = moving & (brk > 0.3)
    if braking.sum() >= 20:
        front = np.minimum(slip["fl"], slip["fr"])
        share = float(np.mean(front[braking] < LOCK))
        if share >= MILD:
            findings.append(
                Finding("lockup", "entry", share, _sev(share),
                        f"The front wheels lock on {share * 100:.0f}% of your hard braking.",
                        ["Brake balance −1 (towards the rear)", "ABS +1", "Front brake-pad/toe: front toe 0.0"])
            )

    # ---- bottoming out ----
    if "ride_height_mm" in df.columns:
        rh = _col(df, "ride_height_mm", 999.0)
        m = moving & (rh < BOTTOM_MM)
        share = float(np.mean(m[moving])) if moving.any() else 0.0
        if share >= 0.03:
            findings.append(
                Finding("bottoming", "all", share, "strong" if share >= 0.10 else "mild",
                        f"The car touches the ground on {share * 100:.0f}% of the lap.",
                        ["Ride height +5 mm (front and rear)", "Spring rate +0.5", "Damper compression +2"])
            )

    findings.sort(key=lambda f: f.share, reverse=True)
    res["findings"] = findings
    return res


def _under(ph, share):
    fixes = {
        "entry": ["Front ARB −2", "Rear ARB +1", "Front camber +0.3", "LSD decel −3"],
        "mid": ["Front ARB −2", "Front camber +0.3", "Front toe −0.1 (more toe-out)", "Rear ARB +2"],
        "exit": ["LSD accel −5", "Rear ARB +1", "Front spring rate −0.3"],
    }[ph]
    where = {"entry": "turning in", "mid": "mid-corner", "exit": "on the power out of corners"}[ph]
    return Finding(f"under_{ph}", ph, share, _sev(share),
                   f"Understeer {where}: the car turns less than you steer on {share * 100:.0f}% of that phase.", fixes)


def _over(ph, share):
    fixes = {
        "entry": ["LSD decel −5", "Brake balance +1 (towards the front)", "Rear ARB −1", "Rear toe +0.1"],
        "mid": ["Rear ARB −2", "Rear camber −0.3", "Rear toe +0.1"],
        "exit": ["LSD accel −5", "Rear ARB −2", "Rear toe +0.1", "Traction control +1"],
    }[ph]
    where = {"entry": "turning in", "mid": "mid-corner", "exit": "on the power out of corners"}[ph]
    return Finding(f"over_{ph}", ph, share, _sev(share),
                   f"Oversteer {where}: the rear steps out on {share * 100:.0f}% of that phase.", fixes)


def describe(res: dict) -> str:
    if not res.get("ok"):
        return res.get("msg") or "No diagnosis."
    lines = []
    if res.get("msg"):
        lines.append(res["msg"])
    if not res["findings"]:
        lines.append("Setup looks balanced: nothing worth changing was found in this lap.")
    for f in res["findings"]:
        lines.append(f"[{f.severity}] {f.text}  Try: " + "; ".join(f.fixes[:3]) + ".")
    return "\n".join(lines)


# ---- A/B ------------------------------------------------------------------------------------------------------
def _score(res: dict) -> dict:
    return {f.key: f.share for f in res["findings"]}


def compare_ab(laps_a: list, laps_b: list, times_a: list | None = None, times_b: list | None = None) -> dict:
    """Did the change help? `laps_a` are frames before the change, `laps_b` after (about three laps each).

    Returns {"verdict", "better", "worse", "lap_delta_s", "text"}. Issues are averaged per lap, so one messy lap
    can't decide it; lap time only breaks a tie and is reported with a plain 'not enough laps' when it can't."""
    out = {"verdict": "unclear", "better": [], "worse": [], "lap_delta_s": None, "text": ""}
    ra = [diagnose(x) for x in laps_a if x is not None]
    rb = [diagnose(x) for x in laps_b if x is not None]
    ra = [r for r in ra if r["ok"]]
    rb = [r for r in rb if r["ok"]]
    if len(ra) < 2 or len(rb) < 2:
        out["text"] = "Need at least two good laps before and two after the change."
        return out
    keys = {k for r in ra + rb for k in _score(r)}
    for k in sorted(keys):
        a = float(np.mean([_score(r).get(k, 0.0) for r in ra]))
        b = float(np.mean([_score(r).get(k, 0.0) for r in rb]))
        if b < a - 0.03:
            out["better"].append((k, a, b))
        elif b > a + 0.03:
            out["worse"].append((k, a, b))
    ta = [t for t in (times_a or []) if t and t > 0]
    tb = [t for t in (times_b or []) if t and t > 0]
    if len(ta) >= 2 and len(tb) >= 2:
        out["lap_delta_s"] = float(np.median(tb) - np.median(ta))
    gain = len(out["better"]) - len(out["worse"])
    d = out["lap_delta_s"]
    if gain > 0 and (d is None or d <= 0.05):
        out["verdict"] = "better"
    elif gain < 0 and (d is None or d >= -0.05):
        out["verdict"] = "worse"
    elif d is not None and abs(d) >= 0.15:
        out["verdict"] = "better" if d < 0 else "worse"
    names = lambda xs: ", ".join(k.replace("_", " ") for k, _, _ in xs) or "none"  # noqa: E731
    lap = "" if d is None else f" Median lap time changed by {d:+.2f}s."
    out["text"] = (f"Change looks {out['verdict']}. Improved: {names(out['better'])}. "
                   f"Got worse: {names(out['worse'])}.{lap}")
    return out
