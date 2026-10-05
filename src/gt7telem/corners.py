"""Per-corner analysis for the Lap Analyst "Corners" tab.

Pure pandas/numpy (no tkinter), so it is easy to test. A corner is found from
the lap itself (lateral G + braking), matched to the same corner on a
reference lap (your PB), and scored on three things:

* brake point  -- where braking starts (m of track position)
* min speed    -- slowest speed through the corner (km/h)
* throttle-on  -- where you get back to the throttle after the apex (m)

plus the time gained or lost across the corner. The per-session label is a
heuristic read on style, like the existing Style tab: it describes how the lap
was driven, not whether it was fast.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LAT_G_MIN = 0.5  # |lat_g| above this counts as "in a corner"
MERGE_GAP_M = 40.0  # corner runs closer than this are one corner
MIN_CORNER_M = 15.0  # ignore blips shorter than this
BRAKE_ON = 0.1
THROTTLE_ON = 0.5
BRAKE_LOOKBACK_M = 250.0  # how far before a corner we look for the brake point
MATCH_TOL_M = 80.0  # apex positions this close are "the same corner"
LABEL_BRAKE_M = 6.0  # mean brake-point shift (m) before we call it early/late
LABEL_THROTTLE_M = 5.0

_NEEDED = ("track_position", "speed_kmh", "throttle", "brake", "lat_g", "t")


@dataclass
class Corner:
    number: int
    start: float  # m, where |lat_g| first exceeds the threshold
    end: float  # m, where it drops back
    apex: float  # m, slowest point
    brake_pos: float | None  # m, None if no braking before this corner
    throttle_pos: float | None  # m, first throttle after the apex, None if never
    min_speed: float  # km/h
    brake_peak: float  # 0..1
    trail: bool  # still on the brakes at the apex


@dataclass
class CornerDelta:
    number: int  # corner number on the lap being analysed
    ref_number: int  # matching corner on the reference lap
    brake_m: float | None  # + = braked later than the reference
    min_speed_kmh: float  # + = carried more speed
    throttle_m: float | None  # - = back on the throttle earlier
    time_s: float | None  # + = lost time vs the reference


def _prepare(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or any(c not in df.columns for c in _NEEDED) or len(df) < 20:
        return None
    d = df.sort_values("track_position")
    d = d.drop_duplicates("track_position")
    if float(d["track_position"].max() - d["track_position"].min()) < 100:
        return None
    return d.reset_index(drop=True)


def detect_corners(df: pd.DataFrame) -> list[Corner]:
    """Find the corners of one lap, in track order."""
    d = _prepare(df)
    if d is None:
        return []
    pos = d["track_position"].to_numpy(dtype=float)
    spd = d["speed_kmh"].to_numpy(dtype=float)
    thr = d["throttle"].to_numpy(dtype=float)
    brk = d["brake"].to_numpy(dtype=float)
    inside = np.abs(d["lat_g"].to_numpy(dtype=float)) > LAT_G_MIN

    # contiguous runs of "in a corner"
    runs: list[list[int]] = []
    i, n = 0, len(d)
    while i < n:
        if inside[i]:
            j = i
            while j + 1 < n and inside[j + 1]:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1
    # merge runs that are really one corner (a flick, a kerb), drop blips
    merged: list[list[int]] = []
    for r in runs:
        if merged and pos[r[0]] - pos[merged[-1][1]] < MERGE_GAP_M:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    merged = [r for r in merged if pos[r[1]] - pos[r[0]] >= MIN_CORNER_M]

    corners: list[Corner] = []
    for k, (a, b) in enumerate(merged):
        apex_i = a + int(np.argmin(spd[a : b + 1]))
        # braking zone: from the previous corner's exit (or 250 m back) to the apex
        floor = pos[merged[k - 1][1]] if k else pos[0]
        lo_pos = max(floor, pos[a] - BRAKE_LOOKBACK_M)
        zone = np.where((pos >= lo_pos) & (np.arange(n) <= apex_i) & (brk > BRAKE_ON))[0]
        brake_pos = float(pos[zone[0]]) if len(zone) else None
        after = np.where((np.arange(n) > apex_i) & (thr > THROTTLE_ON))[0]
        throttle_pos = float(pos[after[0]]) if len(after) else None
        corners.append(
            Corner(
                number=k + 1,
                start=float(pos[a]),
                end=float(pos[b]),
                apex=float(pos[apex_i]),
                brake_pos=brake_pos,
                throttle_pos=throttle_pos,
                min_speed=float(spd[apex_i]),
                brake_peak=float(brk[zone].max()) if len(zone) else 0.0,
                trail=bool(brk[apex_i] > BRAKE_ON),
            )
        )
    return corners


def _time_between(d: pd.DataFrame, p0: float, p1: float) -> float:
    t = np.interp([p0, p1], d["track_position"], d["t"])
    return float(t[1] - t[0])


def match_corners(
    df: pd.DataFrame, ref: pd.DataFrame
) -> tuple[list[Corner], list[Corner], list[CornerDelta]]:
    """Detect corners on both laps and score each against its match on `ref`."""
    a, b = detect_corners(df), detect_corners(ref)
    da, db = _prepare(df), _prepare(ref)
    deltas: list[CornerDelta] = []
    if da is None or db is None:
        return a, b, deltas
    used: set[int] = set()
    for ca in a:
        best, best_gap = None, MATCH_TOL_M
        for cb in b:
            gap = abs(cb.apex - ca.apex)
            if cb.number not in used and gap <= best_gap:
                best, best_gap = cb, gap
        if best is None:
            continue
        used.add(best.number)
        # time across the reference corner's span, entry (brake or start) to exit
        entry = min(x for x in (best.brake_pos, best.start) if x is not None)
        exit_ = max(x for x in (best.throttle_pos, best.end) if x is not None)
        time_s = _time_between(da, entry, exit_) - _time_between(db, entry, exit_)
        deltas.append(
            CornerDelta(
                number=ca.number,
                ref_number=best.number,
                brake_m=(
                    ca.brake_pos - best.brake_pos
                    if ca.brake_pos is not None and best.brake_pos is not None
                    else None
                ),
                min_speed_kmh=ca.min_speed - best.min_speed,
                throttle_m=(
                    ca.throttle_pos - best.throttle_pos
                    if ca.throttle_pos is not None and best.throttle_pos is not None
                    else None
                ),
                time_s=time_s,
            )
        )
    return a, b, deltas


def _smoothness(df: pd.DataFrame) -> str:
    # same 0-100 index and cut-offs as the Style tab, so the two always agree
    try:
        agg = float(
            min(
                100.0,
                df["steering"].diff().abs().fillna(0).mean() * 220
                + df["throttle"].diff().abs().fillna(0).mean() * 140
                + df["brake"].diff().abs().fillna(0).mean() * 140,
            )
        )
    except KeyError:
        return ""
    return "Smooth" if agg < 33 else "Balanced" if agg < 66 else "Aggressive"


def session_label(df: pd.DataFrame, corners: list[Corner], deltas: list[CornerDelta]) -> str:
    """One line describing how the lap was driven, e.g. "Late braker, smooth"."""
    parts: list[str] = []
    brake = [x.brake_m for x in deltas if x.brake_m is not None]
    thr = [x.throttle_m for x in deltas if x.throttle_m is not None]
    if brake:  # against a reference lap
        m = float(np.mean(brake))
        parts.append("Late braker" if m >= LABEL_BRAKE_M else "Early braker" if m <= -LABEL_BRAKE_M else "PB braking points")
        if thr:
            t = float(np.mean(thr))
            if t <= -LABEL_THROTTLE_M:
                parts.append("early on throttle")
            elif t >= LABEL_THROTTLE_M:
                parts.append("late on throttle")
    elif corners:  # no reference: fall back to what the lap itself shows
        braked = [c for c in corners if c.brake_pos is not None]
        if braked:
            trail = sum(c.trail for c in braked) / len(braked)
            parts.append("Trail braker" if trail >= 0.5 else "Straight-line braker")
    smooth = _smoothness(df)
    if smooth:
        parts.append(smooth.lower())
    s = ", ".join(parts)
    return s[:1].upper() + s[1:]


# ── time-loss summary (any lap vs a reference) ───────────────────────────────
TIME_LOSS_MIN_S = 0.005  # a corner must cost at least this to be named


def time_loss_summary(deltas: list[CornerDelta], n: int = 3) -> list[CornerDelta]:
    """The `n` corners that cost the most time against the reference, worst
    first. Corners that were not slower (or lost under 5 ms) are left out, so a
    lap that matches its reference returns an empty list."""
    lost = [d for d in deltas if d.time_s is not None and d.time_s > TIME_LOSS_MIN_S]
    return sorted(lost, key=lambda d: d.time_s, reverse=True)[:n]


def describe_time_loss(deltas: list[CornerDelta], n: int = 3) -> str:
    """One line for the log / a caption, e.g. "Time lost: T4 +0.21s, T9 +0.12s, T2 +0.08s"."""
    worst = time_loss_summary(deltas, n)
    if not worst:
        return "No corner lost time against the reference"
    return "Time lost: " + ", ".join(f"T{d.number} +{d.time_s:.2f}s" for d in worst)


# ── every lap of a race against one baseline ─────────────────────────────────
@dataclass
class CornerSpread:
    ref_number: int  # corner number on the baseline lap
    n: int  # laps in which the corner was matched
    mean_time_s: float  # average time vs the baseline across those laps (+ = slower)
    std_time_s: float  # lap-to-lap spread of that time
    std_brake_m: float | None  # lap-to-lap spread of the brake point


def score_laps(
    laps: list[tuple[int, pd.DataFrame]], ref: pd.DataFrame
) -> tuple[list[Corner], list[tuple[int, dict[int, CornerDelta]]]]:
    """Score each (lap id, lap frame) against `ref`. Returns the reference
    lap's corners and, per lap, its deltas keyed by the *reference* corner
    number so every lap lines up in the same columns. Laps that cannot be read
    come back with no deltas rather than raising."""
    ref_corners = detect_corners(ref)
    out: list[tuple[int, dict[int, CornerDelta]]] = []
    for lap_id, df in laps:
        try:
            _, _, deltas = match_corners(df, ref)
        except Exception:
            deltas = []
        out.append((lap_id, {d.ref_number: d for d in deltas}))
    return ref_corners, out


def corner_spreads(scored: list[tuple[int, dict[int, CornerDelta]]], min_laps: int = 3) -> list[CornerSpread]:
    """Per-corner consistency over a set of scored laps, most consistent
    (smallest time spread) first. Corners matched on fewer than `min_laps`
    laps are skipped -- two laps say nothing about consistency."""
    by_corner: dict[int, list[CornerDelta]] = {}
    for _lap_id, deltas in scored:
        for ref_no, d in deltas.items():
            if d.time_s is not None:
                by_corner.setdefault(ref_no, []).append(d)
    out: list[CornerSpread] = []
    for ref_no, ds in by_corner.items():
        if len(ds) < min_laps:
            continue
        times = np.array([d.time_s for d in ds], dtype=float)
        brakes = np.array([d.brake_m for d in ds if d.brake_m is not None], dtype=float)
        out.append(
            CornerSpread(
                ref_number=ref_no,
                n=len(ds),
                mean_time_s=float(times.mean()),
                std_time_s=float(times.std(ddof=1)),
                std_brake_m=float(brakes.std(ddof=1)) if len(brakes) >= min_laps else None,
            )
        )
    return sorted(out, key=lambda c: c.std_time_s)
