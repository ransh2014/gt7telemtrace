"""Theoretical best lap: stitch together the fastest micro-sector from each of
several laps on the same car and track -- the lap you could drive if you strung
your best bits together -- and show where your best real lap is held back.

Pure numpy/pandas, no tkinter. Each lap is cut into ~25 m micro-sectors by
*fraction of the lap* (so the small length differences between recorded laps
cancel out, the same way the Lap Analyst's micro-sector heatmap does it), and
each micro-sector's time is read off the lap's own position -> time curve. The
theoretical lap is the sum of the fastest time in every micro-sector.

It is an optimistic ceiling, not a promise: sectors interact (a faster exit
carries speed into the next one), so the real gain from fixing a weak zone is
usually smaller than the number shown.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MICRO_SECTOR_M = 25.0
ZONE_M = 100.0  # micro-sector losses are summed into zones this long for the summary
MAX_LENGTH_SPREAD = 0.05  # a lap this much longer/shorter than the median is another layout
MIN_LAPS = 2
LOSS_MIN_S = 0.001  # a zone has to cost at least this to be reported


def _curve(df: pd.DataFrame):
    """(position, time) arrays for a lap, both non-decreasing."""
    pos = np.maximum.accumulate(np.nan_to_num(df["track_position"].to_numpy(dtype=float)))
    t = np.maximum.accumulate(np.nan_to_num(df["t"].to_numpy(dtype=float)))
    return pos, t


def usable(df) -> bool:
    """A lap we can cut into sectors: has position and time columns and covers some distance."""
    if df is None or any(c not in df.columns for c in ("track_position", "t")) or len(df) < 20:
        return False
    pos, t = _curve(df)
    return bool(pos[-1] - pos[0] > 100 and t[-1] - t[0] > 1)


def sector_times(df: pd.DataFrame, n_sectors: int) -> np.ndarray:
    """Time spent in each of `n_sectors` equal-fraction sectors of the lap."""
    pos, t = _curve(df)
    edges = np.linspace(pos[0], pos[-1], n_sectors + 1)
    return np.diff(np.interp(edges, pos, t))


def theoretical_best(laps: list[tuple[str, pd.DataFrame]], sector_m: float = MICRO_SECTOR_M,
                     zone_m: float = ZONE_M) -> dict | None:
    """Analyse `laps` ((label, frame) pairs). Returns None when fewer than two
    usable laps of the same layout are given, otherwise a dict:

        labels        labels of the laps used
        totals        each lap's time (s)
        best_idx      index of the fastest real lap
        actual        its time; theoretical = stitched time; gap = actual - theoretical
        centers       micro-sector centre positions (m, along the best lap)
        donor         per micro-sector, index of the lap that holds the fastest time
        loss          per micro-sector, time the best real lap loses to the fastest (s)
        cum_gap       running total of `loss` along the lap (s)
        zones         zones of ~zone_m with their loss, worst first:
                      {"start", "end", "loss", "donor"} (donor = lap that gains the most there)
        skipped       laps left out (unusable, or a different length)
    """
    good = [(lab, df) for lab, df in laps if usable(df)]
    skipped = len(laps) - len(good)
    if len(good) >= 2:
        lengths = np.array([float(_curve(df)[0][-1] - _curve(df)[0][0]) for _, df in good])
        med = float(np.median(lengths))
        keep = np.abs(lengths - med) <= MAX_LENGTH_SPREAD * med
        skipped += int((~keep).sum())
        good = [g for g, k in zip(good, keep) if k]
        lengths = lengths[keep]
    if len(good) < MIN_LAPS:
        return None

    n = max(4, int(round(float(np.median(lengths)) / sector_m)))
    mat = np.vstack([sector_times(df, n) for _, df in good])  # laps x sectors
    totals = mat.sum(axis=1)
    best_idx = int(np.argmin(totals))
    fastest = mat.min(axis=0)
    donor = mat.argmin(axis=0)
    loss = mat[best_idx] - fastest
    length = float(lengths[best_idx])
    edges = np.linspace(0.0, length, n + 1)
    centers = (edges[:-1] + edges[1:]) / 2

    per_zone = max(1, int(round(zone_m / (length / n))))
    zones = []
    for a in range(0, n, per_zone):
        b = min(n, a + per_zone)
        z_loss = float(loss[a:b].sum())
        if z_loss < LOSS_MIN_S:
            continue
        # the lap that gains the most in this zone, not counting the best lap itself
        gains = mat[best_idx, a:b].sum() - mat[:, a:b].sum(axis=1)
        zones.append({"start": float(edges[a]), "end": float(edges[b]), "loss": z_loss,
                      "donor": int(np.argmax(gains))})
    zones.sort(key=lambda z: z["loss"], reverse=True)
    return {
        "labels": [lab for lab, _ in good],
        "totals": totals,
        "best_idx": best_idx,
        "actual": float(totals[best_idx]),
        "theoretical": float(fastest.sum()),
        "gap": float(totals[best_idx] - fastest.sum()),
        "centers": centers,
        "donor": donor,
        "loss": loss,
        "cum_gap": np.cumsum(loss),
        "zones": zones,
        "skipped": skipped,
    }
