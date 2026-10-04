"""
add_track.py -- add a missing track to course_ids.csv

Unlike add_car.py, this doesn't need a numeric ID from you: GT7's telemetry
stream doesn't report a track/course ID at all (confirmed -- it's just not
in the packet), so course_ids.csv only exists to back the TRACK dropdown's
name list, not a live lookup. If GT7 adds a new track that isn't in the
picker yet, just type its name here.

Usage (any install):  gt7telem-add-track
   or:              python -m gt7telem.add_track
"""
import csv
import os
from pathlib import Path

from . import tracks

SHIPPED_CSV = Path(__file__).parent / "course_ids.csv"

FIELDNAMES = [
    "ID", "Name", "Base", "Country", "Category", "Length", "LongestStraight",
    "ElevationDiff", "Altitude", "MinTimeH", "MinTimeM", "MinTimeS",
    "MaxTimeH", "MaxTimeM", "MaxTimeS", "LayoutNumber", "IsReverse",
    "PitLaneDelta", "IsOval", "NumCorners", "NoRain",
]


def _read(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_rows():
    """Shipped + local rows (local additions live in the per-user settings
    folder so they survive upgrades -- see tracks.local_csv_path())."""
    return _read(SHIPPED_CSV) + _read(tracks.local_csv_path())


def save_rows(rows):
    def sort_key(r):
        try:
            return int(r.get("ID", 0))
        except (TypeError, ValueError):
            return 0
    rows_sorted = sorted(rows, key=sort_key)
    path = tracks.local_csv_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows_sorted)
    os.replace(tmp, path)   # atomic: a crash can't leave a truncated list


def next_local_id(rows):
    """User-added tracks get negative IDs so they can never collide with a
    real gt7info course ID (those are always positive)."""
    local_ids = []
    for r in rows:
        try:
            v = int(r.get("ID", 0))
        except (TypeError, ValueError):
            continue
        if v < 0:
            local_ids.append(v)
    return (min(local_ids) - 1) if local_ids else -1


def main():
    print("=== Add a track to TRACE's track list ===")
    print(f"File: {tracks.local_csv_path()}\n")
    print("GT7's telemetry doesn't report a track ID, so there's nothing to")
    print("look up by number here -- this just adds a name to the TRACK")
    print("dropdown in the Live Dashboard.\n")

    rows = load_rows()
    existing_names = {(r.get("Name") or "").strip().lower() for r in rows}

    name = input("Track Name: ").strip()
    if not name:
        print("Track name can't be empty -- cancelled.")
        return

    if name.lower() in existing_names:
        print(f"\n\"{name}\" is already in the track list -- nothing to add.")
        return

    new_id = next_local_id(rows)
    row = {k: "" for k in FIELDNAMES}
    row["ID"] = str(new_id)
    row["Name"] = name
    local_rows = _read(tracks.local_csv_path()) + [row]
    try:
        save_rows(local_rows)
    except OSError as e:
        print(f"\nCouldn't write {tracks.local_csv_path()}: {e}")
        return

    print(f"\nSaved. \"{name}\" added (local ID {new_id}) -- it'll show up")
    print("in the TRACK dropdown next time you open the Dashboard.")
    print()
    print("It's saved in your own settings folder, so it survives TRACE upgrades.")
    print()
    print("To get the track into the official database for everyone, open an")
    print("issue with the in-game name:")
    print("  https://github.com/ransh2014/gt7telemtrace/issues")


if __name__ == "__main__":
    main()
