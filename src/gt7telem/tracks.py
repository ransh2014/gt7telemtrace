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
from pathlib import Path

__all__ = ["get_track_name", "all_track_names"]

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
