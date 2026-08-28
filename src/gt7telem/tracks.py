"""GT7 track/course ID -> name lookup.
Data sourced from the ddm999/gt7info community track database (course_ids.csv,
same folder). To refresh: download
https://raw.githubusercontent.com/ddm999/gt7info/web-new/_data/db/course.csv
and overwrite course_ids.csv.

Note -- unlike car_db.py, this is NOT a live autofill. GT7's telemetry stream
does not expose a track/course ID anywhere in the packet (confirmed against
gt7udp.py's own byte-offset parsing, plus every other community GT7 telemetry
tool we could find -- none of them get a track ID from the stream either).
So there's nothing to auto-detect from. What this backs instead is a
searchable track picker for the TRACK field: pick a real track name from the
dropdown instead of hand-typing it, so recordings land in consistent,
non-typo'd folders (no more "suzuka" vs "Suzuka Circuit" vs "suzuka_circuit"
splitting one track's laps across three folders).
"""
import csv
import json
from pathlib import Path

import numpy as np

from . import config as _runtime_config

__all__ = ["get_track_name", "all_track_names", "extract_boundary", "save_boundary", "load_boundary"]

_CSV_PATH = Path(__file__).parent / "course_ids.csv"
_track_names: dict[int, str] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    tid = int(row["ID"])
                except (KeyError, ValueError, TypeError):
                    continue
                _track_names[tid] = (row.get("Name") or "").strip()
    except FileNotFoundError:
        pass


def get_track_name(track_id: int | str) -> str:
    """Return the track's name for a given GT7 course ID, or "" if unknown."""
    _load()
    try:
        return _track_names.get(int(track_id), "")
    except (TypeError, ValueError):
        return ""


def all_track_names() -> list[str]:
    """Return every known track name, sorted, for populating the TRACK picker."""
    _load()
    return sorted(n for n in _track_names.values() if n)


# ── track boundary extraction ────────────────────────────────────────────────
# Estimates a track's left/right edge from a single clean lap's GPS trace
# (world_x/world_z + track_position), so Lap Analyst can render approximate
# track limits under the race-line map without any external track-map data.

_BOUNDARIES_PATH = _runtime_config._base_dir() / "track_boundaries.json"
_boundaries_cache: dict | None = None


def _load_boundaries_file() -> dict:
    global _boundaries_cache
    if _boundaries_cache is not None:
        return _boundaries_cache
    try:
        with open(_BOUNDARIES_PATH, encoding="utf-8") as f:
            _boundaries_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _boundaries_cache = {}
    return _boundaries_cache


def extract_boundary(lap_samples, bin_size_m: float = 25.0, smooth_window: int = 15) -> dict:
    """Estimate a track's left/right edge from one clean lap's GPS trace.

    lap_samples: list of per-frame telemetry dicts (needs world_x, world_z,
    track_position -- the same fields a saved lap/reference_lap.json's
    "samples" list already has). No PS5 needed to test: just pass in samples
    loaded from any saved lap file.

    Approach: smooth the raw world_x/world_z path with a moving average to
    approximate the centerline, then for every raw sample compute its signed
    lateral (perpendicular) offset from that centerline. Samples are bucketed
    by distance-along-track (track_position) into bin_size_m sectors; within
    each sector the min/max lateral offset gives the left/right edge point
    for that sector. This is a rough single-lap approximation -- a real lap
    doesn't touch both edges everywhere, especially on straights -- good
    enough to sanity-check a racing line against, not a surveyed boundary.
    Returns {"bins": [{distance, center_x, center_z, left_x, left_z,
    right_x, right_z}, ...]}, sorted by distance. Returns {"bins": []} if
    there isn't enough GPS data to work with (e.g. a compact ghost-lap
    download with no world_x/world_z).
    """
    pts = [s for s in lap_samples if s.get("world_x") or s.get("world_z")]
    if len(pts) < smooth_window * 2:
        return {"bins": []}

    xs = np.array([p.get("world_x", 0.0) for p in pts], dtype=float)
    zs = np.array([p.get("world_z", 0.0) for p in pts], dtype=float)
    tp = np.array([p.get("track_position", 0.0) for p in pts], dtype=float)

    k = max(3, smooth_window | 1)  # odd window, so padding is symmetric
    pad = k // 2
    kernel = np.ones(k) / k
    cx = np.convolve(np.pad(xs, (pad, pad), mode="edge"), kernel, mode="valid")[: len(xs)]
    cz = np.convolve(np.pad(zs, (pad, pad), mode="edge"), kernel, mode="valid")[: len(zs)]

    dx, dz = np.gradient(cx), np.gradient(cz)
    seg_len = np.hypot(dx, dz)
    seg_len[seg_len == 0] = 1.0
    perp_x, perp_z = dz / seg_len, -dx / seg_len  # unit perpendicular to heading

    lateral = (xs - cx) * perp_x + (zs - cz) * perp_z  # signed offset from centerline

    bins: dict[int, dict] = {}
    for i in range(len(pts)):
        b = int(tp[i] // bin_size_m)
        e = bins.setdefault(b, {
            "cx_sum": 0.0, "cz_sum": 0.0, "n": 0,
            "min_off": lateral[i], "min_i": i,
            "max_off": lateral[i], "max_i": i,
        })
        e["cx_sum"] += cx[i]; e["cz_sum"] += cz[i]; e["n"] += 1
        if lateral[i] < e["min_off"]:
            e["min_off"], e["min_i"] = lateral[i], i
        if lateral[i] > e["max_off"]:
            e["max_off"], e["max_i"] = lateral[i], i

    out_bins = []
    for b in sorted(bins):
        e = bins[b]
        li, ri = e["min_i"], e["max_i"]
        out_bins.append({
            "distance": round(b * bin_size_m, 1),
            "center_x": round(e["cx_sum"] / e["n"], 3),
            "center_z": round(e["cz_sum"] / e["n"], 3),
            "left_x": round(float(xs[li]), 3), "left_z": round(float(zs[li]), 3),
            "right_x": round(float(xs[ri]), 3), "right_z": round(float(zs[ri]), 3),
        })
    return {"bins": out_bins}


def save_boundary(track_id, boundary: dict) -> None:
    """Cache a track's boundary (from extract_boundary) to
    track_boundaries.json, keyed by track_id (the lap's "track" name string)."""
    data = _load_boundaries_file()
    data[str(track_id)] = boundary
    _BOUNDARIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_BOUNDARIES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)
    global _boundaries_cache
    _boundaries_cache = data


def load_boundary(track_id):
    """Return the cached boundary dict for track_id, or None if none saved yet."""
    return _load_boundaries_file().get(str(track_id))
